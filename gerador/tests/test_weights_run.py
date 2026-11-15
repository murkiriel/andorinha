"""A execução do estudo de um lote (andorinha/weights_run.py) e a linha de comando (scripts/weights.py), com um estudo
falso no lugar do Valhalla."""
import contextlib
import hashlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

GENERATOR_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GENERATOR_DIR))
from andorinha import config, municipalities, weights_run, weights_tables  # noqa: E402
from andorinha.weights_study import Outcome  # noqa: E402

try:
    import zstandard
except ImportError:  # pragma: no cover
    zstandard = None

BUILD_ID = "2026-11-16-0a1b2c3d"


class FakePool:
    """Um Pool que não abre processo nenhum: devolve um resultado combinado para cada município."""

    instances = []

    def __init__(self, tiles_dir, workers):
        self.tiles_dir, self.workers = tiles_dir, workers
        self.asked = []
        self.deadline = None
        FakePool.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def municipalities(self, items, deadline=None, on_result=None):
        self.deadline = deadline
        out = []
        for code, geometry in items:
            self.asked.append(code)
            outcome = Outcome(code, None if code in self.no_sample else (0.7, 0.3), 1024, 1000, 200, 1.5,
                              center=(0.6, 0.4))
            out.append(outcome)
            if on_result:
                on_result(outcome)
        return out

    no_sample = ()

    def measurer(self, deadline=None):
        return lambda *args: []


def no_valhalla(tiles_dir):
    raise RuntimeError("sem o Valhalla neste teste")


class RunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mesh = municipalities.load_mesh()

    def setUp(self):
        FakePool.instances = []
        FakePool.no_sample = ()
        self.tmp = Path(tempfile.mkdtemp(prefix="andorinha-teste-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "saida"

    def plan(self, **kwargs):
        defaults = dict(tiles_dir=self.tmp / "tiles", build_id=BUILD_ID, out=self.out, date="2026-11-16", workers=3,
                        batch=10, states=0, minutes=60)
        defaults.update(kwargs)
        return weights_run.Plan(**defaults)

    def run_plan(self, plan, **kwargs):
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            result = weights_run.run(plan, pool_factory=FakePool, **kwargs)
        return result, log.getvalue()

    def test_the_batch_is_the_rotation_one_and_the_tables_come_out_updated(self):
        result, log = self.run_plan(self.plan())
        expected = weights_tables.select_batch(
            municipalities.parse_weights(config.MOTORCYCLE_WEIGHTS_COPY.read_text(encoding="utf-8"), self.mesh),
            self.mesh, 10)
        self.assertEqual(expected, result.selected_codes)
        self.assertEqual(expected, FakePool.instances[0].asked)
        self.assertTrue(result.changed)
        table = municipalities.parse_weights((self.out / municipalities.NEXT_WEIGHTS_FILE).read_text(encoding="utf-8"),
                                             self.mesh)
        for code in expected:
            self.assertEqual((0.7, 0.3, BUILD_ID, config.VALHALLA_VERSION, "2026-11-16"),
                             (table[code].use_highways, table[code].use_trails, table[code].tiles_build_id,
                              table[code].valhalla_version, table[code].date))
        self.assertEqual(config.MOTORCYCLE_WEIGHTS_ROWS + 6, len(table))  # os seis sem linha entraram no lote
        self.assertIn("10 municípios", log)

    def test_the_rest_of_the_table_is_untouched(self):
        self.run_plan(self.plan())
        seed = config.MOTORCYCLE_WEIGHTS_COPY.read_text(encoding="utf-8").splitlines()
        new = (self.out / municipalities.NEXT_WEIGHTS_FILE).read_text(encoding="utf-8").splitlines()
        changed = [line for line in new if line not in set(seed)]
        self.assertEqual(10, len(changed))
        self.assertEqual(len(seed), len(new) - 6)
        self.assertEqual((self.out / municipalities.NEXT_STATE_WEIGHTS_FILE).read_text(encoding="utf-8"),
                         config.MOTORCYCLE_STATE_WEIGHTS_COPY.read_text(encoding="utf-8"))

    def test_the_manifest_describes_the_next_tables(self):
        self.run_plan(self.plan())
        manifest = json.loads((self.out / municipalities.NEXT_MANIFEST_FILE).read_text(encoding="utf-8"))
        weights = (self.out / municipalities.NEXT_WEIGHTS_FILE).read_text(encoding="utf-8")
        self.assertEqual(weights_tables.sha256_of(weights), manifest["weights"]["sha256"])
        self.assertEqual(config.MOTORCYCLE_WEIGHTS_ROWS + 6, manifest["weights"]["count"])
        self.assertEqual((BUILD_ID, config.VALHALLA_VERSION, "2026-11-16"),
                         (manifest["build_id"], manifest["valhalla_version"], manifest["study"]["date"]))
        self.assertEqual(10, manifest["study"]["municipalities"]["updated"])
        # e o que o gerador da próxima geração leria aceita o conjunto
        self.assertEqual(3, len([n for n in self.out.iterdir() if ".proximo." in n.name or n.name == "pesos_proximo.json"]))

    def test_the_normal_names_are_never_written_by_the_study(self):
        self.run_plan(self.plan())
        self.assertFalse((self.out / municipalities.WEIGHTS_FILE).exists())
        self.assertFalse((self.out / municipalities.STATE_WEIGHTS_FILE).exists())

    def test_the_summary_says_what_happened(self):
        FakePool.no_sample = ("1300201",)
        result, _ = self.run_plan(self.plan())
        summary = json.loads((self.out / weights_run.SUMMARY_FILE).read_text(encoding="utf-8"))
        self.assertEqual(BUILD_ID, summary["build_id"])
        self.assertEqual(10, summary["municipalities"]["selected"])
        self.assertEqual(9, summary["municipalities"]["updated"])
        self.assertEqual(["1300201"], summary["municipalities"]["without_weight"])
        self.assertEqual(10, len(summary["municipalities"]["results"]))
        self.assertEqual("a cópia do repositório", summary["started_from"])

    def test_nothing_studied_writes_no_tables(self):
        FakePool.no_sample = tuple(weights_tables.select_batch(municipalities.load_weights(self.mesh), self.mesh, 3))
        result, _ = self.run_plan(self.plan(batch=3))
        self.assertFalse(result.changed)
        self.assertFalse((self.out / municipalities.NEXT_WEIGHTS_FILE).exists())
        self.assertFalse((self.out / municipalities.NEXT_MANIFEST_FILE).exists())
        self.assertTrue((self.out / weights_run.SUMMARY_FILE).exists())

    def test_by_hand_codes_replace_the_rotation(self):
        result, _ = self.run_plan(self.plan(only_codes=["5300108", "5208707"]))
        self.assertEqual(["5300108", "5208707"], FakePool.instances[0].asked)
        self.assertEqual(2, len(result.municipalities))

    def test_a_code_outside_the_mesh_is_refused(self):
        with self.assertRaisesRegex(weights_tables.TableError, "9999999"):
            self.run_plan(self.plan(only_codes=["9999999"]))

    def test_the_deadline_leaves_room_for_the_state(self):
        _, _ = self.run_plan(self.plan(only_states=["DF"], minutes=100), actor_factory=no_valhalla)
        # o estado falha aqui (sem Valhalla): o importante é o prazo dos municípios, 70% do orçamento
        pool = FakePool.instances[0]
        self.assertIsNotNone(pool.deadline)
        _, _ = self.run_plan(self.plan(minutes=100))
        no_state = FakePool.instances[1].deadline
        self.assertGreater(no_state, pool.deadline)

    def test_a_state_that_fails_is_skipped_and_the_municipalities_still_count(self):
        result, log = self.run_plan(self.plan(only_states=["DF"]), actor_factory=no_valhalla)
        self.assertIn("DF", result.skipped_states)
        self.assertEqual({"DF"}, set(result.skipped_states))
        self.assertTrue(result.changed)
        self.assertIn("o estado DF não entrou", log)
        self.assertEqual((self.out / municipalities.NEXT_STATE_WEIGHTS_FILE).read_text(encoding="utf-8"),
                         config.MOTORCYCLE_STATE_WEIGHTS_COPY.read_text(encoding="utf-8"))

    def test_start_tables_can_come_from_a_folder(self):
        start = self.tmp / "partida"
        start.mkdir()
        texts = weights_tables.seed_tables(self.mesh, {municipalities.state_of(c) for c in self.mesh})[0]
        text = texts.weights.replace("1100015;0.6;0.4;2026-11-09-c17dff60;3.9.1;2026-11-14",
                                     "1100015;0.6;0.4;2026-11-09-c17dff60;3.9.1;2026-12-31")
        (start / municipalities.WEIGHTS_FILE).write_text(text, encoding="utf-8", newline="\n")
        (start / municipalities.STATE_WEIGHTS_FILE).write_text(texts.state_weights, encoding="utf-8", newline="\n")
        result, _ = self.run_plan(self.plan(tables_dir=start, batch=8))
        self.assertNotIn("1100015", result.selected_codes)  # o recém-refeito sai da frente da fila

    def test_start_tables_that_do_not_serve_stop_the_study(self):
        start = self.tmp / "partida"
        start.mkdir()
        (start / municipalities.WEIGHTS_FILE).write_text("lixo\n", encoding="utf-8")
        (start / municipalities.STATE_WEIGHTS_FILE).write_text("lixo\n", encoding="utf-8")
        with self.assertRaises(weights_tables.TableError):
            self.run_plan(self.plan(tables_dir=start))


@unittest.skipIf(zstandard is None, "zstandard não instalado")
class PrepareFromReleaseTest(unittest.TestCase):
    """O job weights baixa a release recém-publicada pelas URLs públicas: nada de artefato do Actions."""

    def setUp(self):
        import io as _io
        import tarfile

        self.tmp = Path(tempfile.mkdtemp(prefix="andorinha-teste-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.url = config.RELEASE_URL.format(tag=BUILD_ID)
        self.files = {}
        self.cat = {"build_id": BUILD_ID, "base": {}, "states": {}, "municipalities": {}}
        for name, tiles in (("base", ["0/000/001.gph"]), ("DF", ["1/000/002.gph", "2/000/003.gph"]),
                            ("GO", ["1/000/004.gph", "2/000/003.gph"])):
            plain = _io.BytesIO()
            with tarfile.open(fileobj=plain, mode="w") as tar:
                for path in tiles:
                    info = tarfile.TarInfo(path)
                    info.size = 3
                    tar.addfile(info, _io.BytesIO(b"abc"))
            data = zstandard.ZstdCompressor().compress(plain.getvalue())
            entry = {"file": f"andorinha-{name}.tar.zst", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            self.files[self.url + entry["file"]] = data
            if name == "base":
                self.cat["base"] = entry
            else:
                self.cat["states"][name] = entry
        weights = weights_tables.seed_tables(*(lambda m: (m, {municipalities.state_of(c) for c in m}))(
            municipalities.load_mesh()))[0]
        self.tables = {municipalities.WEIGHTS_FILE: weights.weights.encode(),
                       municipalities.STATE_WEIGHTS_FILE: weights.state_weights.encode()}
        self.cat["municipalities"] = {
            "weights": {"sha256": hashlib.sha256(weights.weights.encode()).hexdigest()},
            "state_weights": {"sha256": hashlib.sha256(weights.state_weights.encode()).hexdigest()}}
        for name, data in self.tables.items():
            self.files[self.url + name] = data
        self.downloaded = []

    def fetch(self, url):
        if url == self.url + "catalogo.json":
            return json.dumps(self.cat)
        return self.files[url].decode("utf-8")

    def download(self, url, dest, identity=None, **kwargs):
        self.downloaded.append(url)
        dest.write_bytes(self.files[url])

    def prepare(self):
        return weights_run.prepare_from_release(BUILD_ID, self.tmp / "trabalho", self.fetch, self.download)

    def test_every_package_goes_into_one_folder_and_the_tables_next_to_it(self):
        tiles, tables, build_id = self.prepare()
        found = sorted(str(p.relative_to(tiles)) for p in tiles.rglob("*.gph"))
        self.assertEqual(["0/000/001.gph", "1/000/002.gph", "1/000/004.gph", "2/000/003.gph"], found)
        self.assertEqual(BUILD_ID, build_id)
        self.assertEqual(sorted(self.tables), sorted(p.name for p in tables.iterdir()))
        self.assertEqual([self.url + "andorinha-base.tar.zst", self.url + "andorinha-DF.tar.zst",
                          self.url + "andorinha-GO.tar.zst"], self.downloaded)
        self.assertEqual([], list((self.tmp / "trabalho" / "pacotes").iterdir()))  # só os tiles ficam no disco

    def test_a_package_that_does_not_match_the_catalog_stops_the_study(self):
        self.cat["states"]["DF"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "sha256"):
            self.prepare()

    def test_a_table_that_does_not_match_the_catalog_stops_the_study(self):
        self.cat["municipalities"]["weights"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "pesos_moto_municipios.csv"):
            self.prepare()

    def test_a_catalog_of_another_generation_stops_the_study(self):
        self.cat["build_id"] = "2026-11-09-c17dff60"
        with self.assertRaisesRegex(RuntimeError, "geração"):
            self.prepare()


def load_script():
    spec = importlib.util.spec_from_file_location("weights_script", GENERATOR_DIR / "scripts" / "weights.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CommandLineTest(unittest.TestCase):
    def setUp(self):
        self.script = load_script()
        self.tmp = Path(tempfile.mkdtemp(prefix="andorinha-teste-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_main(self, argv):
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            code = self.script.main(argv)
        return code, log.getvalue()

    def test_study_without_release_asks_for_tiles_and_build_id(self):
        with self.assertRaises(SystemExit):
            self.run_main(["study", "--out", str(self.tmp)])

    def test_study_passes_the_options_to_the_plan(self):
        plans = []

        class Result:
            changed = True

        def fake_run(plan):
            plans.append(plan)
            return Result()

        original = self.script.weights_run.run
        self.script.weights_run.run = fake_run
        try:
            code, _ = self.run_main(["study", "--tiles", str(self.tmp / "t"), "--build-id", BUILD_ID, "--out",
                                     str(self.tmp / "o"), "--municipalities", "5300108, 5208707", "--states", "0",
                                     "--workers", "4", "--date", "2026-11-16", "--minutes", "30", "--state", "DF"])
        finally:
            self.script.weights_run.run = original
        self.assertEqual(0, code)
        plan = plans[0]
        self.assertEqual((["5300108", "5208707"], ["DF"], 4, 0, "2026-11-16", 30.0),
                         (plan.only_codes, plan.only_states, plan.workers, plan.states, plan.date, plan.minutes))
        self.assertEqual(BUILD_ID, plan.build_id)

    def test_study_exits_one_when_nothing_changed(self):
        class Result:
            changed = False

        original = self.script.weights_run.run
        self.script.weights_run.run = lambda plan: Result()
        try:
            code, _ = self.run_main(["study", "--tiles", str(self.tmp), "--build-id", BUILD_ID, "--out",
                                     str(self.tmp / "o")])
        finally:
            self.script.weights_run.run = original
        self.assertEqual(1, code)

    def test_study_with_a_release_downloads_it_first(self):
        calls = []

        class Result:
            changed = True

        original = (self.script.weights_run.prepare_from_release, self.script.weights_run.run)
        self.script.weights_run.prepare_from_release = lambda tag, work: calls.append((tag, work)) or (
            self.tmp / "t", self.tmp / "tab", tag)
        self.script.weights_run.run = lambda plan: calls.append(plan) or Result()
        try:
            code, _ = self.run_main(["study", "--release", BUILD_ID, "--work", str(self.tmp / "w"), "--out",
                                     str(self.tmp / "o")])
        finally:
            self.script.weights_run.prepare_from_release, self.script.weights_run.run = original
        self.assertEqual(0, code)
        self.assertEqual((BUILD_ID, self.tmp / "w"), calls[0])
        self.assertEqual((self.tmp / "t", self.tmp / "tab", BUILD_ID), (calls[1].tiles_dir, calls[1].tables_dir,
                                                                        calls[1].build_id))

    def test_upload_with_a_missing_result_exits_one_and_says_why(self):
        original = self.script.publishing.gh_real
        self.script.publishing.gh_real = lambda args: (1, "", "não deveria ser chamado")
        try:
            code, log = self.run_main(["upload", "--tag", BUILD_ID, "--out", str(self.tmp)])
        finally:
            self.script.publishing.gh_real = original
        self.assertEqual(1, code)
        self.assertIn("NÃO subiu", log)


if __name__ == "__main__":
    unittest.main()
