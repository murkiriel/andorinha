"""Contrato do catalogo.json. Mudar uma chave aqui quebra quem consome os pacotes."""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

GENERATOR_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GENERATOR_DIR))
from andorinha import catalog, config, engine  # noqa: E402
from andorinha.municipalities import MunicipalityFile  # noqa: E402
from andorinha.packing import Package, polygon_bbox  # noqa: E402

TOP_LEVEL_KEYS = {"schema", "build_id", "built_at", "valhalla_version", "generator_commit", "required_config", "osm",
                  "release_url", "attribution", "timezones", "base", "states", "municipalities", "contract"}
MUNICIPALITIES_KEYS = {"format_version", "mesh", "weights", "state_weights", "states"}
MUNICIPALITY_FILE_KEYS = {"file", "bytes", "sha256", "bytes_unpacked", "municipalities", "weights", "generations",
                          "same_generation", "state_weights"}
STATE_WEIGHTS_KEYS = {"use_highways", "use_trails", "tiles_build_id", "valhalla_version", "date", "same_generation"}
WEIGHTS_BUILD_ID = "2026-11-09-c17dff60"  # a geração dos tiles com que os pesos do exemplo foram calculados
PACKAGE_KEYS = {"file", "bytes", "sha256", "tiles", "bytes_tiles", "bbox", "bbox_tiles"}
MD5 = "cb859dc31c5fb8459735f0cd273d2c4f"
OSM = {"path": "brazil-latest.osm.pbf", "url": "https://exemplo/brazil-latest.osm.pbf",
       "timestamp": "2026-09-29T00:59:51Z", "md5": MD5, "size": 123}


def package(name, bbox, bbox_tiles):
    return Package(name=name, file=f"andorinha-{name}.tar.zst", size=1000, sha256="a" * 64, tiles=10,
                   bytes_tiles=3000, bbox=bbox, bbox_tiles=bbox_tiles)


def municipality_file(state, municipalities=3, weights=2, tiles_build_id=WEIGHTS_BUILD_ID):
    generations = [{"tiles_build_id": tiles_build_id, "valhalla_version": "3.9.1", "date": "2026-11-14",
                    "count": weights}]
    return MunicipalityFile(state=state, file=f"andorinha-municipios-{state}.geojson.zst", size=500, sha256="b" * 64,
                            bytes_unpacked=2000, municipalities=municipalities, weights=weights, generations=generations,
                            state_weights={"use_highways": 0.8, "use_trails": 0.4, "tiles_build_id": tiles_build_id,
                                           "valhalla_version": "3.9.1", "date": "2026-11-14"})


def municipality_files(**kwargs):
    """DF e GO, que têm pacote nos catálogos de exemplo, e SP, que não tem."""
    return {state: municipality_file(state, **kwargs) for state in ("DF", "GO", "SP")}


def fake_catalog():
    packages = {"base": package("base", [-33.75, -73.99, 5.27, -34.79], [-36.0, -76.0, 8.0, -32.0]),
                "DF": package("DF", [-16.05, -48.29, -15.5, -47.31], [-17.0, -49.0, -15.0, -47.0]),
                "GO": package("GO", [-19.5, -53.25, -12.4, -45.91], [-20.0, -54.0, -12.0, -45.0])}
    build_id = catalog.build_id("2026-09-30", MD5)
    return catalog.build_catalog(build_id, OSM, packages, generator_commit="abc1234",
                                 municipality_files=municipality_files())


def readme_keys():
    """(chaves de topo, chaves de pacote) listadas no bloco de código da seção `catalogo.json` do README da raiz."""
    text = (GENERATOR_DIR.parent / "README.md").read_text(encoding="utf-8")
    section = text.split("## `catalogo.json`", 1)[1]
    chunk = section.split("```", 2)[1]
    top_level, package_keys_ = set(), set()
    for line in chunk.splitlines():
        m = re.match(r"^( *)([a-z_0-9]+)\s", line + " ")
        if m:
            (package_keys_ if m.group(1) else top_level).add(m.group(2))
    return top_level, package_keys_


class ContractTest(unittest.TestCase):
    def test_top_level_keys(self):
        self.assertEqual(TOP_LEVEL_KEYS, set(fake_catalog()))

    def test_keys_of_each_package(self):
        cat = fake_catalog()
        self.assertEqual(PACKAGE_KEYS, set(cat["base"]))
        for state, entry in cat["states"].items():
            self.assertEqual(PACKAGE_KEYS | {"name"}, set(entry), state)
            self.assertEqual(config.STATES[state], entry["name"])

    def test_types(self):
        cat = fake_catalog()
        self.assertIsInstance(cat["schema"], int)
        for key in ("build_id", "built_at", "valhalla_version", "generator_commit", "release_url", "attribution"):
            self.assertIsInstance(cat[key], str, key)
        self.assertEqual({"timestamp", "md5", "url"}, set(cat["osm"]))
        for entry in [cat["base"], *cat["states"].values()]:
            for key in ("bytes", "tiles", "bytes_tiles"):
                self.assertIsInstance(entry[key], int)
            for key in ("bbox", "bbox_tiles"):
                self.assertEqual(4, len(entry[key]))
            # Desde 2026-10-05 o pacote é só o .tar.zst (houve uma geração com os dois e a chave `zstd`).
            self.assertTrue(entry["file"].endswith(".tar.zst"))

    def test_build_id_has_date_and_extract_md5(self):
        self.assertEqual("2026-09-30-cb859dc3", catalog.build_id("2026-09-30", MD5))
        self.assertRegex(fake_catalog()["build_id"], r"^\d{4}-\d{2}-\d{2}-[0-9a-f]{8}$")
        # Mesmo dia, outro extrato: outro id (pacotes das duas gerações não podem se misturar).
        self.assertNotEqual(catalog.build_id("2026-09-30", MD5), catalog.build_id("2026-09-30", "f" * 32))

    def test_release_url_uses_build_id(self):
        cat = fake_catalog()
        self.assertTrue(cat["release_url"].endswith(f"/releases/download/{cat['build_id']}/"))

    def test_required_config_matches_validation(self):
        cat = fake_catalog()
        self.assertEqual(engine.REQUIRED_CONFIG, cat["required_config"])
        self.assertIs(False, cat["required_config"]["loki.use_connectivity"])
        self.assertEqual(5_000_000, cat["required_config"]["service_limits.motorcycle.max_distance"])

    def test_well_formed_rectangles(self):
        # Não vale "bbox dentro de bbox_tiles": nos dados reais, onde a UF não tem via não há tile (ilha da Trindade
        # no ES e na base, extremo norte de AP, PA e RR), e o bbox da UF passa do dos tiles.
        cat = fake_catalog()
        for entry in [cat["base"], *cat["states"].values()]:
            for key in ("bbox", "bbox_tiles"):
                s, w, n, e = entry[key]
                self.assertTrue(-90 <= s < n <= 90 and -180 <= w < e <= 180, (key, entry[key]))

    def test_generator_commit_from_checkout(self):
        self.assertRegex(catalog.git_commit_hash(), r"^([0-9a-f]{7,}|desconhecido)$")

    def test_save_and_load_roundtrip(self):
        cat = fake_catalog()
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "catalogo.json"
            catalog.save(cat, dest)
            self.assertEqual(cat, json.loads(dest.read_text(encoding="utf-8")))

    def test_municipalities_describe_one_file_per_state_with_a_package(self):
        municipal = fake_catalog()["municipalities"]
        self.assertEqual(MUNICIPALITIES_KEYS, set(municipal))
        self.assertEqual(1, municipal["format_version"])
        self.assertEqual(["DF", "GO"], list(municipal["states"]))  # SP não tem pacote neste catálogo
        for state, entry in municipal["states"].items():
            self.assertEqual(MUNICIPALITY_FILE_KEYS, set(entry), state)
            self.assertEqual(f"andorinha-municipios-{state}.geojson.zst", entry["file"])
            self.assertEqual((500, "b" * 64, 2000, 3, 2), (entry["bytes"], entry["sha256"], entry["bytes_unpacked"],
                                                           entry["municipalities"], entry["weights"]))

    def test_municipalities_say_the_mesh_and_the_table_they_came_from(self):
        municipal = fake_catalog()["municipalities"]
        self.assertEqual({"url": config.IBGE_MUNICIPAL_MESH_URL, "date": config.IBGE_MUNICIPAL_MESH_COPY_DATE,
                          "sha256": config.IBGE_MUNICIPAL_MESH_COPY_SHA256, "count": 6}, municipal["mesh"])
        self.assertEqual({"costing": "motorcycle", "use_tolls": 1.0, "sha256": config.MOTORCYCLE_WEIGHTS_SHA256,
                          "count": 4, "same_generation": False, "generations": [
                              {"tiles_build_id": WEIGHTS_BUILD_ID, "valhalla_version": "3.9.1", "date": "2026-11-14",
                               "count": 4}]}, municipal["weights"])

    def test_weights_of_an_earlier_generation_are_declared_per_state(self):
        """O catálogo diz, em cada UF, com que tiles os pesos foram calculados: o leitor vê que não são os desta."""
        cat = fake_catalog()
        self.assertNotEqual(WEIGHTS_BUILD_ID, cat["build_id"])
        for entry in cat["municipalities"]["states"].values():
            self.assertIs(False, entry["same_generation"])
            self.assertEqual([WEIGHTS_BUILD_ID], [g["tiles_build_id"] for g in entry["generations"]])

    def test_each_state_has_its_state_weight_with_the_origin(self):
        cat = fake_catalog()
        for state, entry in cat["municipalities"]["states"].items():
            self.assertEqual(STATE_WEIGHTS_KEYS, set(entry["state_weights"]), state)
            self.assertEqual({"use_highways": 0.8, "use_trails": 0.4, "tiles_build_id": WEIGHTS_BUILD_ID,
                              "valhalla_version": "3.9.1", "date": "2026-11-14", "same_generation": False},
                             entry["state_weights"])

    def test_state_weights_table_is_summarized(self):
        self.assertEqual({"costing": "motorcycle", "use_tolls": 1.0, "sha256": config.MOTORCYCLE_STATE_WEIGHTS_SHA256,
                          "count": 2, "same_generation": False, "generations": [
                              {"tiles_build_id": WEIGHTS_BUILD_ID, "valhalla_version": "3.9.1", "date": "2026-11-14",
                               "count": 2}]}, fake_catalog()["municipalities"]["state_weights"])

    def test_the_digests_of_the_tables_the_generation_published_replace_the_seed_ones(self):
        """O sha256 do catálogo é o da tabela que sobe na release (a que o estudo atualizou), não o da semente."""
        packages = {"base": package("base", [-33.75, -73.99, 5.27, -34.79], [-36.0, -76.0, 8.0, -32.0]),
                    "DF": package("DF", [-16.05, -48.29, -15.5, -47.31], [-17.0, -49.0, -15.0, -47.0])}
        cat = catalog.build_catalog(catalog.build_id("2026-09-30", MD5), OSM, packages, generator_commit="abc1234",
                                    municipality_files={"DF": municipality_file("DF")},
                                    weights_digests=("1" * 64, "2" * 64))
        self.assertEqual(("1" * 64, "2" * 64), (cat["municipalities"]["weights"]["sha256"],
                                                cat["municipalities"]["state_weights"]["sha256"]))
        sources = {s["id"]: s for s in cat["contract"]["sources"]}
        self.assertEqual({"sha256": "1" * 64}, sources["pesos-moto-municipios"]["checksums"])
        self.assertEqual({"sha256": "2" * 64}, sources["pesos-moto-estados"]["checksums"])
        self.assertIn("pesos_moto_municipios.csv da release", sources["pesos-moto-municipios"]["note"])

    def test_files_without_state_weights_keep_the_catalog_of_before(self):
        """Só acrescenta: sem peso de estado, `municipalities` sai com as chaves de antes."""
        packages = {"base": package("base", [-33.75, -73.99, 5.27, -34.79], [-36.0, -76.0, 8.0, -32.0]),
                    "DF": package("DF", [-16.05, -48.29, -15.5, -47.31], [-17.0, -49.0, -15.0, -47.0])}
        files = {"DF": municipality_file("DF")}
        files["DF"].state_weights = None
        municipal = catalog.build_catalog(catalog.build_id("2026-09-30", MD5), OSM, packages, generator_commit="abc1234",
                                          municipality_files=files)["municipalities"]
        self.assertEqual(MUNICIPALITIES_KEYS - {"state_weights"}, set(municipal))
        self.assertEqual(MUNICIPALITY_FILE_KEYS - {"state_weights"}, set(municipal["states"]["DF"]))

    def test_weights_of_this_generation_are_declared_too(self):
        packages = {"base": package("base", [-33.75, -73.99, 5.27, -34.79], [-36.0, -76.0, 8.0, -32.0]),
                    "DF": package("DF", [-16.05, -48.29, -15.5, -47.31], [-17.0, -49.0, -15.0, -47.0])}
        build_id = catalog.build_id("2026-09-30", MD5)
        files = {"DF": municipality_file("DF", tiles_build_id=build_id)}
        municipal = catalog.build_catalog(build_id, OSM, packages, generator_commit="abc1234",
                                          municipality_files=files)["municipalities"]
        self.assertIs(True, municipal["states"]["DF"]["same_generation"])
        self.assertIs(True, municipal["weights"]["same_generation"])
        self.assertIs(True, municipal["states"]["DF"]["state_weights"]["same_generation"])
        self.assertIs(True, municipal["state_weights"]["same_generation"])

    def test_a_catalog_without_the_files_has_no_municipalities_key(self):
        packages = {"base": package("base", [-33.75, -73.99, 5.27, -34.79], [-36.0, -76.0, 8.0, -32.0])}
        cat = catalog.build_catalog(catalog.build_id("2026-09-30", MD5), OSM, packages, generator_commit="abc1234")
        self.assertEqual(TOP_LEVEL_KEYS - {"municipalities"}, set(cat))

    def test_readme_documents_all_keys(self):
        top_level, package_keys_ = readme_keys()
        self.assertEqual(TOP_LEVEL_KEYS, top_level)
        self.assertEqual(PACKAGE_KEYS | {"name"}, package_keys_)


class BboxTest(unittest.TestCase):
    def test_polygon_bbox_in_pardal_order(self):
        try:
            from shapely.geometry import box
        except ImportError:
            self.skipTest("shapely não instalado")
        # box(lon mín, lat mín, lon máx, lat máx) -> [lat mín, lon mín, lat máx, lon máx], 2 casas ARREDONDADAS PARA
        # FORA: o retângulo nunca fica menor que o polígono, então um ponto na borda da UF nunca fica fora do bbox dela.
        self.assertEqual([-16.06, -48.29, -15.49, -47.3], polygon_bbox(box(-48.2861, -16.0502, -47.3079, -15.4999)))

    def test_published_catalog_is_latitude_first(self):
        """`[lat_min, lon_min, lat_max, lon_max]`, ao contrário do GeoJSON. Conferido no catálogo publicado: no
        Brasil, a latitude vai de ~-34 a ~6 e a longitude de ~-74 a ~-28, então as duas ordens não se confundem."""
        cat = json.loads((GENERATOR_DIR.parent / "catalogo.json").read_text(encoding="utf-8"))
        for name, entry in [("base", cat["base"]), *cat["states"].items()]:
            for key in ("bbox", "bbox_tiles"):
                lat0, lon0, lat1, lon1 = entry[key]
                self.assertTrue(-35 <= lat0 < lat1 <= 7, (name, key, entry[key]))
                self.assertTrue(-77 <= lon0 < lon1 <= -28, (name, key, entry[key]))

    def test_readme_declares_order_and_datum(self):
        section = (GENERATOR_DIR.parent / "README.md").read_text(encoding="utf-8").split("## `catalogo.json`", 1)[1]
        section = section.split("\n## ", 1)[0]
        self.assertIn("`[lat_min, lon_min, lat_max, lon_max]`", section)
        self.assertIn("WGS84", section)
        self.assertIn("GeoJSON", section)


if __name__ == "__main__":
    unittest.main()
