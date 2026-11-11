"""O bloco `contract` do catalogo.json: o contrato de catálogo comum v1 (docs/CATALOGO.md).

Confere o formato contra o esquema (docs/catalogo-v1.schema.json), as regras que o esquema não expressa, as URLs
absolutas, os sha256 iguais aos das chaves antigas, a ordem do bbox e as versões do motor amarradas ao config.
"""
import copy
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

GENERATOR_DIR = Path(__file__).resolve().parent.parent
REPO = GENERATOR_DIR.parent
sys.path.insert(0, str(GENERATOR_DIR))
from andorinha import catalog, config, engine  # noqa: E402
from tests import contract_check  # noqa: E402
from tests.test_catalog import OSM, package  # noqa: E402

DOCS = REPO / "docs"
FULL = "0123456789abcdef0123456789abcdef01234567"

# O commit da tag de cada versão do Valhalla em github.com/valhalla/valhalla e a versão do valhalla-mobile que embute
# esse commit (submódulo src/valhalla). Trocar VALHALLA_VERSION no config sem conferir e acrescentar a linha aqui falha.
KNOWN_ENGINES = {"3.9.1": ("bafb69902220615a48307d5c790fb6c943802ba4", "0.6.4")}


def built(**kwargs):
    packages = {"base": package("base", [-33.76, -74.0, 5.28, -29.29], [-34.0, -76.0, 6.0, -32.0]),
                "DF": package("DF", [-16.06, -48.29, -15.49, -47.3], [-17.0, -49.0, -15.0, -47.0]),
                "GO": package("GO", [-19.5, -53.25, -12.39, -45.9], [-20.0, -54.0, -12.0, -45.0])}
    kwargs.setdefault("generator_commit", FULL[:7])
    kwargs.setdefault("generator_commit_full", FULL)
    return catalog.build_catalog(catalog.build_id("2026-09-30", OSM["md5"]), OSM, packages, **kwargs)


class ContractShapeTest(unittest.TestCase):
    def setUp(self):
        self.cat = built()
        self.block = self.cat["contract"]

    def test_valid_against_the_schema_and_the_rules(self):
        self.assertEqual([], contract_check.problems(self.cat, DOCS))

    def test_the_checker_catches_a_broken_block(self):
        broken = copy.deepcopy(self.cat)
        broken["contract"]["files"][1]["sha256"] = "X"
        broken["contract"]["files"][1]["requires"] = ["nada"]
        self.assertTrue(contract_check.problems(broken, DOCS))
        broken = copy.deepcopy(self.cat)
        broken["contract"]["files"][1]["requires"] = ["nada"]
        self.assertTrue(contract_check.problems(broken, DOCS))

    def test_identity_and_versions(self):
        b = self.block
        self.assertEqual(("catalogo-comum", 1, 1, "andorinha"), (b["name"], b["version"], b["min_reader_version"],
                                                                 b["dataset"]))
        self.assertEqual(self.cat["build_id"], b["build_id"])
        self.assertEqual(self.cat["built_at"], b["built_at"])
        self.assertEqual(self.cat["release_url"], b["release_url"])
        self.assertEqual({"crs": "EPSG:4326", "bbox_order": "lat_min,lon_min,lat_max,lon_max"}, b["coordinates"])

    def test_generator_full_commit_and_licences(self):
        self.assertEqual({"repository": config.REPOSITORY_URL, "commit": FULL}, self.block["generator"])
        self.assertEqual(FULL[:7], self.cat["generator_commit"])  # a chave antiga continua curta
        self.assertEqual({"data": "ODbL-1.0", "code": "MIT", "attribution": config.ATTRIBUTION}, self.block["license"])
        self.assertIn("Open Database License", (REPO / "LICENSE").read_text(encoding="utf-8"))
        self.assertTrue((GENERATOR_DIR / "LICENSE").read_text(encoding="utf-8").startswith("MIT License"))

    def test_a_full_sha_in_generator_commit_fills_both(self):
        cat = built(generator_commit=FULL, generator_commit_full=None)
        self.assertEqual(FULL, cat["generator_commit"])
        self.assertEqual(FULL, cat["contract"]["generator"]["commit"])

    def test_a_commit_that_is_not_the_catalog_one_is_refused(self):
        with self.assertRaises(ValueError):
            built(generator_commit="abc1234", generator_commit_full=FULL)

    def test_full_commit_from_the_checkout(self):
        sha = catalog.git_commit_full()
        self.assertTrue(sha is None or re.fullmatch(r"[0-9a-f]{40}", sha), sha)

    def test_sources_with_dates_and_checksums(self):
        sources = {s["id"]: s for s in self.block["sources"]}
        self.assertEqual({"md5": OSM["md5"]}, sources["openstreetmap"]["checksums"])
        self.assertEqual(OSM["timestamp"], sources["openstreetmap"]["date"])
        self.assertEqual({"sha256": config.IBGE_MESH_COPY_SHA256}, sources["ibge-malha-uf"]["checksums"])
        self.assertEqual({"sha256": config.TIMEZONE_SHA256}, sources["timezone-boundary-builder"]["checksums"])
        self.assertEqual(config.TIMEZONE_RELEASE, sources["timezone-boundary-builder"]["version"])
        self.assertEqual({"date": OSM["timestamp"], "md5": OSM["md5"]}, self.block["osm_snapshot"])

    def test_a_timezone_release_other_than_the_config_one_has_no_checksum(self):
        cat = built()
        cat["timezones"]["release"] = "2020a"
        block = catalog.contract(cat, FULL)
        tz = [s for s in block["sources"] if s["id"] == "timezone-boundary-builder"][0]
        self.assertEqual({}, tz["checksums"])


class ContractFilesTest(unittest.TestCase):
    def setUp(self):
        self.cat = built()
        self.files = {f["id"]: f for f in self.cat["contract"]["files"]}

    def legacy(self, file_id):
        return self.cat["base"] if file_id == "base" else self.cat["states"][file_id]

    def test_one_file_per_package(self):
        self.assertEqual({"base", *self.cat["states"]}, set(self.files))

    def test_absolute_urls_are_release_url_plus_file(self):
        for file_id, f in self.files.items():
            self.assertTrue(f["url"].startswith("https://"), f["url"])
            self.assertEqual(self.cat["release_url"] + self.legacy(file_id)["file"], f["url"])

    def test_sha256_and_sizes_are_the_legacy_ones(self):
        for file_id, f in self.files.items():
            old = self.legacy(file_id)
            self.assertEqual((old["sha256"], old["bytes"], old["bytes_tiles"]),
                             (f["sha256"], f["bytes"], f["bytes_unpacked"]))

    def test_bbox_is_the_legacy_one_latitude_first(self):
        for file_id, f in self.files.items():
            self.assertEqual(self.legacy(file_id)["bbox"], f["bbox"])
        self.assertEqual([-19.5, -53.25, -12.39, -45.9], self.files["GO"]["bbox"])  # Goiás: lat ~-19..-12, lon ~-53..-46

    def test_base_links_and_states_need_it(self):
        base, go = self.files["base"], self.files["GO"]
        self.assertEqual(("base", "BR", "BASE_HIGHWAYS_ONLY", []), (base["kind"], base["region"],
                                                                    base["coverage_level"], base["requires"]))
        self.assertEqual(("uf", "GO", "DETAILED", ["base"]), (go["kind"], go["region"], go["coverage_level"],
                                                              go["requires"]))

    def test_format_and_unpacking(self):
        for f in self.files.values():
            self.assertEqual(("valhalla-tiles", "application/zstd", ["zstd", "tar"]),
                             (f["format"], f["media_type"], f["unpack"]))
            self.assertTrue(f["url"].endswith(".tar.zst"))


class EngineVersionsTest(unittest.TestCase):
    """As versões do bloco `compatibility.andorinha` vêm do config e andam juntas."""

    def setUp(self):
        self.cat = built()
        self.compat = self.cat["contract"]["compatibility"]
        self.engine = self.compat["andorinha"]

    def test_block_equals_the_config(self):
        self.assertEqual({
            "engine": "valhalla",
            "valhalla_engine_version": config.VALHALLA_VERSION,
            "valhalla_commit": config.VALHALLA_COMMIT,
            "tile_format_version": config.TILE_FORMAT_VERSION,
            "valhalla_mobile_version": config.VALHALLA_MOBILE_VERSION,
            "valhalla_mobile_artifact": config.VALHALLA_MOBILE_ARTIFACT,
            "valhalla_mobile_aar_sha256": config.VALHALLA_MOBILE_AAR_SHA256,
            "valhalla_mobile_abis": list(config.VALHALLA_MOBILE_ABIS),
            "required_config": engine.REQUIRED_CONFIG,
        }, self.engine)
        self.assertFalse(self.compat["mix_generations"])
        self.assertEqual({"name": "valhalla-tiles", "version": config.TILE_FORMAT_VERSION}, self.compat["data_format"])

    def test_one_valhalla_version_everywhere(self):
        version = config.VALHALLA_VERSION
        self.assertEqual(version, self.cat["valhalla_version"])
        self.assertEqual(version, self.engine["valhalla_engine_version"])
        # o cabeçalho do tile traz a versão do Valhalla que o gerou
        self.assertEqual(version, self.engine["tile_format_version"])
        requirements = (GENERATOR_DIR / "requirements.in").read_text(encoding="utf-8")
        self.assertIn(f"pyvalhalla=={version}\n", requirements)
        engine.check_version(version)

    def test_commit_and_mobile_library_match_the_version(self):
        self.assertIn(config.VALHALLA_VERSION, KNOWN_ENGINES,
                      "versão nova do Valhalla: confira o commit da tag e o valhalla-mobile e acrescente em KNOWN_ENGINES")
        commit, mobile = KNOWN_ENGINES[config.VALHALLA_VERSION]
        self.assertEqual(commit, config.VALHALLA_COMMIT)
        self.assertEqual(mobile, config.VALHALLA_MOBILE_VERSION)
        self.assertTrue(config.VALHALLA_MOBILE_ARTIFACT.endswith(":" + config.VALHALLA_MOBILE_VERSION))
        self.assertRegex(config.VALHALLA_MOBILE_AAR_SHA256, r"^[0-9a-f]{64}$")
        self.assertTrue(set(config.VALHALLA_MOBILE_ABIS) <= contract_check.ABIS)

    def test_required_config_repeats_the_top_level_key(self):
        self.assertEqual(self.cat["required_config"], self.engine["required_config"])
        self.assertEqual(engine.REQUIRED_CONFIG, self.engine["required_config"])

    def test_a_catalog_of_another_valhalla_is_refused(self):
        cat = built()
        cat["valhalla_version"] = "3.6.3"
        with self.assertRaises(ValueError):
            catalog.contract(cat, FULL)


class OnlyAddsTest(unittest.TestCase):
    def test_the_old_keys_are_untouched(self):
        cat = built()
        old = {k: v for k, v in cat.items() if k != "contract"}
        self.assertEqual({"schema", "build_id", "built_at", "valhalla_version", "generator_commit", "required_config",
                          "osm", "release_url", "attribution", "timezones", "base", "states"}, set(old))
        self.assertEqual(1, cat["schema"])
        self.assertEqual(engine.REQUIRED_CONFIG, cat["required_config"])


class SharedDocumentsTest(unittest.TestCase):
    def test_spec_and_schema_are_the_shared_ones(self):
        self.assertEqual({}, contract_check.doc_mismatches(DOCS))

    def test_the_readme_points_to_the_spec(self):
        self.assertIn("docs/CATALOGO.md", (REPO / "README.md").read_text(encoding="utf-8"))


class RecatalogTest(unittest.TestCase):
    """scripts/recatalog.py sobre o catálogo publicado na raiz: só acrescenta `contract`."""

    def test_rebuilds_the_published_catalog(self):
        published = json.loads((REPO / "catalogo.json").read_text(encoding="utf-8"))
        if catalog.git_commit_full(published["generator_commit"]) is None:
            self.skipTest("o commit da geração publicada não está neste checkout (clone raso)")
        sys.path.insert(0, str(GENERATOR_DIR / "scripts"))
        import recatalog  # noqa: E402
        out = recatalog.rebuild(published)
        self.assertEqual({k: v for k, v in published.items() if k != "contract"},
                         {k: v for k, v in out.items() if k != "contract"})
        self.assertEqual(list(published), [k for k in out if k != "contract"])
        self.assertEqual([], contract_check.problems(out, DOCS))
        self.assertEqual(len(published["states"]) + 1, len(out["contract"]["files"]))

    def test_constants_are_read_without_running_the_file(self):
        sys.path.insert(0, str(GENERATOR_DIR / "scripts"))
        import recatalog  # noqa: E402
        head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True)
        if head.returncode != 0:
            self.skipTest("sem git")
        found = recatalog.constants_at(head.stdout.strip(), ["VALHALLA_VERSION", "NAO_EXISTE"])
        self.assertEqual({"VALHALLA_VERSION": config.VALHALLA_VERSION}, found)


if __name__ == "__main__":
    unittest.main()
