import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from andorinha import sources, config, net  # noqa: E402
from andorinha.util import file_hash  # noqa: E402
from tests.fake_server import FakeServer  # noqa: E402

A = b"extrato versao A " * 5000
B = b"extrato versao B, mais nova " * 5000


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


class ExtractTest(unittest.TestCase):
    """sources.osm_extract contra um servidor local que imita o openstreetmap.fr (state.txt, .md5 e o .pbf)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self._tmp.name)
        self.current = [A]  # o arquivo que o "servidor" publica agora
        self.server = FakeServer().__enter__()
        s = self.server
        s.files["/state.txt"] = lambda: b"sequenceNumber=1\ntimestamp=2026-09-29T00\\:59\\:51Z\n"
        s.files["/x.md5"] = lambda: f"{md5(self.current[0])}  brazil.osm.pbf\n".encode()
        s.files["/x.pbf"] = lambda: self.current[0]
        self._patches = [mock.patch.object(config, "RAW", self.raw),
                         mock.patch.object(config, "OSM_STATE_URL", s.url("/state.txt")),
                         mock.patch.object(config, "OSM_MD5_URL", s.url("/x.md5")),
                         mock.patch.object(config, "OSM_PBF_URL", s.url("/x.pbf")),
                         mock.patch.object(sources, "RETRY_DELAY", 0)]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.server.__exit__(None, None, None)
        self._tmp.cleanup()

    def downloads(self):
        return sum(1 for path, _ in self.server.requests if path == "/x.pbf")

    def test_downloads_and_saves_meta_with_size(self):
        meta = sources.osm_extract()
        self.assertEqual(A, (self.raw / "brazil-latest.osm.pbf").read_bytes())
        self.assertEqual(md5(A), meta["md5"])
        self.assertEqual("2026-09-29T00:59:51Z", meta["timestamp"])
        self.assertEqual(len(A), json.loads((self.raw / "brazil-latest.meta.json").read_text())["size"])

    def test_does_not_download_again_when_up_to_date(self):
        sources.osm_extract()
        sources.osm_extract()
        self.assertEqual(1, self.downloads())

    def test_extract_updated_during_download(self):
        # O md5 lido antes do download é o de A, mas o servidor troca para B antes de mandar o arquivo.
        def switch_and_serve():
            self.current[0] = B
            return B
        self.server.files["/x.pbf"] = switch_and_serve
        meta = sources.osm_extract()
        self.assertEqual(md5(B), meta["md5"])
        self.assertEqual(B, (self.raw / "brazil-latest.osm.pbf").read_bytes())

    def test_corrupted_file_fails_and_is_removed(self):
        self.server.files["/x.pbf"] = lambda: A[:-10] + b"corrompido"
        with self.assertRaises(RuntimeError):
            sources.osm_extract()
        self.assertFalse((self.raw / "brazil-latest.osm.pbf").exists())

    def test_truncated_local_extract_is_downloaded_again(self):
        sources.osm_extract()
        pbf = self.raw / "brazil-latest.osm.pbf"
        pbf.write_bytes(A[:1000])  # meta diz que está tudo certo, mas o arquivo encolheu
        sources.osm_extract()
        self.assertEqual(A, pbf.read_bytes())
        self.assertEqual(2, self.downloads())


def fake_mesh(exclude=()) -> bytes:
    """GeoJSON no formato da API de malhas do IBGE: uma feição por UF, código em `codarea`."""
    features = [{"type": "Feature", "properties": {"codarea": code},
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}
                for code in config.IBGE_STATE_CODES if code not in exclude]
    return json.dumps({"type": "FeatureCollection", "features": features}).encode()


class MeshTest(unittest.TestCase):
    """sources.ibge_mesh: a malha é a cópia versionada, conferida (27 UFs e sha256) e sem nenhum acesso à rede.

    Até 2026-11-08 a malha vinha da API do IBGE; de 2026-11-03 em diante a API não respondeu aos executores do
    GitHub Actions e todas as gerações agendadas falharam."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.copy = Path(self._tmp.name) / "ibge_malha_uf.json"
        self._patches = [mock.patch.object(config, "IBGE_MESH_COPY", self.copy),
                         mock.patch.object(net, "download", side_effect=AssertionError("baixou a malha")),
                         mock.patch.object(net, "fetch_text", side_effect=AssertionError("acessou a rede"))]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def write_copy(self, content: bytes, sha256: str = "") -> None:
        self.copy.write_bytes(content)
        patch = mock.patch.object(config, "IBGE_MESH_COPY_SHA256", sha256 or hashlib.sha256(content).hexdigest())
        patch.start()
        self._patches.append(patch)

    def test_good_copy_is_used_without_network(self):
        self.write_copy(fake_mesh())
        self.assertEqual(self.copy, sources.ibge_mesh())

    def test_copy_missing_a_state_stops(self):
        self.write_copy(fake_mesh(exclude=("53",)))  # sem o DF
        with self.assertRaises(RuntimeError) as ctx:
            sources.ibge_mesh()
        self.assertIn("DF", str(ctx.exception))

    def test_copy_that_is_not_json_stops(self):
        self.write_copy(b"<html><body>Servico indisponivel</body></html>")
        with self.assertRaises(RuntimeError) as ctx:
            sources.ibge_mesh()
        self.assertIn("JSON", str(ctx.exception))

    def test_missing_copy_stops(self):
        with self.assertRaises(RuntimeError) as ctx:
            sources.ibge_mesh()
        self.assertIn(self.copy.name, str(ctx.exception))

    def test_copy_changed_without_new_sha256_stops(self):
        """Uma malha diferente muda a divisão dos pacotes: só entra com o sha256 novo no config, de propósito."""
        self.write_copy(fake_mesh(), sha256="0" * 64)
        with self.assertRaises(RuntimeError) as ctx:
            sources.ibge_mesh()
        self.assertIn("sha256", str(ctx.exception))


class VersionedMeshTest(unittest.TestCase):
    """A cópia da malha que vai no repositório (config.IBGE_MESH_COPY)."""

    def test_has_the_27_states(self):
        self.assertIsNone(sources.mesh_problem(config.IBGE_MESH_COPY))

    def test_is_the_reviewed_file(self):
        """Trocar a cópia é decisão consciente: baixar da URL do config, conferir e atualizar o sha256."""
        self.assertEqual(config.IBGE_MESH_COPY_SHA256, file_hash(config.IBGE_MESH_COPY, "sha256"))

    def test_generation_uses_the_real_copy(self):
        self.assertEqual(config.IBGE_MESH_COPY, sources.ibge_mesh())


if __name__ == "__main__":
    unittest.main()
