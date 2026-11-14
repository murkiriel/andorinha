"""Os arquivos de apoio por UF (andorinha/municipalities.py): a malha municipal e a tabela de pesos versionadas, a
divisão por UF, o que vai em cada arquivo e os avisos sobre a geração dos tiles dos pesos."""
import gzip
import hashlib
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

GENERATOR_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GENERATOR_DIR))
from andorinha import config, municipalities  # noqa: E402
from andorinha.municipalities import Inputs, Weight  # noqa: E402

try:
    import zstandard
except ImportError:  # pragma: no cover
    zstandard = None

OLD = "2026-11-09-c17dff60"
NEW = "2026-11-15-0123abcd"
SQUARE = {"type": "Polygon", "coordinates": [[[-48.0, -16.0], [-47.0, -16.0], [-47.0, -15.0], [-48.0, -16.0]]]}
HEADER = "codigo_ibge;use_highways;use_trails;geracao_tiles;versao_valhalla;data_calculo\n"
STATE_HEADER = "codigo_ibge;uf;use_highways;use_trails;geracao_tiles;versao_valhalla;data_calculo\n"


def weight(highways=0.6, trails=0.4, tiles=OLD, version="3.9.1", date="2026-11-14"):
    return Weight(highways, trails, tiles, version, date)


def small_inputs():
    """Três municípios de Goiás (um sem peso, um de outra geração de tiles) e o do DF."""
    mesh = {"5200050": SQUARE, "5208707": SQUARE, "5200100": SQUARE, "5300108": SQUARE}
    weights = {"5208707": weight(0.8, 0.2), "5200050": weight(tiles=NEW, date="2026-11-15"), "5300108": weight(0.4, 0.4)}
    return Inputs(mesh, weights, {"GO": weight(0.8, 0.4), "DF": weight(0.9, 0.4, tiles=NEW, date="2026-11-15")})


def mesh_body(codes, geometry=SQUARE) -> bytes:
    features = [{"type": "Feature", "geometry": geometry, "properties": {"codarea": code}} for code in codes]
    return json.dumps({"type": "FeatureCollection", "features": features}).encode("utf-8")


def one_per_state():
    return [code + "00001" for code in config.IBGE_STATE_CODES]


class VersionedCopiesTest(unittest.TestCase):
    """As cópias do repositório são as que o config descreve, e a tabela só fala de municípios da malha."""

    @classmethod
    def setUpClass(cls):
        cls.inputs = municipalities.check()

    def test_mesh_copy_has_the_sha256_and_the_count_of_the_config(self):
        body = gzip.decompress(config.IBGE_MUNICIPAL_MESH_COPY.read_bytes())
        self.assertEqual(config.IBGE_MUNICIPAL_MESH_COPY_SHA256, hashlib.sha256(body).hexdigest())
        self.assertIsNone(municipalities.mesh_problem(body))
        self.assertEqual(5570, config.MUNICIPALITY_COUNT)
        self.assertEqual(5570, len(self.inputs.mesh))

    def test_mesh_copy_is_a_reproducible_gzip(self):
        """`gzip -9 -n`: sem nome nem data no cabeçalho, para a cópia não mudar de bytes à toa."""
        head = config.IBGE_MUNICIPAL_MESH_COPY.read_bytes()[:10]
        self.assertEqual(b"\x1f\x8b\x08\x00\x00\x00\x00\x00", head[:8])

    def test_mesh_url_is_the_municipal_one_of_the_same_api(self):
        self.assertIn("intrarregiao=municipio", config.IBGE_MUNICIPAL_MESH_URL)
        self.assertIn("qualidade=intermediaria", config.IBGE_MUNICIPAL_MESH_URL)
        self.assertEqual(config.IBGE_MESH_URL.split("?")[0], config.IBGE_MUNICIPAL_MESH_URL.split("?")[0])

    def test_every_state_has_municipalities_and_the_df_has_one(self):
        states = municipalities.by_state(self.inputs)
        self.assertEqual(sorted(config.STATES), list(states))
        self.assertEqual(5570, sum(len(codes) for codes in states.values()))
        self.assertEqual(["5300108"], states["DF"])
        self.assertEqual(853, len(states["MG"]))
        self.assertTrue(all(codes == sorted(codes) and codes for codes in states.values()))

    def test_weights_copy_has_the_sha256_and_the_rows_of_the_config(self):
        copy = config.MOTORCYCLE_WEIGHTS_COPY
        self.assertEqual(config.MOTORCYCLE_WEIGHTS_SHA256, hashlib.sha256(copy.read_bytes()).hexdigest())
        self.assertEqual(config.MOTORCYCLE_WEIGHTS_ROWS, len(self.inputs.weights))
        self.assertNotIn(b"\r", copy.read_bytes())  # fim de linha LF: o sha256 é o mesmo em qualquer sistema

    def test_weights_are_in_range_and_only_for_known_municipalities(self):
        self.assertTrue(set(self.inputs.weights) <= set(self.inputs.mesh))
        for code, w in self.inputs.weights.items():
            self.assertTrue(0.0 <= w.use_highways <= 1.0, code)
            self.assertTrue(0.0 <= w.use_trails <= config.MOTORCYCLE_WEIGHTS_MAX_USE_TRAILS, code)

    def test_six_municipalities_have_no_weight(self):
        missing = sorted(set(self.inputs.mesh) - set(self.inputs.weights))
        self.assertEqual(["1300201", "1301951", "1302108", "1302801", "1500701", "2605459"], missing)

    def test_every_row_says_which_tiles_it_was_computed_with(self):
        self.assertEqual([{"tiles_build_id": OLD, "valhalla_version": "3.9.1", "date": "2026-11-14", "count": 5564}],
                         municipalities.generations(self.inputs.weights.values()))


class StateWeightsCopyTest(unittest.TestCase):
    """A tabela de pesos por estado do repositório: o sha256 e a contagem do config, uma linha por UF da malha."""

    @classmethod
    def setUpClass(cls):
        cls.inputs = municipalities.check()

    def test_copy_has_the_sha256_and_the_rows_of_the_config(self):
        copy = config.MOTORCYCLE_STATE_WEIGHTS_COPY
        self.assertEqual(config.MOTORCYCLE_STATE_WEIGHTS_SHA256, hashlib.sha256(copy.read_bytes()).hexdigest())
        self.assertEqual(27, config.MOTORCYCLE_STATE_WEIGHTS_ROWS)
        self.assertEqual(27, len(self.inputs.state_weights))
        self.assertNotIn(b"\r", copy.read_bytes())

    def test_one_row_for_each_state_of_the_mesh(self):
        mesh_states = {municipalities.state_of(code) for code in self.inputs.mesh}
        self.assertEqual(mesh_states, set(self.inputs.state_weights))
        self.assertEqual(set(config.STATES), set(self.inputs.state_weights))

    def test_weights_are_in_range_and_say_where_they_came_from(self):
        for state, w in self.inputs.state_weights.items():
            self.assertTrue(0.0 <= w.use_highways <= 1.0, state)
            self.assertTrue(0.0 <= w.use_trails <= config.MOTORCYCLE_WEIGHTS_MAX_USE_TRAILS, state)
            self.assertEqual((OLD, "3.9.1", "2026-11-14"), (w.tiles_build_id, w.valhalla_version, w.date), state)
        self.assertEqual(weight(0.8, 0.4), self.inputs.state_weights["GO"])
        self.assertEqual(weight(0.6, 0.4), self.inputs.state_weights["SP"])


class ParseStateWeightsTest(unittest.TestCase):
    GOOD = f"52;GO;0.8;0.4;{OLD};3.9.1;2026-11-14\n"

    def test_reads_the_weight_by_state(self):
        out = municipalities.parse_state_weights(STATE_HEADER + self.GOOD + f"53;DF;0.9;0.4;{NEW};3.9.1;2026-11-15\n")
        self.assertEqual({"GO": weight(0.8, 0.4), "DF": weight(0.9, 0.4, tiles=NEW, date="2026-11-15")}, out)

    def test_problems_are_refused(self):
        cases = {
            "colunas": "52;GO;0.8;0.4\n",
            "não é o da UF": f"53;GO;0.8;0.4;{OLD};3.9.1;2026-11-14\n",
            "não é o da UF ": f"99;XX;0.8;0.4;{OLD};3.9.1;2026-11-14\n",
            "repetida": self.GOOD + self.GOOD,
            "peso fora": f"52;GO;1.5;0.4;{OLD};3.9.1;2026-11-14\n",
            "acima do teto": f"52;GO;0.8;0.6;{OLD};3.9.1;2026-11-14\n",
            "fora do formato": f"52;GO;0.8;0.4;ontem;3.9.1;2026-11-14\n",
        }
        for message, rows in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message.strip()):
                municipalities.parse_state_weights(STATE_HEADER + rows)
        with self.assertRaisesRegex(ValueError, "cabeçalho"):
            municipalities.parse_state_weights("codigo_ibge;uf;use_highways;use_trails\n" + self.GOOD)

    def test_a_table_with_another_sha256_count_or_states_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "estados.csv"
            copy.write_text(STATE_HEADER + self.GOOD, encoding="utf-8")
            sha256 = hashlib.sha256(copy.read_bytes()).hexdigest()
            with mock.patch.object(config, "MOTORCYCLE_STATE_WEIGHTS_COPY", copy):
                with self.assertRaisesRegex(RuntimeError, "mudou"):
                    municipalities.load_state_weights({"GO"})
                with mock.patch.object(config, "MOTORCYCLE_STATE_WEIGHTS_SHA256", sha256):
                    with self.assertRaisesRegex(RuntimeError, "1 UFs"):
                        municipalities.load_state_weights({"GO"})
                    with mock.patch.object(config, "MOTORCYCLE_STATE_WEIGHTS_ROWS", 1):
                        self.assertEqual({"GO": weight(0.8, 0.4)}, municipalities.load_state_weights({"GO"}))
                        with self.assertRaisesRegex(RuntimeError, "cada UF da malha \\(DF\\)"):
                            municipalities.load_state_weights({"GO", "DF"})
                        with self.assertRaisesRegex(RuntimeError, "cada UF da malha"):
                            municipalities.load_state_weights({"DF"})
            with mock.patch.object(config, "MOTORCYCLE_STATE_WEIGHTS_COPY", Path(tmp) / "falta.csv"):
                with self.assertRaisesRegex(RuntimeError, "não abre"):
                    municipalities.load_state_weights({"GO"})


class MeshProblemTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(config, "MUNICIPALITY_COUNT", 27)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_one_per_state_passes(self):
        self.assertIsNone(municipalities.mesh_problem(mesh_body(one_per_state())))

    def test_an_error_page_is_refused(self):
        self.assertIn("não é JSON", municipalities.mesh_problem(b"<html>erro</html>"))
        self.assertIn("features", municipalities.mesh_problem(b'{"erro": 1}'))

    def test_another_count_is_refused(self):
        self.assertIn("26 municípios", municipalities.mesh_problem(mesh_body(one_per_state()[:-1])))

    def test_a_state_without_municipalities_is_refused(self):
        codes = one_per_state()
        codes[-1] = codes[0][:2] + "00002"
        self.assertIn("faltam as UFs", municipalities.mesh_problem(mesh_body(codes)))

    def test_unexpected_or_repeated_code_is_refused(self):
        self.assertIn("inesperado", municipalities.mesh_problem(mesh_body(["9900001"])))
        self.assertIn("inesperado", municipalities.mesh_problem(mesh_body(["52"])))  # a malha das UFs, por engano
        self.assertIn("repetido", municipalities.mesh_problem(mesh_body(["5200050", "5200050"])))

    def test_a_municipality_without_polygon_is_refused(self):
        self.assertIn("sem polígono", municipalities.mesh_problem(mesh_body(["5200050"], {"type": "Point"})))


class LoadMeshTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.copy = Path(self._tmp.name) / "ibge_malha_municipios.json.gz"
        self.body = mesh_body(one_per_state())
        self.copy.write_bytes(gzip.compress(self.body))
        for name, value in (("IBGE_MUNICIPAL_MESH_COPY", self.copy), ("MUNICIPALITY_COUNT", 27),
                            ("IBGE_MUNICIPAL_MESH_COPY_SHA256", hashlib.sha256(self.body).hexdigest())):
            patcher = mock.patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_copy_that_matches_is_loaded_without_network(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("a geração não baixa a malha")):
            self.assertEqual(27, len(municipalities.load_mesh()))

    def test_a_changed_copy_is_refused(self):
        with mock.patch.object(config, "IBGE_MUNICIPAL_MESH_COPY_SHA256", "0" * 64):
            with self.assertRaisesRegex(RuntimeError, "mudou"):
                municipalities.load_mesh()

    def test_a_copy_that_is_not_gzip_or_is_missing_is_refused(self):
        self.copy.write_bytes(self.body)
        with self.assertRaisesRegex(RuntimeError, "não abre"):
            municipalities.load_mesh()
        self.copy.unlink()
        with self.assertRaisesRegex(RuntimeError, "não abre"):
            municipalities.load_mesh()


class ParseWeightsTest(unittest.TestCase):
    CODES = {"5200050", "5208707"}

    def parse(self, rows):
        return municipalities.parse_weights(HEADER + rows, self.CODES)

    def test_reads_the_weight_and_where_it_came_from(self):
        out = self.parse(f"5208707;0.8;0.2;{OLD};3.9.1;2026-11-14\n5200050;0.6;0.4;{NEW};3.9.1;2026-11-15\n")
        self.assertEqual({"5208707": weight(0.8, 0.2), "5200050": weight(tiles=NEW, date="2026-11-15")}, out)

    def test_problems_are_refused(self):
        good = f"5208707;0.8;0.2;{OLD};3.9.1;2026-11-14\n"
        cases = {
            "cabeçalho": None,
            "colunas": "5208707;0.8;0.2\n",
            "repetido": good + good,
            "não está na malha": f"5299999;0.8;0.2;{OLD};3.9.1;2026-11-14\n",
            "peso fora": f"5208707;1.2;0.2;{OLD};3.9.1;2026-11-14\n",
            "peso fora ": f"5208707;0.85;0.2;{OLD};3.9.1;2026-11-14\n",
            "peso fora  ": f"5208707;;0.2;{OLD};3.9.1;2026-11-14\n",
            "acima do teto": f"5208707;0.6;0.6;{OLD};3.9.1;2026-11-14\n",
            "fora do formato": "5208707;0.8;0.2;2026-11-09;3.9.1;2026-11-14\n",
            "fora do formato ": f"5208707;0.8;0.2;{OLD};3.9;2026-11-14\n",
            "fora do formato  ": f"5208707;0.8;0.2;{OLD};3.9.1;14/11/2026\n",
        }
        for message, rows in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message.strip()):
                if rows is None:
                    municipalities.parse_weights("codigo_ibge;use_highways;use_trails\n", self.CODES)
                else:
                    self.parse(rows)

    def test_a_table_with_another_sha256_or_count_is_refused(self):
        mesh = {"5200050": SQUARE, "5208707": SQUARE}
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "pesos.csv"
            copy.write_text(HEADER + f"5208707;0.8;0.2;{OLD};3.9.1;2026-11-14\n", encoding="utf-8")
            sha256 = hashlib.sha256(copy.read_bytes()).hexdigest()
            with mock.patch.object(config, "MOTORCYCLE_WEIGHTS_COPY", copy):
                with mock.patch.object(config, "MOTORCYCLE_WEIGHTS_SHA256", "0" * 64):
                    with self.assertRaisesRegex(RuntimeError, "mudou"):
                        municipalities.load_weights(mesh)
                with mock.patch.object(config, "MOTORCYCLE_WEIGHTS_SHA256", sha256):
                    with self.assertRaisesRegex(RuntimeError, "1 municípios"):
                        municipalities.load_weights(mesh)
                    with mock.patch.object(config, "MOTORCYCLE_WEIGHTS_ROWS", 1):
                        self.assertEqual(["5208707"], list(municipalities.load_weights(mesh)))
                    copy.write_text(HEADER + "5299999;0.8;0.2\n", encoding="utf-8")
                with mock.patch.object(config, "MOTORCYCLE_WEIGHTS_SHA256", hashlib.sha256(copy.read_bytes()).hexdigest()):
                    with self.assertRaisesRegex(RuntimeError, "não serve"):
                        municipalities.load_weights(mesh)


class StateDocumentTest(unittest.TestCase):
    def setUp(self):
        self.inputs = small_inputs()
        self.states = municipalities.by_state(self.inputs)

    def test_split_by_the_first_two_digits_of_the_code(self):
        self.assertEqual(["5200050", "5200100", "5208707"], self.states["GO"])
        self.assertEqual(["5300108"], self.states["DF"])
        self.assertEqual([], self.states["SP"])
        self.assertEqual("GO", municipalities.state_of("5208707"))
        self.assertIsNone(municipalities.state_of("9900001"))

    def test_document_has_only_the_municipalities_of_the_state(self):
        doc = municipalities.state_document("GO", self.states["GO"], self.inputs)
        self.assertEqual("FeatureCollection", doc["type"])
        self.assertEqual(["5200050", "5200100", "5208707"], [f["properties"]["codarea"] for f in doc["features"]])
        self.assertTrue(all(f["type"] == "Feature" and f["geometry"] == SQUARE for f in doc["features"]))

    def test_each_municipality_has_its_weight_and_its_origin(self):
        doc = municipalities.state_document("GO", self.states["GO"], self.inputs)
        by_code = {f["properties"]["codarea"]: f["properties"] for f in doc["features"]}
        self.assertEqual({"codarea": "5208707", "use_highways": 0.8, "use_trails": 0.2, "tiles_build_id": OLD,
                          "valhalla_version": "3.9.1", "date": "2026-11-14"}, by_code["5208707"])
        self.assertEqual((NEW, "2026-11-15"), (by_code["5200050"]["tiles_build_id"], by_code["5200050"]["date"]))

    def test_a_municipality_without_weight_has_nulls(self):
        doc = municipalities.state_document("GO", self.states["GO"], self.inputs)
        properties = doc["features"][1]["properties"]
        self.assertEqual({"codarea": "5200100", "use_highways": None, "use_trails": None, "tiles_build_id": None,
                          "valhalla_version": None, "date": None}, properties)

    def test_header_says_the_costing_and_the_generations_of_the_state(self):
        header = municipalities.state_document("GO", self.states["GO"], self.inputs)["andorinha"]
        self.assertEqual((1, "GO"), (header["format_version"], header["state"]))
        self.assertEqual({"costing": "motorcycle", "use_tolls": 1.0, "count": 2, "generations": [
            {"tiles_build_id": OLD, "valhalla_version": "3.9.1", "date": "2026-11-14", "count": 1},
            {"tiles_build_id": NEW, "valhalla_version": "3.9.1", "date": "2026-11-15", "count": 1}]}, header["weights"])

    def test_the_document_does_not_carry_the_generation_being_built(self):
        """Sem `build_id` nem data da geração: a mesma malha e a mesma tabela dão o mesmo arquivo toda semana."""
        doc = municipalities.state_document("DF", self.states["DF"], self.inputs)
        self.assertEqual({"type", "andorinha", "features"}, set(doc))
        self.assertEqual({"format_version", "state", "weights", "state_weights"}, set(doc["andorinha"]))

    def test_header_has_the_weight_of_the_state_and_its_origin(self):
        go = municipalities.state_document("GO", self.states["GO"], self.inputs)["andorinha"]
        self.assertEqual({"use_highways": 0.8, "use_trails": 0.4, "tiles_build_id": OLD, "valhalla_version": "3.9.1",
                          "date": "2026-11-14"}, go["state_weights"])
        df = municipalities.state_document("DF", self.states["DF"], self.inputs)["andorinha"]
        self.assertEqual((0.9, NEW, "2026-11-15"), (df["state_weights"]["use_highways"],
                                                    df["state_weights"]["tiles_build_id"], df["state_weights"]["date"]))

    def test_the_state_weight_only_adds_to_the_header(self):
        """O que já havia no arquivo (versão do formato, pesos dos municípios, contornos) sai igual."""
        with_state = municipalities.state_document("GO", self.states["GO"], self.inputs)
        without = municipalities.state_document("GO", self.states["GO"], Inputs(self.inputs.mesh, self.inputs.weights))
        self.assertIsNone(without["andorinha"]["state_weights"])
        self.assertEqual(1, with_state["andorinha"]["format_version"])
        self.assertEqual(without["features"], with_state["features"])
        self.assertEqual(without["andorinha"]["weights"], with_state["andorinha"]["weights"])


@unittest.skipIf(zstandard is None, "zstandard não instalado")
class StateFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)
        self.inputs = small_inputs()

    def test_one_file_per_state_with_size_and_sha256(self):
        files = municipalities.generate(self.inputs, self.out)
        self.assertEqual(sorted(config.STATES), sorted(files))
        go = files["GO"]
        path = self.out / "andorinha-municipios-GO.geojson.zst"
        body = zstandard.ZstdDecompressor().decompress(path.read_bytes())
        self.assertEqual(("andorinha-municipios-GO.geojson.zst", path.stat().st_size,
                          hashlib.sha256(path.read_bytes()).hexdigest(), len(body), 3, 2),
                         (go.file, go.size, go.sha256, go.bytes_unpacked, go.municipalities, go.weights))
        self.assertEqual(json.loads(body)["andorinha"]["state_weights"], go.state_weights)
        self.assertEqual(0.8, go.state_weights["use_highways"])
        self.assertIsNone(files["SP"].state_weights)  # sem peso nas entradas deste teste
        self.assertEqual(municipalities.state_document("GO", ["5200050", "5200100", "5208707"], self.inputs),
                         json.loads(body))

    def test_the_file_is_one_plain_zstd_frame_not_a_tar(self):
        municipalities.generate(self.inputs, self.out)
        data = (self.out / "andorinha-municipios-DF.geojson.zst").read_bytes()
        parameters = zstandard.get_frame_parameters(data)
        self.assertLessEqual(parameters.window_size, 8 * 1024 * 1024)
        body = zstandard.ZstdDecompressor().decompress(data)
        self.assertEqual(parameters.content_size, len(body))
        self.assertTrue(body.startswith(b'{"type":"FeatureCollection"'))
        self.assertFalse(tarfile.is_tarfile(self.out / "andorinha-municipios-DF.geojson.zst"))

    def test_same_inputs_same_bytes(self):
        a = municipalities.generate(self.inputs, self.out)
        other = self.out / "outra"
        b = municipalities.generate(small_inputs(), other)
        self.assertEqual({s: f.sha256 for s, f in a.items()}, {s: f.sha256 for s, f in b.items()})

    def test_index_roundtrip_and_cleaning(self):
        files = municipalities.generate(self.inputs, self.out)
        self.assertEqual(files, municipalities.load_index(self.out))
        (self.out / "andorinha-GO.tar.zst").write_text("x")
        (self.out / "andorinha-municipios-XX.geojson.zst").write_text("de outra geração")
        municipalities.generate(self.inputs, self.out)
        self.assertFalse((self.out / "andorinha-municipios-XX.geojson.zst").exists())
        self.assertTrue((self.out / "andorinha-GO.tar.zst").exists())  # os pacotes de tiles não são daqui
        municipalities.clean_output(self.out)
        self.assertEqual(["andorinha-GO.tar.zst"], sorted(p.name for p in self.out.iterdir()))

    def test_tile_packages_are_untouched(self):
        """Os contornos e os pesos não entram no .tar.zst da UF: um leitor que só aceita tiles lá dentro segue lendo."""
        from andorinha.packing import write_package
        from andorinha.tiles import Tile
        folder = self.out / "tiles"
        t = Tile(2, 426768)
        (folder / t.path()).parent.mkdir(parents=True)
        (folder / t.path()).write_bytes(b"tile")
        municipalities.generate(self.inputs, self.out)
        write_package("DF", [t], folder, self.out / "andorinha-DF.tar.zst")
        with open(self.out / "andorinha-DF.tar.zst", "rb") as f, zstandard.ZstdDecompressor().stream_reader(f) as stream:
            with tarfile.open(fileobj=stream, mode="r|") as tar:
                self.assertEqual([t.path()], [m.name for m in tar])


@unittest.skipIf(zstandard is None, "zstandard não instalado")
class RealStateFilesTest(unittest.TestCase):
    """Com as cópias de verdade: os 27 arquivos, todos os municípios e os tamanhos medidos."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.out = Path(cls._tmp.name)
        cls.files = municipalities.generate(municipalities.check(), cls.out)

    def test_all_municipalities_and_weights_are_in_some_state(self):
        self.assertEqual(27, len(self.files))
        self.assertEqual(config.MUNICIPALITY_COUNT, sum(f.municipalities for f in self.files.values()))
        self.assertEqual(config.MOTORCYCLE_WEIGHTS_ROWS, sum(f.weights for f in self.files.values()))
        self.assertEqual((853, 853), (self.files["MG"].municipalities, self.files["MG"].weights))
        self.assertEqual((62, 58), (self.files["AM"].municipalities, self.files["AM"].weights))

    def test_sizes_stay_small(self):
        """Medido em 2026-11-14: 1,97 MB no país, o maior (MG) com 285 kB."""
        self.assertLess(sum(f.size for f in self.files.values()), 2_500_000)
        self.assertEqual("MG", max(self.files.values(), key=lambda f: f.size).state)
        self.assertLess(self.files["MG"].size, 400_000)

    def test_a_state_file_opens_as_geojson_with_the_weights(self):
        body = zstandard.ZstdDecompressor().decompress((self.out / self.files["GO"].file).read_bytes())
        doc = json.loads(body)
        self.assertEqual(246, len(doc["features"]))
        goiania = [f for f in doc["features"] if f["properties"]["codarea"] == "5208707"][0]
        self.assertIn(goiania["geometry"]["type"], ("Polygon", "MultiPolygon"))
        self.assertEqual(OLD, goiania["properties"]["tiles_build_id"])
        self.assertEqual({"use_highways": 0.8, "use_trails": 0.4, "tiles_build_id": OLD, "valhalla_version": "3.9.1",
                          "date": "2026-11-14"}, doc["andorinha"]["state_weights"])
        lon, lat = goiania["geometry"]["coordinates"][0][0][:2]  # GeoJSON: longitude primeiro
        self.assertTrue(-51 < lon < -48 and -18 < lat < -15, (lon, lat))

    def test_every_state_file_has_the_state_weight(self):
        self.assertTrue(all(f.state_weights for f in self.files.values()))
        self.assertEqual([{"tiles_build_id": OLD, "valhalla_version": "3.9.1", "date": "2026-11-14", "count": 27}],
                         municipalities.all_state_generations(self.files))


class GenerationNotesTest(unittest.TestCase):
    def files(self):
        from andorinha.municipalities import MunicipalityFile
        old = {"tiles_build_id": OLD, "valhalla_version": "3.9.1", "date": "2026-11-14", "count": 2}
        new = {"tiles_build_id": NEW, "valhalla_version": "3.9.1", "date": "2026-11-15", "count": 1}
        return {"GO": MunicipalityFile("GO", "g", 1, "a" * 64, 2, 3, 3, [old, new]),
                "DF": MunicipalityFile("DF", "d", 1, "b" * 64, 2, 1, 1, [dict(old, count=1)])}

    def with_state_weights(self, tiles=OLD, version="3.9.1"):
        files = {"DF": self.files()["DF"]}
        files["DF"].state_weights = {"use_highways": 0.9, "use_trails": 0.4, "tiles_build_id": tiles,
                                     "valhalla_version": version, "date": "2026-11-14"}
        return files

    def test_state_weights_of_another_generation_are_a_note_too(self):
        notes = municipalities.generation_notes(self.with_state_weights(), NEW, "3.9.1")
        self.assertEqual(2, len(notes))  # o dos municípios e o dos estados
        self.assertIn("pesos de 1 estados calculados com os tiles da geração " + OLD, notes[1])

    def test_state_weights_of_this_generation_have_no_note(self):
        self.assertEqual([], municipalities.generation_notes(self.with_state_weights(), OLD, "3.9.1"))

    def test_state_weights_of_another_valhalla_are_noted(self):
        notes = municipalities.generation_notes(self.with_state_weights(version="3.8.0"), OLD, "3.9.1")
        self.assertEqual(1, len(notes))
        self.assertIn("estados calculados com o Valhalla 3.8.0", notes[0])

    def test_country_summary_adds_the_states(self):
        self.assertEqual([{"tiles_build_id": OLD, "valhalla_version": "3.9.1", "date": "2026-11-14", "count": 3},
                          {"tiles_build_id": NEW, "valhalla_version": "3.9.1", "date": "2026-11-15", "count": 1}],
                         municipalities.all_generations(self.files()))

    def test_weights_of_another_generation_are_a_note_not_an_error(self):
        notes = municipalities.generation_notes(self.files(), NEW, "3.9.1")
        self.assertEqual(1, len(notes))
        self.assertIn(OLD, notes[0])
        self.assertIn("3 municípios (2 UFs)", notes[0])

    def test_weights_of_this_generation_have_no_note(self):
        files = {"DF": self.files()["DF"]}
        self.assertEqual([], municipalities.generation_notes(files, OLD, "3.9.1"))
        self.assertTrue(municipalities.same_generation(files["DF"].generations, OLD))
        self.assertFalse(municipalities.same_generation(files["DF"].generations, NEW))

    def test_another_valhalla_is_noted_too(self):
        notes = municipalities.generation_notes({"DF": self.files()["DF"]}, OLD, "3.10.0")
        self.assertEqual(1, len(notes))
        self.assertIn("Valhalla 3.9.1", notes[0])


if __name__ == "__main__":
    unittest.main()
