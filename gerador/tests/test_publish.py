import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from andorinha.publishing import drops  # noqa: E402
from andorinha import config, publishing  # noqa: E402


def cat(base=64, **states):
    return {"base": {"tiles": base}, "states": {state: {"tiles": n} for state, n in states.items()}}


class DropsTest(unittest.TestCase):
    def test_no_change(self):
        self.assertEqual([], drops(cat(GO=605, DF=23), cat(GO=605, DF=23)))

    def test_small_variation_passes(self):
        self.assertEqual([], drops(cat(GO=605), cat(GO=590)))

    def test_large_drop_blocks(self):
        self.assertEqual(["GO: 605 -> 500 tiles"], drops(cat(GO=605), cat(GO=500)))

    def test_missing_state_blocks(self):
        self.assertEqual(["DF: sumiu do catálogo"], drops(cat(GO=605, DF=23), cat(GO=605)))

    def test_base_also_counts(self):
        self.assertEqual(["base: 64 -> 40 tiles"], drops(cat(base=64), cat(base=40)))

    def test_new_state_is_not_a_problem(self):
        self.assertEqual([], drops(cat(GO=605), cat(GO=605, DF=23)))



class LicenseFileTest(unittest.TestCase):
    """LICENCA-DADOS.txt sobe em cada release, ao lado dos pacotes, para quem baixa só os arquivos receber a licença e
    a atribuição. Fica fora dos .tar.zst: quem lê os pacotes recusa um caminho que não seja de tile."""

    CAT = {"build_id": "2026-11-15-0e2e0e2e", "valhalla_version": "3.6.3",
           "osm": {"timestamp": "2026-11-15T00:33:49Z", "md5": "0" * 32,
                   "url": "https://download.openstreetmap.fr/extracts/south-america/brazil-latest.osm.pbf"},
           "base": {"file": "andorinha-base.tar.zst"}, "states": {"GO": {"file": "andorinha-GO.tar.zst"}}}

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.dist = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_release_takes_the_packages_and_the_license(self):
        files = publishing.release_files(self.CAT, self.dist)
        self.assertEqual(["andorinha-base.tar.zst", "andorinha-GO.tar.zst", publishing.LICENSE_FILE],
                         [f.name for f in files])
        self.assertTrue((self.dist / publishing.LICENSE_FILE).exists())

    def test_license_says_who_to_credit_and_where_the_data_came_from(self):
        text = publishing.license_text(self.CAT)
        for expected in (config.ATTRIBUTION, "ODbL", "2026-11-15-0e2e0e2e", "2026-11-15T00:33:49Z",
                         self.CAT["osm"]["url"], "IBGE", "openstreetmap.org/copyright"):
            self.assertIn(expected, text)

    def test_license_carries_the_full_odbl_text(self):
        """Sozinho, sem o repositório: o texto inteiro da licença vai junto."""
        odbl = (config.REPO / "LICENSE").read_text(encoding="utf-8")
        self.assertIn(odbl.strip(), publishing.license_text(self.CAT))

    def test_license_is_not_a_package(self):
        """O catálogo e a conferência continuam só com os pacotes: quem lê o catálogo não vê o arquivo."""
        self.assertNotIn(publishing.LICENSE_FILE, publishing.package_files(self.CAT))


if __name__ == "__main__":
    unittest.main()


class ZstdFilesTest(unittest.TestCase):
    """O .tar.zst de cada pacote (2026-10-05) sobe na release junto do .tar.gz e é conferido do mesmo jeito: existe,
    cabe no limite do GitHub e o sha256 bate com o catálogo."""

    def _cat(self, folder):
        from andorinha.util import file_hash
        for name in ("andorinha-base.tar.gz", "andorinha-base.tar.zst"):
            (folder / name).write_bytes(name.encode())
        gz, zst = folder / "andorinha-base.tar.gz", folder / "andorinha-base.tar.zst"
        return {"valhalla_version": config.VALHALLA_VERSION, "states": {},
                "base": {"file": gz.name, "sha256": file_hash(gz), "tiles": 1,
                         "zstd": {"file": zst.name, "bytes": zst.stat().st_size, "sha256": file_hash(zst)}}}

    def test_both_files_go_to_the_release(self):
        cat = {"base": {"file": "andorinha-base.tar.gz", "zstd": {"file": "andorinha-base.tar.zst"}},
               "states": {"GO": {"file": "andorinha-GO.tar.gz"}}}  # sem zstd (geração antiga): só o gz
        self.assertEqual(["andorinha-base.tar.gz", "andorinha-base.tar.zst", "andorinha-GO.tar.gz"],
                         publishing.package_files(cat))

    def test_missing_or_wrong_zstd_blocks(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            cat = self._cat(folder)
            with mock.patch.object(config, "DIST", folder):
                self.assertEqual([], publishing.check(cat, None, accept_drop=True, accept_older=True))
                (folder / "andorinha-base.tar.zst").write_bytes(b"outro")
                self.assertEqual(["base: sha256 de andorinha-base.tar.zst não bate com o catálogo"],
                                 publishing.check(cat, None, accept_drop=True, accept_older=True))
                (folder / "andorinha-base.tar.zst").unlink()
                self.assertEqual(["base: falta andorinha-base.tar.zst"],
                                 publishing.check(cat, None, accept_drop=True, accept_older=True))
