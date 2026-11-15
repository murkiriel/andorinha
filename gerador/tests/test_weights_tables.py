"""As tabelas de pesos que o gerador atualiza (andorinha/weights_tables.py): o formato escrito de volta, a rotação dos
lotes, a mescla dos resultados, a tabela anterior vinda da release (e o que acontece quando ela não serve) e a
aplicação da tabela nova numa geração."""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

GENERATOR_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GENERATOR_DIR))
from andorinha import catalog, config, municipalities, net, publishing, weights_tables  # noqa: E402
from andorinha.municipalities import Weight  # noqa: E402
from andorinha.weights_study import Outcome  # noqa: E402
from tests import contract_check  # noqa: E402

OLD = "2026-11-09-c17dff60"
RELEASE_ID = "2026-11-16-0a1b2c3d"
NEXT_ID = "2026-11-23-4e5f6a7b"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Shared(unittest.TestCase):
    """A malha e a semente, lidas uma vez."""

    @classmethod
    def setUpClass(cls):
        cls.mesh = municipalities.load_mesh()
        cls.states = {municipalities.state_of(code) for code in cls.mesh}
        cls.seed_texts, cls.seed_weights, cls.seed_states = weights_tables.seed_tables(cls.mesh, cls.states)


def changed_tables(weights, state_weights, date="2026-11-16", build_id=RELEASE_ID, codes=("5300108",),
                   states=("DF",), value=(0.7, 0.3)):
    """As tabelas da semente com `codes` e `states` refeitos."""
    new = dict(weights)
    new_states = dict(state_weights)
    for code in codes:
        new[code] = Weight(value[0], value[1], build_id, "3.9.1", date)
    for state in states:
        new_states[state] = Weight(value[0], value[1], build_id, "3.9.1", date)
    return weights_tables.serialize_weights(new), weights_tables.serialize_state_weights(new_states)


class SerializationTest(Shared):
    def test_the_seed_is_written_back_byte_for_byte(self):
        """Ler e escrever de volta dá o mesmo texto: o sha256 de uma tabela que o gerador atualizou só muda pelo que
        ele atualizou."""
        self.assertEqual(self.seed_texts.weights, weights_tables.serialize_weights(self.seed_weights))
        self.assertEqual(self.seed_texts.state_weights, weights_tables.serialize_state_weights(self.seed_states))

    def test_the_written_table_is_read_back_equal(self):
        weights, states = changed_tables(self.seed_weights, self.seed_states)
        self.assertEqual(Weight(0.7, 0.3, RELEASE_ID, "3.9.1", "2026-11-16"),
                         municipalities.parse_weights(weights, self.mesh)["5300108"])
        self.assertEqual(Weight(0.7, 0.3, RELEASE_ID, "3.9.1", "2026-11-16"),
                         municipalities.parse_state_weights(states)["DF"])

    def test_the_rows_are_in_code_order(self):
        lines = weights_tables.serialize_weights(self.seed_weights).splitlines()[1:]
        self.assertEqual(sorted(lines), lines)
        self.assertNotIn("\r", weights_tables.serialize_weights(self.seed_weights))

    def test_digests_of_the_table_texts_are_the_sha256_of_their_bytes(self):
        self.assertEqual((config.MOTORCYCLE_WEIGHTS_SHA256, config.MOTORCYCLE_STATE_WEIGHTS_SHA256),
                         self.seed_texts.digests())


class CheckTablesTest(Shared):
    def test_the_seed_passes(self):
        weights, states = weights_tables.check_tables(self.seed_texts.weights, self.seed_texts.state_weights,
                                                      self.mesh, self.states)
        self.assertEqual((config.MOTORCYCLE_WEIGHTS_ROWS, 27), (len(weights), len(states)))

    def test_a_table_that_loses_rows_is_refused(self):
        lines = self.seed_texts.weights.splitlines(keepends=True)
        with self.assertRaisesRegex(weights_tables.TableError, "não perde linhas"):
            weights_tables.check_tables("".join(lines[:-1]), self.seed_texts.state_weights, self.mesh, self.states)

    def test_a_code_outside_the_mesh_is_refused(self):
        text = self.seed_texts.weights + "9999999;0.6;0.4;" + OLD + ";3.9.1;2026-11-14\n"
        with self.assertRaisesRegex(weights_tables.TableError, "não está na malha"):
            weights_tables.check_tables(text, self.seed_texts.state_weights, self.mesh, self.states)

    def test_trails_above_the_ceiling_are_refused(self):
        text = self.seed_texts.weights.replace("1100015;0.6;0.4", "1100015;0.6;0.6")
        with self.assertRaisesRegex(weights_tables.TableError, "teto"):
            weights_tables.check_tables(text, self.seed_texts.state_weights, self.mesh, self.states)

    def test_a_repeated_municipality_is_refused(self):
        lines = self.seed_texts.weights.splitlines(keepends=True)
        with self.assertRaisesRegex(weights_tables.TableError, "repetido"):
            weights_tables.check_tables("".join(lines + [lines[1]]), self.seed_texts.state_weights, self.mesh,
                                        self.states)

    def test_a_missing_state_is_refused(self):
        lines = self.seed_texts.state_weights.splitlines(keepends=True)
        with self.assertRaisesRegex(weights_tables.TableError, "uma linha para cada UF"):
            weights_tables.check_tables(self.seed_texts.weights, "".join(lines[:-1]), self.mesh, self.states)

    def test_a_wrong_header_is_refused(self):
        with self.assertRaisesRegex(weights_tables.TableError, "cabeçalho"):
            weights_tables.check_tables("codigo;x\n", self.seed_texts.state_weights, self.mesh, self.states)


class RotationTest(Shared):
    def test_the_batch_is_the_oldest_rows_and_ties_go_by_code(self):
        weights = {"5300108": Weight(0.6, 0.4, OLD, "3.9.1", "2026-11-20"),
                   "5208707": Weight(0.6, 0.4, OLD, "3.9.1", "2026-11-14"),
                   "5200050": Weight(0.6, 0.4, OLD, "3.9.1", "2026-11-14"),
                   "5200100": Weight(0.6, 0.4, OLD, "3.9.1", "2026-11-18")}
        codes = ["5300108", "5208707", "5200050", "5200100"]
        self.assertEqual(["5200050", "5208707", "5200100"], weights_tables.select_batch(weights, codes, 3))
        self.assertEqual(codes[:0], weights_tables.select_batch(weights, codes, 0))

    def test_rows_made_with_another_valhalla_come_first(self):
        weights = {"5300108": Weight(0.6, 0.4, OLD, "3.8.0", "2026-11-20"),
                   "5208707": Weight(0.6, 0.4, OLD, "3.9.1", "2026-11-14")}
        self.assertEqual(["5300108", "5208707"], weights_tables.select_batch(weights, weights, 2, "3.9.1"))

    def test_municipalities_without_a_row_come_first(self):
        """Um município novo, ou um que não deu amostra: entra no lote de toda geração, na frente."""
        weights = {"5300108": Weight(0.6, 0.4, OLD, "3.9.1", "2026-11-20")}
        self.assertEqual(["5200050", "5208707", "5300108"],
                         weights_tables.select_batch(weights, ["5208707", "5200050", "5300108"], 3))

    def test_same_table_same_batch(self):
        first = weights_tables.select_batch(self.seed_weights, self.mesh, 600)
        self.assertEqual(first, weights_tables.select_batch(self.seed_weights, self.mesh, 600))
        self.assertEqual(600, len(set(first)))

    def test_the_first_batch_of_the_seed_starts_at_the_lowest_codes(self):
        batch = weights_tables.select_batch(self.seed_weights, self.mesh, 600)
        self.assertEqual("1100015", batch[6])  # depois dos seis sem linha
        self.assertEqual(sorted(batch[6:]), batch[6:])
        never = sorted(code for code in self.mesh if code not in self.seed_weights)
        self.assertEqual(6, len(never))
        self.assertEqual(never, [code for code in batch if code not in self.seed_weights])  # os sem linha vão sempre

    def test_batches_go_all_the_way_around_the_country_in_ten_generations(self):
        """600 por geração, a partir da semente: na décima geração todo município foi refeito pelo menos uma vez."""
        self.assertEqual(10, weights_tables.generations_to_cycle(config.MUNICIPALITY_COUNT, 600))
        table = dict(self.seed_weights)
        redone = {}
        for generation in range(1, 11):
            batch = weights_tables.select_batch(table, self.mesh, 600)
            outcomes = [Outcome(code, (0.6, 0.4)) for code in batch]
            table, _ = weights_tables.merge(table, outcomes, f"2026-11-{9 + generation:02d}-aaaaaaaa", "3.9.1",
                                            f"2026-12-{generation:02d}")
            for code in batch:
                redone.setdefault(code, generation)
        self.assertEqual(set(self.mesh), set(redone))
        self.assertEqual(config.MUNICIPALITY_COUNT, len(table))
        dates = sorted({w.date for w in table.values()})
        self.assertEqual([f"2026-12-{g:02d}" for g in range(1, 11)], dates)

    def test_one_state_per_generation_and_27_to_go_around(self):
        self.assertEqual(27, weights_tables.generations_to_cycle(27, 1))
        table = dict(self.seed_states)
        order = []
        for generation in range(27):
            (state,) = weights_tables.select_states(table, self.states, 1)
            order.append(state)
            table, _ = weights_tables.merge(table, [Outcome(state, (0.6, 0.4))], "x", "3.9.1",
                                            f"2026-12-{generation + 1:02d}" if generation < 9 else f"2027-01-{generation:02d}")
        self.assertEqual(sorted(self.states), sorted(order))
        self.assertEqual("AC", order[0])
        self.assertEqual(order, sorted(order))  # as linhas da semente têm todas o mesmo dia: a ordem é a da sigla

    def test_zero_turns_the_study_off(self):
        self.assertEqual([], weights_tables.select_states(self.seed_states, self.states, 0))
        self.assertEqual(0, weights_tables.generations_to_cycle(5570, 0))


class MergeTest(unittest.TestCase):
    def test_merge_replaces_the_rows_and_stamps_them(self):
        table = {"1": Weight(0.6, 0.4, OLD, "3.9.1", "2026-11-14"), "2": Weight(0.6, 0.4, OLD, "3.9.1", "2026-11-14")}
        merged, updated = weights_tables.merge(table, [Outcome("2", (0.8, 0.3))], RELEASE_ID, "3.9.1", "2026-11-16")
        self.assertEqual(1, updated)
        self.assertEqual(Weight(0.8, 0.3, RELEASE_ID, "3.9.1", "2026-11-16"), merged["2"])
        self.assertEqual(table["1"], merged["1"])
        self.assertEqual(Weight(0.6, 0.4, OLD, "3.9.1", "2026-11-14"), table["2"])  # a original não muda

    def test_an_outcome_without_a_weight_keeps_the_old_row(self):
        table = {"1": Weight(0.6, 0.4, OLD, "3.9.1", "2026-11-14")}
        merged, updated = weights_tables.merge(table, [Outcome("1", None, reason="sem amostra"),
                                                       Outcome("3", None)], RELEASE_ID, "3.9.1", "2026-11-16")
        self.assertEqual((table, 0), (merged, updated))

    def test_a_new_municipality_gets_a_row(self):
        merged, updated = weights_tables.merge({}, [Outcome("7", (0.5, 0.3))], RELEASE_ID, "3.9.1", "2026-11-16")
        self.assertEqual((1, 1), (len(merged), updated))


class PreviousTablesTest(Shared):
    """De onde a geração parte."""

    def published_catalog(self, weights_text, state_text, build_id=RELEASE_ID, weights=None, states=None):
        w = municipalities.parse_weights(weights_text, self.mesh) if weights is None else weights
        s = municipalities.parse_state_weights(state_text) if states is None else states
        return {"build_id": build_id, "release_url": config.RELEASE_URL.format(tag=build_id),
                "municipalities": {"weights": {"sha256": sha(weights_text), "count": len(w)},
                                   "state_weights": {"sha256": sha(state_text), "count": len(s)}}}

    def write_catalog(self, tmp, cat):
        path = Path(tmp) / "catalogo.json"
        path.write_text(json.dumps(cat), encoding="utf-8")
        return path

    def fetcher(self, files):
        calls = []

        def fetch(url):
            calls.append(url)
            name = url.rsplit("/", 1)[1]
            if name not in files:
                raise net.PermanentError(f"{url}: HTTP 404")
            value = files[name]
            if isinstance(value, Exception):
                raise value
            return value

        fetch.calls = calls
        return fetch

    def previous(self, cat, files):
        with tempfile.TemporaryDirectory() as tmp:
            fetch = self.fetcher(files)
            result = weights_tables.previous_tables(self.mesh, self.states, self.write_catalog(tmp, cat), fetch)
        return result, fetch.calls

    def test_the_release_table_is_the_start_when_the_catalog_says_so(self):
        weights_text, state_text = changed_tables(self.seed_weights, self.seed_states)
        cat = self.published_catalog(weights_text, state_text)
        (texts, weights, states, notes), calls = self.previous(
            cat, {"pesos_moto_municipios.csv": weights_text, "pesos_moto_estados.csv": state_text})
        self.assertEqual([], notes)
        self.assertEqual(weights_text, texts.weights)
        self.assertEqual(f"a release {RELEASE_ID}", texts.origin)
        self.assertEqual(Weight(0.7, 0.3, RELEASE_ID, "3.9.1", "2026-11-16"), weights["5300108"])
        self.assertEqual(Weight(0.7, 0.3, RELEASE_ID, "3.9.1", "2026-11-16"), states["DF"])
        self.assertEqual([config.RELEASE_URL.format(tag=RELEASE_ID) + "pesos_moto_municipios.csv",
                          config.RELEASE_URL.format(tag=RELEASE_ID) + "pesos_moto_estados.csv"], calls)

    def test_a_catalog_whose_tables_are_the_seed_downloads_nothing(self):
        cat = self.published_catalog(self.seed_texts.weights, self.seed_texts.state_weights)
        (texts, weights, states, notes), calls = self.previous(cat, {})
        self.assertEqual(([], self.seed_texts.weights), (calls, texts.weights))
        self.assertEqual([], notes)
        self.assertEqual("a cópia do repositório", texts.origin)

    def test_without_a_published_catalog_the_seed_is_the_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            texts, _, _, notes = weights_tables.previous_tables(self.mesh, self.states, Path(tmp) / "nada.json",
                                                                self.fetcher({}))
        self.assertEqual(("a cópia do repositório", []), (texts.origin, notes))

    def test_a_release_without_the_tables_falls_back_to_the_seed_and_says_so(self):
        weights_text, state_text = changed_tables(self.seed_weights, self.seed_states)
        (texts, _, _, notes), _ = self.previous(self.published_catalog(weights_text, state_text), {})
        self.assertEqual(self.seed_texts.weights, texts.weights)
        self.assertEqual(1, len(notes))
        self.assertIn(f"a release {RELEASE_ID} não tem as tabelas de pesos", notes[0])

    def test_a_table_that_does_not_match_the_catalog_sha256_falls_back(self):
        weights_text, state_text = changed_tables(self.seed_weights, self.seed_states)
        cat = self.published_catalog(weights_text, state_text)
        other, _ = changed_tables(self.seed_weights, self.seed_states, value=(0.5, 0.3))
        (texts, _, _, notes), _ = self.previous(
            cat, {"pesos_moto_municipios.csv": other, "pesos_moto_estados.csv": state_text})
        self.assertEqual(self.seed_texts.weights, texts.weights)
        self.assertIn("não bate com o sha256 do catálogo", notes[0])

    def test_a_table_with_a_different_count_than_the_catalog_falls_back(self):
        weights_text, state_text = changed_tables(self.seed_weights, self.seed_states)
        cat = self.published_catalog(weights_text, state_text)
        cat["municipalities"]["weights"]["count"] += 1
        (texts, _, _, notes), _ = self.previous(
            cat, {"pesos_moto_municipios.csv": weights_text, "pesos_moto_estados.csv": state_text})
        self.assertEqual(self.seed_texts.weights, texts.weights)
        self.assertIn("o catálogo diz", notes[0])

    def test_a_table_with_fewer_rows_than_the_seed_falls_back(self):
        weights_text, state_text = changed_tables(self.seed_weights, self.seed_states)
        short = "".join(weights_text.splitlines(keepends=True)[:-10])
        cat = self.published_catalog(short, state_text, weights=municipalities.parse_weights(short, self.mesh))
        (texts, _, _, notes), _ = self.previous(
            cat, {"pesos_moto_municipios.csv": short, "pesos_moto_estados.csv": state_text})
        self.assertEqual(self.seed_texts.weights, texts.weights)
        self.assertIn("não perde linhas", notes[0])

    def test_a_broken_format_falls_back(self):
        weights_text, state_text = changed_tables(self.seed_weights, self.seed_states)
        broken = weights_text.replace("0.7;0.3", "0.7;0.9")
        cat = self.published_catalog(weights_text, state_text)
        cat["municipalities"]["weights"]["sha256"] = sha(broken)
        (texts, _, _, notes), _ = self.previous(
            cat, {"pesos_moto_municipios.csv": broken, "pesos_moto_estados.csv": state_text})
        self.assertEqual(self.seed_texts.weights, texts.weights)
        self.assertIn("teto", notes[0])

    def test_a_network_failure_falls_back_instead_of_stopping_the_generation(self):
        weights_text, state_text = changed_tables(self.seed_weights, self.seed_states)
        failure = net.DownloadError("5 tentativas falharam")
        (texts, _, _, notes), _ = self.previous(
            self.published_catalog(weights_text, state_text),
            {"pesos_moto_municipios.csv": failure, "pesos_moto_estados.csv": state_text})
        self.assertEqual(self.seed_texts.weights, texts.weights)
        self.assertIn("5 tentativas falharam", notes[0])

    def test_a_table_that_is_not_utf8_falls_back(self):
        weights_text, state_text = changed_tables(self.seed_weights, self.seed_states)
        (texts, _, _, notes), _ = self.previous(
            self.published_catalog(weights_text, state_text),
            {"pesos_moto_municipios.csv": ValueError("invalid start byte"), "pesos_moto_estados.csv": state_text})
        self.assertEqual(self.seed_texts.weights, texts.weights)

    def test_a_catalog_from_before_the_weights_is_the_first_generation_and_starts_from_the_seed_without_a_note(self):
        (texts, _, _, notes), calls = self.previous({"build_id": "2026-10-05-93a54bc6", "release_url": "x/"}, {})
        self.assertEqual(([], self.seed_texts.weights, []), (calls, texts.weights, notes))

    def test_a_catalog_with_weights_but_without_the_tables_falls_back_with_a_note(self):
        cat = {"build_id": "2026-11-14-aaaaaaaa", "release_url": "x/", "municipalities": {"weights": {}}}
        (texts, _, _, notes), calls = self.previous(cat, {})
        self.assertEqual(([], self.seed_texts.weights), (calls, texts.weights))
        self.assertEqual(1, len(notes))

    def test_check_returns_the_inputs_with_the_text_of_the_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs, notes = weights_tables.check(Path(tmp) / "nada.json", self.fetcher({}))
        self.assertEqual(5570, len(inputs.mesh))
        self.assertEqual(self.seed_texts.weights, inputs.tables.weights)
        self.assertEqual([], notes)


class ApplyTest(Shared):
    """A tabela nova entra numa geração já montada (arquivos de apoio, catálogo, geracao.json)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = Path(tempfile.mkdtemp(prefix="andorinha-teste-"))
        inputs = municipalities.Inputs(cls.mesh, cls.seed_weights, cls.seed_states, cls.seed_texts)
        files = municipalities.generate(inputs, cls.template)
        osm = {"path": "x", "url": "https://exemplo/brazil.osm.pbf", "timestamp": "2026-11-15T00:00:00Z",
               "md5": "0a1b2c3d" * 4, "size": 1}
        from tests.test_catalog import package
        packages = {"base": package("base", [-33.75, -73.99, 5.27, -34.79], [-36.0, -76.0, 8.0, -32.0])}
        for state in config.STATES:
            packages[state] = package(state, [-30.0, -60.0, -10.0, -40.0], [-31.0, -61.0, -9.0, -39.0])
        cls.build_id = catalog.build_id("2026-11-15", osm["md5"])
        cat = catalog.build_catalog(cls.build_id, osm, packages, generator_commit="a" * 40,
                                    municipality_files=files, weights_digests=cls.seed_texts.digests())
        catalog.save(cat, cls.template / "catalogo.json")
        (cls.template / "geracao.json").write_text(json.dumps({"result": "ok", "notes": [
            "pesos de 5564 municípios (27 UFs) calculados com os tiles da geração antiga", "outro aviso"]}),
            encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.template, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="andorinha-teste-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dist = self.tmp / "dist"
        shutil.copytree(self.template, self.dist)
        self.tables = self.tmp / "novas"
        self.tables.mkdir()

    def write_tables(self, weights_text, state_text):
        (self.tables / municipalities.WEIGHTS_FILE).write_text(weights_text, encoding="utf-8", newline="\n")
        (self.tables / municipalities.STATE_WEIGHTS_FILE).write_text(state_text, encoding="utf-8", newline="\n")

    def snapshot(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(self.dist.iterdir())}

    def test_generation_without_the_study_already_carries_the_tables_it_started_from(self):
        cat = json.loads((self.dist / "catalogo.json").read_text(encoding="utf-8"))
        self.assertEqual(self.seed_texts.digests(), tuple(
            hashlib.sha256((self.dist / name).read_bytes()).hexdigest()
            for name in (municipalities.WEIGHTS_FILE, municipalities.STATE_WEIGHTS_FILE)))
        self.assertEqual(self.seed_texts.digests()[0], cat["municipalities"]["weights"]["sha256"])
        self.assertEqual(self.seed_texts.digests()[1], cat["municipalities"]["state_weights"]["sha256"])

    def test_new_tables_replace_the_old_and_the_catalog_follows(self):
        weights_text, state_text = changed_tables(self.seed_weights, self.seed_states, build_id=self.build_id)
        self.write_tables(weights_text, state_text)
        before = self.snapshot()
        notes = weights_tables.apply_to_dist(self.dist, self.tables, "estudo: 1 município")
        after = self.snapshot()

        self.assertEqual(weights_text.encode(), (self.dist / municipalities.WEIGHTS_FILE).read_bytes())
        self.assertEqual(state_text.encode(), (self.dist / municipalities.STATE_WEIGHTS_FILE).read_bytes())
        cat = json.loads((self.dist / "catalogo.json").read_text(encoding="utf-8"))
        self.assertEqual(sha(weights_text), cat["municipalities"]["weights"]["sha256"])
        self.assertEqual(sha(state_text), cat["municipalities"]["state_weights"]["sha256"])
        sources = {s["id"]: s for s in cat["contract"]["sources"]}
        self.assertEqual({"sha256": sha(weights_text)}, sources["pesos-moto-municipios"]["checksums"])
        self.assertEqual({"sha256": sha(state_text)}, sources["pesos-moto-estados"]["checksums"])
        self.assertEqual("2026-11-16", sources["pesos-moto-municipios"]["date"])
        # o arquivo do DF mudou (e só os que tinham linha refeita), e o catálogo tem o sha256 dele
        self.assertNotEqual(before["andorinha-municipios-DF.geojson.zst"], after["andorinha-municipios-DF.geojson.zst"])
        self.assertEqual(before["andorinha-municipios-GO.geojson.zst"], after["andorinha-municipios-GO.geojson.zst"])
        self.assertEqual(after["andorinha-municipios-DF.geojson.zst"], cat["municipalities"]["states"]["DF"]["sha256"])
        df = next(f for f in cat["contract"]["files"] if f["id"] == "municipios-DF")
        self.assertEqual(after["andorinha-municipios-DF.geojson.zst"], df["sha256"])
        self.assertEqual(self.build_id, cat["build_id"])
        # a geração agora tem pesos de duas gerações de tiles; o aviso diz isso
        self.assertTrue(any("calculados com os tiles da geração 2026-11-09-c17dff60" in n for n in notes), notes)
        self.assertEqual(sorted(after), sorted(before))  # os mesmos arquivos, sem sobras

    def test_the_catalog_still_follows_the_common_contract(self):
        weights_text, state_text = changed_tables(self.seed_weights, self.seed_states, build_id=self.build_id)
        self.write_tables(weights_text, state_text)
        weights_tables.apply_to_dist(self.dist, self.tables)
        cat = json.loads((self.dist / "catalogo.json").read_text(encoding="utf-8"))
        self.assertEqual([], contract_check.problems(cat, GENERATOR_DIR.parent / "docs"))

    def test_the_publication_checks_pass_with_the_new_tables(self):
        weights_text, state_text = changed_tables(self.seed_weights, self.seed_states, build_id=self.build_id)
        self.write_tables(weights_text, state_text)
        weights_tables.apply_to_dist(self.dist, self.tables)
        cat = json.loads((self.dist / "catalogo.json").read_text(encoding="utf-8"))
        self.assertEqual(["pesos_moto_estados.csv", "pesos_moto_municipios.csv"],
                         sorted(publishing.weights_tables(cat)))
        with mock.patch.object(config, "DIST", self.dist):
            problems = [p for p in publishing.check(cat, None, True, True) if not p.startswith(("base:", "AC:"))
                        and "falta andorinha-" not in p]
        self.assertEqual([], [p for p in problems if "pesos" in p or "municípios" in p])

    def test_geracao_json_notes_are_updated(self):
        weights_text, state_text = changed_tables(self.seed_weights, self.seed_states, build_id=self.build_id)
        self.write_tables(weights_text, state_text)
        weights_tables.apply_to_dist(self.dist, self.tables, "estudo: 1 município e 1 estado")
        notes = json.loads((self.dist / "geracao.json").read_text(encoding="utf-8"))["notes"]
        self.assertIn("outro aviso", notes)
        self.assertIn("estudo: 1 município e 1 estado", notes)
        self.assertFalse(any(n.startswith("pesos de 5564 municípios (27 UFs) calculados com os tiles da geração antiga")
                             for n in notes))

    def test_a_table_that_loses_rows_leaves_the_generation_untouched(self):
        lines = self.seed_texts.weights.splitlines(keepends=True)
        self.write_tables("".join(lines[:-1]), self.seed_texts.state_weights)
        before = self.snapshot()
        with self.assertRaises(weights_tables.ApplyError):
            weights_tables.apply_to_dist(self.dist, self.tables)
        self.assertEqual(before, self.snapshot())

    def test_a_missing_table_leaves_the_generation_untouched(self):
        before = self.snapshot()
        with self.assertRaises(weights_tables.ApplyError):
            weights_tables.apply_to_dist(self.dist, self.tables)  # a pasta de tabelas está vazia
        self.assertEqual(before, self.snapshot())

    def test_a_table_with_fewer_rows_than_the_generation_leaves_it_untouched(self):
        """A geração usou uma tabela maior que a semente (uma linha a mais, de um município novo)."""
        extra = dict(self.seed_weights)
        del extra["1100015"]
        weights_text, state_text = changed_tables(extra, self.seed_states)
        self.write_tables(weights_text, state_text)
        before = self.snapshot()
        with self.assertRaises(weights_tables.ApplyError):
            weights_tables.apply_to_dist(self.dist, self.tables)
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
