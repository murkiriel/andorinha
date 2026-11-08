import hashlib
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from andorinha import net  # noqa: E402
from tests.fake_server import FakeServer  # noqa: E402

SAMPLE_DATA = bytes(range(256)) * 4000  # ~1 MB


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self._tmp.name) / "arquivo.bin"

    def tearDown(self):
        self._tmp.cleanup()

    def test_resumes_after_drop(self):
        with FakeServer() as s:
            s.files["/a"] = SAMPLE_DATA
            s.cut_after["/a"] = [400_000]
            net.download(s.url("/a"), self.dest, delay=0)
            self.assertEqual(SAMPLE_DATA, self.dest.read_bytes())
            self.assertEqual([("/a", None), ("/a", "bytes=400000-")], s.requests)
            self.assertFalse(self.dest.with_suffix(".bin.parcial").exists())

    def test_drop_never_leaves_incomplete_file(self):
        with FakeServer() as s:
            s.files["/a"] = SAMPLE_DATA
            s.cut_after["/a"] = [1000, 1000, 1000]
            with self.assertRaises(net.DownloadError):
                net.download(s.url("/a"), self.dest, attempts=3, delay=0)
            self.assertFalse(self.dest.exists())

    def test_server_without_range_restarts_from_zero(self):
        with FakeServer() as s:
            s.files["/a"] = SAMPLE_DATA
            s.cut_after["/a"] = [400_000]
            s.no_range.add("/a")
            net.download(s.url("/a"), self.dest, delay=0)
            self.assertEqual(SAMPLE_DATA, self.dest.read_bytes())

    def test_gzip_response_is_decompressed(self):
        # O IBGE manda o corpo comprimido para o urllib no Actions mesmo sem o cliente pedir: o arquivo gravado tem de
        # ser o conteúdo, não os bytes comprimidos.
        with FakeServer() as s:
            s.files["/a"] = SAMPLE_DATA
            s.gzip_always.add("/a")
            net.download(s.url("/a"), self.dest, delay=0)
            self.assertEqual(SAMPLE_DATA, self.dest.read_bytes())

    def test_gzip_after_partial_restarts_from_zero(self):
        # Um .parcial da mesma versão não se emenda com um corpo comprimido inteiro: recomeça do zero.
        with FakeServer() as s:
            s.files["/a"] = SAMPLE_DATA
            s.gzip_always.add("/a")
            partial = self.dest.with_name(self.dest.name + ".parcial")
            partial.write_bytes(SAMPLE_DATA[:1000])
            net.mark_origin(partial, "v1")
            net.download(s.url("/a"), self.dest, identity="v1", delay=0)
            self.assertEqual(SAMPLE_DATA, self.dest.read_bytes())

    def test_404_fails_immediately(self):
        with FakeServer() as s:
            t0 = time.time()
            with self.assertRaises(net.PermanentError):
                net.download(s.url("/nao-existe"), self.dest, delay=5)
            self.assertLess(time.time() - t0, 2)
            self.assertEqual(1, len(s.requests))

    def test_503_is_retried(self):
        with FakeServer() as s:
            s.code["/a"] = 503
            with self.assertRaises(net.DownloadError):
                net.download(s.url("/a"), self.dest, attempts=3, delay=0)
            self.assertEqual(3, len(s.requests))

    def test_partial_of_other_version_is_discarded(self):
        with FakeServer() as s:
            s.files["/a"] = SAMPLE_DATA
            partial = self.dest.with_suffix(".bin.parcial")
            partial.write_bytes(b"x" * 1000)  # sobra de um download de outra versão do arquivo
            net.mark_origin(partial, "versao-velha")
            net.download(s.url("/a"), self.dest, identity="versao-nova", delay=0)
            self.assertEqual(SAMPLE_DATA, self.dest.read_bytes())
            self.assertEqual([("/a", None)], s.requests)

    def test_partial_of_same_version_is_resumed(self):
        with FakeServer() as s:
            s.files["/a"] = SAMPLE_DATA
            partial = self.dest.with_suffix(".bin.parcial")
            partial.write_bytes(SAMPLE_DATA[:1000])
            net.mark_origin(partial, "v1")
            net.download(s.url("/a"), self.dest, identity="v1", delay=0)
            self.assertEqual(SAMPLE_DATA, self.dest.read_bytes())
            self.assertEqual([("/a", "bytes=1000-")], s.requests)


class TextTest(unittest.TestCase):
    def test_text(self):
        with FakeServer() as s:
            s.files["/t"] = "ação\n".encode()
            self.assertEqual("ação\n", net.fetch_text(s.url("/t"), delay=0))

    def test_gzip_text_is_decompressed(self):
        with FakeServer() as s:
            s.files["/t"] = "ação\n".encode()
            s.gzip_always.add("/t")
            self.assertEqual("ação\n", net.fetch_text(s.url("/t"), delay=0))

    def test_text_404_fails_immediately(self):
        with FakeServer() as s:
            with self.assertRaises(net.PermanentError):
                net.fetch_text(s.url("/nada"), delay=5)
            self.assertEqual(1, len(s.requests))


class RedirectTargetTest(unittest.TestCase):
    """net.redirect_target: para onde uma URL redireciona, sem seguir (o `-latest` da Geofabrik -> o arquivo datado)."""

    def test_gives_the_target_without_following(self):
        with FakeServer() as s:
            s.redirect["/brazil-latest.osm.pbf"] = "/brazil-261107.osm.pbf"  # relativo: vira absoluto
            s.files["/brazil-261107.osm.pbf"] = SAMPLE_DATA
            target = net.redirect_target(s.url("/brazil-latest.osm.pbf"), delay=0)
            self.assertEqual(s.url("/brazil-261107.osm.pbf"), target)
            self.assertEqual([("/brazil-latest.osm.pbf", None)], s.requests)

    def test_absolute_target_is_kept(self):
        with FakeServer() as s:
            s.redirect["/latest"] = "https://download.example.org/brazil-261107.osm.pbf"
            self.assertEqual("https://download.example.org/brazil-261107.osm.pbf",
                             net.redirect_target(s.url("/latest"), delay=0))

    def test_answer_without_redirect_fails(self):
        with FakeServer() as s:
            s.files["/latest"] = SAMPLE_DATA
            with self.assertRaises(net.PermanentError):
                net.redirect_target(s.url("/latest"), delay=0)

    def test_404_fails_immediately(self):
        with FakeServer() as s:
            with self.assertRaises(net.PermanentError):
                net.redirect_target(s.url("/nada"), delay=5)
            self.assertEqual(1, len(s.requests))

    def test_503_is_retried(self):
        with FakeServer() as s:
            s.code["/latest"] = 503
            with self.assertRaises(net.DownloadError):
                net.redirect_target(s.url("/latest"), attempts=3, delay=0)
            self.assertEqual(3, len(s.requests))


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


if __name__ == "__main__":
    unittest.main()
