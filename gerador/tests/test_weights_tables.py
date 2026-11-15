"""As tabelas de pesos que o gerador atualiza (andorinha/weights_tables.py): o formato escrito de volta, a rotação dos
lotes, a mescla dos resultados, a tabela de que a geração parte (proximo > normal > semente, e o que acontece quando
uma não serve) e a subida do resultado do estudo na release."""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

GENERATOR_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GENERATOR_DIR))
from andorinha import config, municipalities, net, weights_tables  # noqa: E402
from andorinha.municipalities import Weight  # noqa: E402
from andorinha.weights_study import Outcome  # noqa: E402

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


class PreviousHelpers(Shared):
    """Ferramentas dos testes de onde a geração parte."""

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
        # as tentativas de ler o json das tabelas próximo (a maioria das releases não tem) não contam aqui
        return result, [c for c in fetch.calls if ".proximo." not in c and not c.endswith("pesos_proximo.json")]


class PreviousTablesTest(PreviousHelpers):
    """De onde a geração parte, sem as tabelas próximo."""

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


class NextTablesTest(PreviousHelpers):
    """As tabelas `proximo` da release anterior vencem as normais; sem elas (ou se não servem), valem as normais."""

    def next_files(self, value=(0.5, 0.3), build_id=RELEASE_ID, **manifest_changes):
        weights_text, state_text = changed_tables(self.seed_weights, self.seed_states, value=value, date="2026-11-17",
                                                  build_id=build_id)
        manifest = weights_tables.next_manifest(
            weights_text, state_text, municipalities.parse_weights(weights_text, self.mesh),
            municipalities.parse_state_weights(state_text), build_id, date="2026-11-17")
        manifest.update(manifest_changes)
        return {"pesos_moto_municipios.proximo.csv": weights_text, "pesos_moto_estados.proximo.csv": state_text,
                "pesos_proximo.json": json.dumps(manifest)}

    def normal(self):
        """A tabela normal da release: a de que a geração dela partiu (valores diferentes dos da próximo)."""
        weights_text, state_text = changed_tables(self.seed_weights, self.seed_states, value=(0.7, 0.3))
        return weights_text, state_text, self.published_catalog(weights_text, state_text)

    def start(self, files, cat=None):
        weights_text, state_text, normal_cat = self.normal()
        files = {"pesos_moto_municipios.csv": weights_text, "pesos_moto_estados.csv": state_text, **files}
        return self.previous(cat or normal_cat, files)

    def test_manifest_describes_the_tables(self):
        files = self.next_files()
        manifest = json.loads(files["pesos_proximo.json"])
        self.assertEqual(sha(files["pesos_moto_municipios.proximo.csv"]), manifest["weights"]["sha256"])
        self.assertEqual(len(municipalities.parse_weights(files["pesos_moto_municipios.proximo.csv"], self.mesh)),
                         manifest["weights"]["count"])
        self.assertEqual((RELEASE_ID, config.VALHALLA_VERSION, 27), (manifest["build_id"], manifest["valhalla_version"],
                                                                    manifest["state_weights"]["count"]))
        self.assertEqual("2026-11-17", manifest["study"]["date"])

    def test_next_wins_over_the_normal_tables(self):
        files = self.next_files()
        (texts, weights, states, notes), calls = self.start(files)
        self.assertEqual([], notes)
        self.assertEqual(files["pesos_moto_municipios.proximo.csv"], texts.weights)
        self.assertEqual(f"as tabelas próximo da release {RELEASE_ID}", texts.origin)
        self.assertEqual((0.5, 0.3), (weights["5300108"].use_highways, weights["5300108"].use_trails))
        self.assertEqual("2026-11-17", states["DF"].date)
        self.assertEqual([], calls)  # as normais nem foram baixadas

    def test_next_wins_even_when_the_normal_tables_are_the_seed(self):
        cat = self.published_catalog(self.seed_texts.weights, self.seed_texts.state_weights)
        (texts, *_), _ = self.start(self.next_files(), cat)
        self.assertEqual(self.next_files()["pesos_moto_municipios.proximo.csv"], texts.weights)

    def test_without_next_the_normal_tables_are_used_and_nothing_is_said(self):
        weights_text, _, _ = self.normal()
        (texts, _, _, notes), _ = self.start({})
        self.assertEqual((weights_text, []), (texts.weights, notes))
        self.assertEqual(f"a release {RELEASE_ID}", texts.origin)

    def test_without_next_and_with_the_seed_as_normal_the_seed_is_used_quietly(self):
        cat = self.published_catalog(self.seed_texts.weights, self.seed_texts.state_weights)
        (texts, _, _, notes), calls = self.start({}, cat)
        self.assertEqual((self.seed_texts.weights, [], []), (texts.weights, notes, calls))

    def assert_falls_back_to_normal(self, files, fragment):
        weights_text, _, _ = self.normal()
        (texts, _, _, notes), _ = self.start(files)
        self.assertEqual(weights_text, texts.weights)
        self.assertEqual(1, len(notes), notes)
        self.assertIn("tabelas próximo", notes[0])
        self.assertIn(fragment, notes[0])

    def test_a_table_that_does_not_match_the_manifest_sha256_falls_back_to_the_normal_ones(self):
        files = self.next_files()
        files["pesos_moto_municipios.proximo.csv"] = files["pesos_moto_municipios.proximo.csv"].replace("0.5;0.3", "0.4;0.3")
        self.assert_falls_back_to_normal(files, "sha256")

    def test_a_manifest_of_another_generation_falls_back(self):
        self.assert_falls_back_to_normal(self.next_files(build_id="2026-11-09-c17dff60"), "geração")

    def test_a_manifest_with_another_count_falls_back(self):
        files = self.next_files()
        manifest = json.loads(files["pesos_proximo.json"])
        manifest["weights"]["count"] += 1
        files["pesos_proximo.json"] = json.dumps(manifest)
        self.assert_falls_back_to_normal(files, "o pesos_proximo.json diz")

    def test_a_manifest_from_another_valhalla_falls_back(self):
        self.assert_falls_back_to_normal(self.next_files(valhalla_version="3.8.0"), "Valhalla 3.8.0")

    def test_a_manifest_without_keys_or_that_is_not_json_falls_back(self):
        files = self.next_files()
        manifest = json.loads(files["pesos_proximo.json"])
        del manifest["weights"]
        files["pesos_proximo.json"] = json.dumps(manifest)
        self.assert_falls_back_to_normal(files, "sem a chave")
        files["pesos_proximo.json"] = "<html>erro</html>"
        weights_text, _, _ = self.normal()
        (texts, _, _, notes), _ = self.start(files)
        self.assertEqual((weights_text, 1), (texts.weights, len(notes)))

    def test_next_with_fewer_rows_than_the_seed_falls_back(self):
        files = self.next_files()
        short = "".join(files["pesos_moto_municipios.proximo.csv"].splitlines(keepends=True)[:-10])
        manifest = json.loads(files["pesos_proximo.json"])
        manifest["weights"] = {"file": "pesos_moto_municipios.proximo.csv", "sha256": sha(short),
                               "count": len(municipalities.parse_weights(short, self.mesh))}
        files.update({"pesos_moto_municipios.proximo.csv": short, "pesos_proximo.json": json.dumps(manifest)})
        self.assert_falls_back_to_normal(files, "não perde linhas")

    def test_next_with_a_weight_over_the_ceiling_falls_back(self):
        files = self.next_files()
        bad = files["pesos_moto_municipios.proximo.csv"].replace("0.5;0.3", "0.5;0.9")
        manifest = json.loads(files["pesos_proximo.json"])
        manifest["weights"]["sha256"] = sha(bad)
        files.update({"pesos_moto_municipios.proximo.csv": bad, "pesos_proximo.json": json.dumps(manifest)})
        self.assert_falls_back_to_normal(files, "teto")

    def test_a_network_failure_on_next_falls_back(self):
        files = self.next_files()
        files["pesos_moto_estados.proximo.csv"] = net.DownloadError("5 tentativas falharam")
        self.assert_falls_back_to_normal(files, "5 tentativas")

    def test_next_and_normal_both_bad_end_at_the_seed_with_two_notes(self):
        files = self.next_files()
        files["pesos_proximo.json"] = "lixo"
        weights_text, state_text, cat = self.normal()
        files.update({"pesos_moto_municipios.csv": "outra tabela\n", "pesos_moto_estados.csv": state_text})
        (texts, _, _, notes), _ = self.previous(cat, files)
        self.assertEqual(self.seed_texts.weights, texts.weights)
        self.assertEqual(2, len(notes))

    def test_a_catalog_from_before_the_weights_never_looks_for_next(self):
        (texts, *_), calls = self.previous({"build_id": "2026-10-05-93a54bc6", "release_url": "x/"}, {})
        self.assertEqual(self.seed_texts.weights, texts.weights)
        self.assertEqual([], calls)


class FakeGh:
    """Um `gh` falso com uma release: devolve o estado e guarda os `upload`."""

    def __init__(self, state="publicada", assets=None, fail_on=None, drop=()):
        self.state, self.assets = state, dict(assets or {"andorinha-base.tar.zst": 10, "catalogo.json": 5})
        self.fail_on, self.drop, self.calls = fail_on, set(drop), []

    def __call__(self, args):
        self.calls.append(list(args))
        if args[:2] == ["release", "view"]:
            if self.state == "inexistente":
                return 1, "", "release not found"
            return 0, json.dumps({"isDraft": self.state == "rascunho",
                                  "assets": [{"name": n, "size": size} for n, size in self.assets.items()]}), ""
        if args[:2] == ["release", "upload"]:
            path = Path(args[3])
            if self.fail_on == path.name:
                return 1, "", "HTTP 502"
            if path.name not in self.drop:
                self.assets[path.name] = path.stat().st_size
            return 0, "", ""
        return 1, "", f"comando inesperado: {args}"

    def uploads(self):
        return [Path(c[3]).name for c in self.calls if c[:2] == ["release", "upload"]]


class UploadNextTest(Shared):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="andorinha-teste-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        weights_text, state_text = changed_tables(self.seed_weights, self.seed_states)
        manifest = weights_tables.next_manifest(
            weights_text, state_text, municipalities.parse_weights(weights_text, self.mesh),
            municipalities.parse_state_weights(state_text), RELEASE_ID)
        (self.tmp / "pesos_moto_municipios.proximo.csv").write_text(weights_text, encoding="utf-8", newline="\n")
        (self.tmp / "pesos_moto_estados.proximo.csv").write_text(state_text, encoding="utf-8", newline="\n")
        (self.tmp / "pesos_proximo.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_only_the_three_extra_assets_go_up_the_json_last_and_nothing_else_is_touched(self):
        gh = FakeGh()
        names = weights_tables.upload_next(gh, RELEASE_ID, self.tmp)
        self.assertEqual(["pesos_moto_municipios.proximo.csv", "pesos_moto_estados.proximo.csv", "pesos_proximo.json"],
                         names)
        self.assertEqual(names, gh.uploads())
        for call in gh.calls:
            self.assertIn(call[:2], (["release", "view"], ["release", "upload"]), call)  # nada de edit/delete
            if call[1] == "upload":
                self.assertEqual(RELEASE_ID, call[2])
                self.assertEqual("--clobber", call[-1])
        self.assertEqual({"andorinha-base.tar.zst", "catalogo.json", *names}, set(gh.assets))

    def test_a_release_that_is_not_published_gets_nothing(self):
        for state in ("rascunho", "inexistente"):
            gh = FakeGh(state=state)
            with self.assertRaises(weights_tables.UploadError):
                weights_tables.upload_next(gh, RELEASE_ID, self.tmp)
            self.assertEqual([], gh.uploads())

    def test_tables_that_do_not_match_the_manifest_are_not_sent(self):
        (self.tmp / "pesos_moto_estados.proximo.csv").write_text("outra\n", encoding="utf-8")
        gh = FakeGh()
        with self.assertRaisesRegex(weights_tables.UploadError, "sha256"):
            weights_tables.upload_next(gh, RELEASE_ID, self.tmp)
        self.assertEqual([], gh.calls)

    def test_a_manifest_of_another_generation_is_not_sent(self):
        with self.assertRaisesRegex(weights_tables.UploadError, "geração"):
            weights_tables.upload_next(FakeGh(), "2026-11-23-4e5f6a7b", self.tmp)

    def test_missing_files_are_not_sent(self):
        (self.tmp / "pesos_proximo.json").unlink()
        gh = FakeGh()
        with self.assertRaisesRegex(weights_tables.UploadError, "falta"):
            weights_tables.upload_next(gh, RELEASE_ID, self.tmp)
        self.assertEqual([], gh.calls)

    def test_a_failed_upload_stops_before_the_manifest_so_the_next_generation_ignores_the_rest(self):
        gh = FakeGh(fail_on="pesos_moto_estados.proximo.csv")
        with self.assertRaisesRegex(weights_tables.UploadError, "502"):
            weights_tables.upload_next(gh, RELEASE_ID, self.tmp)
        self.assertNotIn("pesos_proximo.json", gh.uploads())

    def test_an_asset_that_did_not_land_is_an_error(self):
        with self.assertRaisesRegex(weights_tables.UploadError, "pesos_proximo.json"):
            weights_tables.upload_next(FakeGh(drop=("pesos_proximo.json",)), RELEASE_ID, self.tmp)



if __name__ == "__main__":
    unittest.main()
