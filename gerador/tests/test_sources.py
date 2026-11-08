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
                         mock.patch.object(config, "OSM_SOURCES", ("openstreetmap.fr",)),  # sem a reserva
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


GEOFABRIK_STATE = b"# original OSM minutely replication sequence number 7362973\n" \
                  b"timestamp=2026-11-07T21\\:21\\:36Z\nsequenceNumber=4967\n"


def serve_geofabrik(s: FakeServer, content: bytes = A, day: str = "261107") -> None:
    """Imita a Geofabrik: `-latest` redireciona para o arquivo datado do dia, que tem o próprio .md5."""
    s.redirect["/gf/brazil-latest.osm.pbf"] = f"/gf/brazil-{day}.osm.pbf"
    s.files[f"/gf/brazil-{day}.osm.pbf"] = content
    s.files[f"/gf/brazil-{day}.osm.pbf.md5"] = f"{md5(A)}  brazil-{day}.osm.pbf\n".encode()
    s.files["/gf/state.txt"] = GEOFABRIK_STATE


class SourcesTestCase(unittest.TestCase):
    """Um servidor local com as duas fontes: o openstreetmap.fr em /fr/ e a Geofabrik em /gf/."""
    order = ("openstreetmap.fr", "geofabrik")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self._tmp.name)
        self.pbf = self.raw / "brazil-latest.osm.pbf"
        self.server = FakeServer().__enter__()
        s = self.server
        s.files["/fr/state.txt"] = b"sequenceNumber=1\ntimestamp=2026-11-07T00\\:33\\:49Z\n"
        s.files["/fr/x.md5"] = f"{md5(B)}  brazil.osm.pbf\n".encode()
        s.files["/fr/x.pbf"] = B
        serve_geofabrik(s)
        self._patches = [mock.patch.object(config, "RAW", self.raw),
                         mock.patch.object(config, "OSM_STATE_URL", s.url("/fr/state.txt")),
                         mock.patch.object(config, "OSM_MD5_URL", s.url("/fr/x.md5")),
                         mock.patch.object(config, "OSM_PBF_URL", s.url("/fr/x.pbf")),
                         mock.patch.object(config, "GEOFABRIK_LATEST_URL", s.url("/gf/brazil-latest.osm.pbf")),
                         mock.patch.object(config, "GEOFABRIK_STATE_URL", s.url("/gf/state.txt")),
                         mock.patch.object(config, "OSM_SOURCES", self.order),
                         mock.patch.object(sources, "RETRY_DELAY", 0)]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self.server.__exit__(None, None, None)
        self._tmp.cleanup()

    def requested(self, prefix: str) -> int:
        return sum(1 for path, _ in self.server.requests if path.startswith(prefix))


class GeofabrikTest(SourcesTestCase):
    """sources.osm_extract só com a Geofabrik: o arquivo datado do dia, que não muda depois de publicado."""
    order = ("geofabrik",)

    def test_downloads_the_dated_file(self):
        meta = sources.osm_extract()
        self.assertEqual(A, self.pbf.read_bytes())
        self.assertEqual(md5(A), meta["md5"])
        self.assertEqual("2026-11-07T21:21:36Z", meta["timestamp"])
        self.assertEqual(self.server.url("/gf/brazil-261107.osm.pbf"), meta["url"])
        self.assertEqual(0, self.requested("/fr/"))

    def test_does_not_download_again_when_up_to_date(self):
        sources.osm_extract()
        sources.osm_extract()
        self.assertEqual(1, sum(1 for path, _ in self.server.requests if path == "/gf/brazil-261107.osm.pbf"))

    def test_corrupted_file_fails_and_is_removed(self):
        serve_geofabrik(self.server, content=A[:-10] + b"corrompido")
        with self.assertRaises(RuntimeError) as ctx:
            sources.osm_extract()
        self.assertIn("md5", str(ctx.exception))
        self.assertFalse(self.pbf.exists())

    def test_state_of_another_day_is_read_again(self):
        """A troca diária pode cair entre ler o `-latest` e o state.txt: lê os dois de novo uma vez."""
        states = [GEOFABRIK_STATE.replace(b"2026-11-07T21", b"2026-11-08T21"), GEOFABRIK_STATE]
        self.server.files["/gf/state.txt"] = lambda: states.pop(0) if len(states) > 1 else states[0]
        meta = sources.osm_extract()
        self.assertEqual("2026-11-07T21:21:36Z", meta["timestamp"])
        self.assertEqual(A, self.pbf.read_bytes())

    def test_state_that_never_matches_the_file_fails(self):
        self.server.files["/gf/state.txt"] = GEOFABRIK_STATE.replace(b"2026-11-07T21", b"2026-11-09T21")
        with self.assertRaises(RuntimeError) as ctx:
            sources.osm_extract()
        self.assertIn("261107", str(ctx.exception))
        self.assertFalse(self.pbf.exists())

    def test_unexpected_file_name_fails(self):
        self.server.redirect["/gf/brazil-latest.osm.pbf"] = "/gf/outra-coisa.osm.pbf"
        with self.assertRaises(RuntimeError):
            sources.osm_extract()


class FallbackTest(SourcesTestCase):
    """O openstreetmap.fr primeiro; a Geofabrik só se ele não entregar um extrato conferido."""

    def test_first_source_answering_is_used(self):
        meta = sources.osm_extract()
        self.assertEqual(self.server.url("/fr/x.pbf"), meta["url"])
        self.assertEqual(B, self.pbf.read_bytes())
        self.assertEqual(0, self.requested("/gf/"))

    def test_first_source_down_uses_the_next(self):
        self.server.code["/fr/state.txt"] = 503
        meta = sources.osm_extract()
        self.assertEqual(self.server.url("/gf/brazil-261107.osm.pbf"), meta["url"])
        self.assertEqual(A, self.pbf.read_bytes())

    def test_first_source_with_bad_md5_uses_the_next(self):
        self.server.files["/fr/x.pbf"] = B[:-10] + b"corrompido"
        meta = sources.osm_extract()
        self.assertEqual(md5(A), meta["md5"])
        self.assertEqual(A, self.pbf.read_bytes())

    def test_all_sources_down_fails_naming_each_one(self):
        self.server.code["/fr/state.txt"] = 503
        self.server.code["/gf/brazil-latest.osm.pbf"] = 404
        with self.assertRaises(RuntimeError) as ctx:
            sources.osm_extract()
        self.assertIn("openstreetmap.fr", str(ctx.exception))
        self.assertIn("geofabrik", str(ctx.exception))

    def test_unknown_source_fails_before_any_request(self):
        with mock.patch.object(config, "OSM_SOURCES", ("openstreetmap.fr", "planeta")):
            with self.assertRaises(ValueError) as ctx:
                sources.osm_extract()
        self.assertIn("planeta", str(ctx.exception))
        self.assertEqual([], self.server.requests)


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
