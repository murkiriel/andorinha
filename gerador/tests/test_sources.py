import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from andorinha import sources, config, net, publishing  # noqa: E402
from andorinha.util import file_hash  # noqa: E402
from tests import fake_pbf  # noqa: E402
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


GF_TIME = "2026-11-07T21:21:36Z"
GF = fake_pbf.pbf(GF_TIME)  # o arquivo datado do dia, com o cabeçalho e o horário dos dados
GF_OLD_TIME = "2026-11-06T21:20:10Z"
GF_OLD = fake_pbf.pbf(GF_OLD_TIME, size=70_000)


def listing(days) -> bytes:
    """A listagem da pasta da Geofabrik: cada arquivo datado com os .md5 e .torrent ao lado, como no site."""
    rows = "".join(f'<tr><td><a href="brazil-{d}.osm.pbf">brazil-{d}.osm.pbf</a></td>'
                   f'<td><a href="brazil-{d}.osm.pbf.md5">[MD5]</a></td></tr>' for d in days)
    return (f'<html><body><a href="brazil-latest.osm.pbf">brazil-latest.osm.pbf</a>{rows}'
            '<a href="brazil-internal.osm.pbf">brazil-internal.osm.pbf</a></body></html>').encode()


def serve_geofabrik(s: FakeServer, days=None, latest: str = "261107", listed=None) -> None:
    """Imita a Geofabrik de hoje: o `-latest` dá dois saltos (301 e 307) até o datado, que tem o próprio .md5.
    `days`: {AAMMDD: conteúdo} dos arquivos que respondem; `listed`: os dias que a listagem mostra (padrão: `days`)."""
    days = {"261107": GF} if days is None else days
    for table in (s.files, s.code, s.redirect, s.redirect_status):  # começa do zero: o setUp já serviu o padrão
        for path in [p for p in table if p.startswith("/gf/")]:
            del table[path]
    s.redirect["/gf/brazil-latest.osm.pbf"] = "/gf/brazil/brazil-latest.osm.pbf"
    s.redirect_status["/gf/brazil-latest.osm.pbf"] = 301
    s.redirect["/gf/brazil/brazil-latest.osm.pbf"] = f"/gf/brazil/brazil-{latest}.osm.pbf"
    for day, content in days.items():
        s.files[f"/gf/brazil/brazil-{day}.osm.pbf"] = content
        s.files[f"/gf/brazil/brazil-{day}.osm.pbf.md5"] = f"{md5(content)}  brazil-{day}.osm.pbf\n".encode()
    s.files["/gf/brazil/"] = listing(sorted(days) if listed is None else listed)


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
                         mock.patch.object(config, "GEOFABRIK_LISTING_URL", s.url("/gf/brazil/")),
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

    def hits(self, path: str) -> int:
        """Quantos pedidos chegaram exatamente a este caminho."""
        return sum(1 for p, _ in self.server.requests if p == path)

    def extract(self):
        """sources.osm_extract() e o que ele escreveu no log."""
        out = io.StringIO()
        with redirect_stdout(out):
            meta = sources.osm_extract()
        self.log = out.getvalue()
        return meta

    def dated(self, day: str) -> str:
        return self.server.url(f"/gf/brazil/brazil-{day}.osm.pbf")


class GeofabrikTest(SourcesTestCase):
    """sources.osm_extract só com a Geofabrik: o arquivo datado que responde, o mais novo primeiro."""
    order = ("geofabrik",)

    def test_downloads_the_dated_file_after_two_redirects(self):
        meta = self.extract()
        self.assertEqual(GF, self.pbf.read_bytes())
        self.assertEqual(md5(GF), meta["md5"])
        self.assertEqual(self.dated("261107"), meta["url"])
        self.assertEqual(0, self.requested("/fr/"))

    def test_timestamp_is_the_one_in_the_file_header_not_the_state(self):
        meta = self.extract()
        self.assertEqual(GF_TIME, meta["timestamp"])
        self.assertNotIn("AVISO", self.log)

    def test_stored_names_in_data_raw_do_not_change(self):
        """O cache e o "já tenho" contam com estes nomes, qualquer que seja o arquivo datado."""
        self.extract()
        self.assertEqual(["brazil-latest.meta.json", "brazil-latest.osm.pbf"], sorted(p.name for p in self.raw.iterdir()))
        meta = json.loads((self.raw / "brazil-latest.meta.json").read_text())
        self.assertEqual({"path", "url", "timestamp", "md5", "size"}, set(meta))
        self.assertEqual(str(self.pbf), meta["path"])

    def test_does_not_download_again_when_up_to_date(self):
        sources.osm_extract()
        meta = sources.osm_extract()
        self.assertEqual(1, self.hits("/gf/brazil/brazil-261107.osm.pbf"))
        self.assertEqual(GF_TIME, meta["timestamp"])

    def test_corrupted_file_fails_and_is_removed(self):
        self.server.files["/gf/brazil/brazil-261107.osm.pbf"] = GF[:-10] + b"corrompido"
        with self.assertRaises(RuntimeError) as ctx:
            sources.osm_extract()
        self.assertIn("md5", str(ctx.exception))
        self.assertFalse(self.pbf.exists())

    def test_day_that_does_not_answer_uses_yesterdays_without_waiting(self):
        """22/11/2026: o datado do dia está na listagem e o `-latest` aponta para ele, mas o .md5 dá 502."""
        serve_geofabrik(self.server, days={"261106": GF_OLD}, latest="261107", listed=["261106", "261107"])
        self.server.code["/gf/brazil/brazil-261107.osm.pbf.md5"] = 502
        meta = self.extract()
        self.assertEqual(self.dated("261106"), meta["url"])
        self.assertEqual(GF_OLD, self.pbf.read_bytes())
        self.assertEqual(GF_OLD_TIME, meta["timestamp"])
        self.assertEqual(1, self.hits("/gf/brazil/brazil-261107.osm.pbf.md5"))  # uma tentativa, sem retentar
        self.assertEqual(0, self.hits("/gf/brazil/brazil-261107.osm.pbf"))  # o arquivo do dia nem foi pedido
        self.assertIn("brazil-261107.osm.pbf", self.log)
        self.assertIn("pulados", self.log)

    def test_day_with_md5_but_without_the_file_goes_to_the_previous(self):
        serve_geofabrik(self.server, days={"261107": GF, "261106": GF_OLD}, latest="261107")
        del self.server.files["/gf/brazil/brazil-261107.osm.pbf"]  # o .md5 saiu e o arquivo ainda não
        meta = self.extract()
        self.assertEqual(self.dated("261106"), meta["url"])
        self.assertEqual(GF_OLD, self.pbf.read_bytes())

    def test_third_candidate_answers(self):
        """A listagem tem 4 datados, o `-latest` aponta o mais novo: tenta 3 (261109, 261108, 261107) e para no 3º."""
        serve_geofabrik(self.server, days={"261107": GF}, latest="261109", listed=["261106", "261107", "261108", "261109"])
        meta = self.extract()
        self.assertEqual(self.dated("261107"), meta["url"])
        self.assertEqual(1, self.hits("/gf/brazil/brazil-261109.osm.pbf.md5"))
        self.assertEqual(1, self.hits("/gf/brazil/brazil-261108.osm.pbf.md5"))
        self.assertEqual(0, self.requested("/gf/brazil/brazil-261106"))  # o 4º da listagem nem entra

    def test_all_candidates_fail_naming_each_one(self):
        serve_geofabrik(self.server, days={}, latest="261109", listed=["261107", "261108", "261109"])
        with self.assertRaises(RuntimeError) as ctx:
            sources.osm_extract()
        message = str(ctx.exception)
        for day in ("261107", "261108", "261109"):
            self.assertIn(f"brazil-{day}.osm.pbf", message)
        self.assertFalse(self.pbf.exists())

    def test_candidates_are_in_order_without_repeats(self):
        serve_geofabrik(self.server, days={}, latest="261108", listed=["261105", "261106", "261107", "261108", "261109"])
        with mock.patch.object(config, "GEOFABRIK_LISTED_DATES", 3):
            names = [u.rsplit("/", 1)[-1] for u in sources._geofabrik_candidates()]
        # o que o -latest aponta primeiro; depois os 3 mais novos da listagem, sem repetir o 261108
        self.assertEqual(["brazil-261108.osm.pbf", "brazil-261109.osm.pbf", "brazil-261107.osm.pbf"], names)

    def test_listing_does_not_count_md5_torrent_or_latest(self):
        html = listing(["261107"]).decode() + \
            '<a href="brazil-261108.osm.pbf.md5">x</a><a href="brazil-261109.osm.pbf.torrent">y</a>'
        self.assertEqual({"261107"}, set(sources.LISTED.findall(html)))

    def test_latest_that_does_not_answer_falls_back_to_the_listing(self):
        self.server.code["/gf/brazil-latest.osm.pbf"] = 404
        meta = self.extract()
        self.assertEqual(self.dated("261107"), meta["url"])
        self.assertIn("AVISO", self.log)

    def test_latest_pointing_somewhere_else_falls_back_to_the_listing(self):
        self.server.redirect["/gf/brazil/brazil-latest.osm.pbf"] = "/gf/brazil/outra-coisa.osm.pbf"
        self.server.files["/gf/brazil/outra-coisa.osm.pbf"] = GF
        meta = self.extract()
        self.assertEqual(self.dated("261107"), meta["url"])
        self.assertIn("inesperado", self.log)

    def test_neither_latest_nor_listing_fails(self):
        self.server.code["/gf/brazil-latest.osm.pbf"] = 404
        self.server.code["/gf/brazil/"] = 404
        with self.assertRaises(RuntimeError) as ctx:
            sources.osm_extract()
        self.assertIn("nenhum arquivo datado", str(ctx.exception))

    def test_md5_answer_that_is_not_an_md5_is_skipped(self):
        serve_geofabrik(self.server, days={"261106": GF_OLD}, latest="261107", listed=["261106", "261107"])
        self.server.files["/gf/brazil/brazil-261107.osm.pbf.md5"] = b"<html>Servico indisponivel</html>"
        meta = self.extract()
        self.assertEqual(self.dated("261106"), meta["url"])

    def test_header_that_cannot_be_read_uses_midnight_of_the_file_day_with_a_warning(self):
        serve_geofabrik(self.server, days={"261107": A})  # um "arquivo" que não começa com o cabeçalho de um PBF
        meta = self.extract()
        self.assertEqual("2026-11-07T00:00:00Z", meta["timestamp"])
        self.assertIn("AVISO", self.log)
        self.assertIn("meia-noite", self.log)

    def test_timestamp_decides_the_older_generation_check(self):
        """A trava de extrato mais velho que o publicado compara o horário do arquivo, não o de quem olhou o state."""
        meta = self.extract()
        published = {"build_id": "x", "osm": {"timestamp": "2026-11-07T21:00:00Z"}}
        updated = {"build_id": "y", "osm": {"timestamp": meta["timestamp"]}}
        self.assertEqual([], publishing.older_generation(published, updated))
        older = {"build_id": "y", "osm": {"timestamp": "2026-11-07T21:00:00Z"}}
        published["osm"]["timestamp"] = meta["timestamp"]
        self.assertEqual(1, len(publishing.older_generation(published, older)))


class OldExtractNoteTest(unittest.TestCase):
    """Extrato mais velho que 36 h no momento da geração: só uma nota. Em 2026-11-22 a geração usou o de 21/11 sem aviso."""

    NOW = datetime(2026, 11, 22, 15, 30, tzinfo=timezone.utc)

    def test_extract_of_the_day_has_no_note(self):
        self.assertIsNone(sources.old_extract_note("2026-11-22T00:54:00Z", self.NOW))

    def test_extract_of_yesterday_has_no_note(self):
        self.assertIsNone(sources.old_extract_note("2026-11-21T09:00:00Z", self.NOW))  # 30 h

    def test_exactly_36_hours_has_no_note(self):
        self.assertIsNone(sources.old_extract_note("2026-11-21T03:30:00Z", self.NOW))

    def test_older_than_36_hours_has_a_note_with_the_day_and_the_hours(self):
        note = sources.old_extract_note("2026-11-21T00:49:19Z", self.NOW)  # 38 h
        self.assertEqual("OSM de 2026-11-21, 38 h mais velho que o momento da geração", note)

    def test_unreadable_timestamp_has_no_note(self):
        self.assertIsNone(sources.old_extract_note("ontem", self.NOW))

    def test_limit_comes_from_the_config(self):
        self.assertEqual(36, config.OSM_MAX_AGE_HOURS)


class FallbackTest(SourcesTestCase):
    """O openstreetmap.fr primeiro; a Geofabrik só se ele não entregar um extrato conferido."""

    def test_first_source_answering_is_used(self):
        meta = sources.osm_extract()
        self.assertEqual(self.server.url("/fr/x.pbf"), meta["url"])
        self.assertEqual(B, self.pbf.read_bytes())
        self.assertEqual(0, self.requested("/gf/"))

    def test_first_source_down_uses_the_next(self):
        self.server.code["/fr/state.txt"] = 503
        meta = self.extract()
        self.assertEqual(self.dated("261107"), meta["url"])
        self.assertEqual(GF, self.pbf.read_bytes())
        self.assertEqual(GF_TIME, meta["timestamp"])
        self.assertIn("veio da reserva", self.log)

    def test_first_source_down_and_the_days_file_missing_uses_yesterdays_from_the_reserve(self):
        serve_geofabrik(self.server, days={"261106": GF_OLD}, latest="261107", listed=["261106", "261107"])
        self.server.code["/fr/state.txt"] = 502
        meta = self.extract()
        self.assertEqual(self.dated("261106"), meta["url"])
        self.assertEqual(GF_OLD, self.pbf.read_bytes())

    def test_first_source_with_bad_md5_uses_the_next(self):
        self.server.files["/fr/x.pbf"] = B[:-10] + b"corrompido"
        meta = self.extract()
        self.assertEqual(md5(GF), meta["md5"])
        self.assertEqual(GF, self.pbf.read_bytes())

    def test_all_sources_down_fails_naming_each_one(self):
        self.server.code["/fr/state.txt"] = 503
        self.server.code["/gf/brazil-latest.osm.pbf"] = 404
        self.server.code["/gf/brazil/"] = 404
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
