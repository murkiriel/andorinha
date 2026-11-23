"""O aviso de geração agendada que falhou: a issue fixa "Geração falhando", aberta, comentada e fechada pelo workflow.
Nenhum teste chama o `gh` de verdade."""
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

GENERATOR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GENERATOR))
from andorinha import alert  # noqa: E402

RUN = "https://github.com/murkiriel/andorinha/actions/runs/123"


class FakeGh:
    """Imita o `gh issue list/create/comment/close` usado pelo aviso."""

    def __init__(self):
        self.issues = {}  # número -> {"title", "state", "body", "comments"}
        self.commands = []
        self.fixed_error = None  # (código, stderr) para qualquer comando

    def add(self, title, state="OPEN"):
        number = len(self.issues) + 1
        self.issues[number] = {"title": title, "state": state, "body": "", "comments": []}
        return number

    def value(self, args, flag):
        return args[args.index(flag) + 1]

    def __call__(self, args):
        self.commands.append(list(args))
        if self.fixed_error:
            return self.fixed_error[0], "", self.fixed_error[1]
        action = args[1]
        if action == "list":
            state = self.value(args, "--state").upper()
            found = [{"number": n, "title": i["title"]} for n, i in self.issues.items() if i["state"] == state]
            return 0, json.dumps(found), ""
        if action == "create":
            number = self.add(self.value(args, "--title"))
            self.issues[number]["body"] = self.value(args, "--body")
            return 0, f"https://github.com/x/y/issues/{number}\n", ""
        number = int(args[2])
        if action == "comment":
            self.issues[number]["comments"].append(self.value(args, "--body"))
            return 0, "", ""
        if action == "close":
            self.issues[number]["state"] = "CLOSED"
            self.issues[number]["comments"].append(self.value(args, "--comment"))
            return 0, "", ""
        return 1, "", f"comando inesperado: {args}"


class OutcomeTest(unittest.TestCase):
    """Os resultados dos jobs (success, failure, cancelled, skipped) viram falhou, ok ou nada."""

    def test_generation_that_failed(self):
        self.assertEqual("falhou", alert.outcome("success", "failure"))

    def test_interval_that_failed(self):
        self.assertEqual("falhou", alert.outcome("failure", "skipped"))

    def test_generation_that_passed(self):
        self.assertEqual("ok", alert.outcome("success", "success"))

    def test_not_the_time_to_generate(self):
        """O dia a dia: o interval diz "ainda não" e o build é pulado. Não é falha nem sucesso."""
        self.assertEqual("nada", alert.outcome("success", "skipped"))

    def test_cancelled_by_hand(self):
        self.assertEqual("nada", alert.outcome("success", "cancelled"))


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGh()

    def sync(self, result):
        return alert.sync(self.gh, result, RUN, "2026-11-15")

    def open_ones(self):
        return [i for i in self.gh.issues.values() if i["state"] == "OPEN"]

    def test_first_failure_opens_the_issue(self):
        self.assertEqual("aberta", self.sync("falhou"))
        [issue] = self.open_ones()
        self.assertEqual(alert.TITLE, issue["title"])
        self.assertIn(RUN, issue["body"])
        self.assertIn("2026-11-15", issue["body"])

    def test_next_failure_comments_on_the_same_issue(self):
        number = self.gh.add(alert.TITLE)
        self.assertEqual("comentada", self.sync("falhou"))
        self.assertEqual(1, len(self.gh.issues))
        self.assertIn(RUN, self.gh.issues[number]["comments"][0])

    def test_success_closes_the_issue(self):
        number = self.gh.add(alert.TITLE)
        self.assertEqual("fechada", self.sync("ok"))
        self.assertEqual("CLOSED", self.gh.issues[number]["state"])
        self.assertIn(RUN, self.gh.issues[number]["comments"][-1])

    def test_success_without_an_issue_does_nothing(self):
        self.assertEqual("nada", self.sync("ok"))
        self.assertEqual({}, self.gh.issues)

    def test_nothing_to_report_does_not_call_gh(self):
        """Duas vezes por dia o build é pulado: nem a lista de issues é lida."""
        self.gh.add(alert.TITLE)
        self.assertEqual("nada", self.sync("nada"))
        self.assertEqual([], self.gh.commands)

    def test_a_closed_issue_is_not_reused(self):
        self.gh.add(alert.TITLE, state="CLOSED")
        self.assertEqual("aberta", self.sync("falhou"))
        self.assertEqual(1, len(self.open_ones()))

    def test_only_the_exact_title_counts(self):
        other = self.gh.add(alert.TITLE + " no fork")
        self.assertEqual("aberta", self.sync("falhou"))
        self.assertEqual([], self.gh.issues[other]["comments"])
        self.assertEqual(2, len(self.open_ones()))

    def test_gh_error_shows_what_gh_said(self):
        self.gh.fixed_error = (1, "HTTP 403: Resource not accessible by integration")
        with self.assertRaises(alert.AlertError) as ctx:
            self.sync("falhou")
        self.assertIn("403", str(ctx.exception))


class WeightsOutcomeTest(unittest.TestCase):
    def test_study_that_failed(self):
        self.assertEqual("falhou", alert.weights_outcome("failure"))

    def test_study_that_passed(self):
        self.assertEqual("ok", alert.weights_outcome("success"))

    def test_study_that_did_not_run_says_nothing(self):
        """Pulado (nada foi publicado) ou cancelado à mão: a issue fica como está."""
        self.assertEqual("nada", alert.weights_outcome("skipped"))
        self.assertEqual("nada", alert.weights_outcome("cancelled"))


class WeightsSyncTest(unittest.TestCase):
    """A issue "Estudo dos pesos falhando" é outra: não mexe na "Geração falhando" nem o contrário."""

    def setUp(self):
        self.gh = FakeGh()

    def sync(self, result):
        return alert.sync(self.gh, result, RUN, "2026-11-22 16:50 UTC", alert.WEIGHTS)

    def test_failure_opens_the_issue(self):
        self.assertEqual("aberta", self.sync("falhou"))
        [issue] = self.gh.issues.values()
        self.assertEqual("Estudo dos pesos falhando", issue["title"])
        self.assertIn(RUN, issue["body"])
        self.assertIn("2026-11-22", issue["body"])

    def test_next_failure_comments(self):
        number = self.gh.add(alert.WEIGHTS.title)
        self.assertEqual("comentada", self.sync("falhou"))
        self.assertEqual(1, len(self.gh.issues))
        self.assertIn(RUN, self.gh.issues[number]["comments"][0])

    def test_first_pass_closes(self):
        number = self.gh.add(alert.WEIGHTS.title)
        self.assertEqual("fechada", self.sync("ok"))
        self.assertEqual("CLOSED", self.gh.issues[number]["state"])

    def test_skipped_study_does_nothing_and_does_not_call_gh(self):
        self.gh.add(alert.WEIGHTS.title)
        self.assertEqual("nada", self.sync(alert.weights_outcome("skipped")))
        self.assertEqual([], self.gh.commands)

    def test_does_not_touch_the_generation_issue(self):
        generation = self.gh.add(alert.TITLE)
        self.assertEqual("aberta", self.sync("falhou"))
        self.assertEqual([], self.gh.issues[generation]["comments"])
        self.assertEqual("OPEN", self.gh.issues[generation]["state"])
        self.assertEqual("fechada", self.sync("ok"))
        self.assertEqual("OPEN", self.gh.issues[generation]["state"])


NOW = datetime(2026, 11, 30, 12, 0, tzinfo=timezone.utc)


class StaleOutcomeTest(unittest.TestCase):
    """A idade da geração publicada contra o limite de 9 dias, com datas fixas."""

    def at(self, built_at):
        return alert.stale_outcome(built_at, NOW)[0]

    def test_limit_is_nine_days(self):
        self.assertEqual(9, alert.STALE_DAYS)

    def test_fresh_generation_is_ok(self):
        self.assertEqual("ok", self.at("2026-11-22T17:30:00Z"))  # 7 dias e meio: a semanal ainda vem aí

    def test_exactly_nine_days_is_still_ok(self):
        self.assertEqual("ok", self.at("2026-11-21T12:00:00Z"))

    def test_one_second_past_nine_days_is_stale(self):
        self.assertEqual("falhou", self.at("2026-11-21T11:59:59Z"))

    def test_missed_week_is_stale(self):
        result, detail = alert.stale_outcome("2026-11-15T17:30:00Z", NOW)
        self.assertEqual("falhou", result)
        self.assertIn("2026-11-15T17:30:00Z", detail)
        self.assertIn("14.8 dias", detail)

    def test_missing_or_broken_date_says_nothing(self):
        for built_at in (None, "", "ontem", "2026-11-22"):
            self.assertEqual("nada", self.at(built_at), built_at)


class StaleSyncTest(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGh()

    def sync(self, built_at):
        result, detail = alert.stale_outcome(built_at, NOW)
        return alert.sync(self.gh, result, RUN, "2026-11-30 12:00 UTC", alert.STALE, detail)

    def test_stale_opens_then_comments_then_closes_when_the_generation_returns(self):
        self.assertEqual("aberta", self.sync("2026-11-15T17:30:00Z"))
        [issue] = self.gh.issues.values()
        self.assertEqual("Publicação parada", issue["title"])
        self.assertIn("2026-11-15T17:30:00Z", issue["body"])
        self.assertIn("14.8 dias", issue["body"])
        self.assertEqual("comentada", self.sync("2026-11-15T17:30:00Z"))
        self.assertEqual(1, len(issue["comments"]))
        self.assertEqual("fechada", self.sync("2026-11-29T17:30:00Z"))
        self.assertEqual("CLOSED", issue["state"])
        self.assertIn("2026-11-29T17:30:00Z", issue["comments"][-1])

    def test_fresh_generation_without_an_issue_does_nothing(self):
        self.assertEqual("nada", self.sync("2026-11-29T17:30:00Z"))
        self.assertEqual({}, self.gh.issues)


class ScriptTest(unittest.TestCase):
    """scripts/alert.py, a linha de comando do workflow, com o gh falso e o relógio injetados."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("alert_script", GENERATOR / "scripts" / "alert.py")
        self.script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.script)
        self.gh = FakeGh()
        self._tmp = tempfile.TemporaryDirectory()
        self.catalog = Path(self._tmp.name) / "catalogo.json"
        self.set_built_at("2026-11-29T17:30:00Z")  # recente em NOW

    def tearDown(self):
        self._tmp.cleanup()

    def set_built_at(self, built_at):
        self.catalog.write_text(json.dumps({"built_at": built_at}), encoding="utf-8")

    def run_script(self, interval, generate, weights="skipped"):
        out = io.StringIO()
        with redirect_stdout(out):
            self.script.main(["--interval", interval, "--generate", generate, "--weights", weights,
                              "--run-url", RUN, "--catalog", str(self.catalog)], gh=self.gh, now=NOW)
        return out.getvalue()

    def titles(self):
        return sorted(i["title"] for i in self.gh.issues.values() if i["state"] == "OPEN")

    def test_failure_opens_the_issue(self):
        log = self.run_script("success", "failure")
        self.assertIn("aberta", log)
        self.assertEqual(["Geração falhando"], self.titles())

    def test_skipped_build_with_a_fresh_generation_only_reads_the_issue_list(self):
        """O dia a dia: duas vezes por dia o build é pulado e a geração publicada é recente. Só lê a lista de issues
        (uma chamada, para fechar a "Publicação parada" se ela estiver aberta); nada é aberto, comentado nem fechado."""
        self.assertIn("nada", self.run_script("success", "skipped"))
        self.assertEqual([["issue", "list"]], [c[:2] for c in self.gh.commands])

    def test_failed_study_opens_its_own_issue(self):
        self.run_script("success", "success", "failure")
        self.assertEqual(["Estudo dos pesos falhando"], self.titles())

    def test_study_that_passes_closes_its_issue(self):
        self.gh.add(alert.WEIGHTS.title)
        self.run_script("success", "success", "success")
        self.assertEqual([], self.titles())

    def test_skipped_study_without_publication_does_nothing(self):
        self.gh.add(alert.WEIGHTS.title)
        self.run_script("success", "skipped", "skipped")
        self.assertEqual([alert.WEIGHTS.title], self.titles())

    def test_stale_publication_is_reported_even_when_the_build_was_skipped(self):
        """O interval disse "ainda não", mas a geração publicada tem 14 dias: abre "Publicação parada"."""
        self.set_built_at("2026-11-15T17:30:00Z")
        log = self.run_script("success", "skipped")
        self.assertEqual(["Publicação parada"], self.titles())
        self.assertIn("aberta", log)

    def test_publication_that_returns_closes_the_stale_issue(self):
        self.gh.add(alert.STALE.title)
        self.run_script("success", "success", "skipped")
        self.assertEqual([], self.titles())

    def test_catalog_without_the_file_says_nothing_about_staleness(self):
        self.catalog.unlink()
        self.run_script("success", "skipped")
        self.assertEqual([], self.gh.commands)

    def test_the_three_issues_are_independent(self):
        self.set_built_at("2026-11-15T17:30:00Z")
        self.run_script("failure", "skipped", "failure")
        self.assertEqual(sorted(["Estudo dos pesos falhando", "Geração falhando", "Publicação parada"]), self.titles())

    def test_gh_error_in_one_issue_does_not_stop_the_others(self):
        calls = []

        def broken_for_generation(args):
            calls.append(list(args))
            if "create" in args and alert.GENERATION.title in args:
                return 1, "", "HTTP 403"
            return self.gh(args)

        self.set_built_at("2026-11-15T17:30:00Z")
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as ctx:
            self.script.main(["--interval", "failure", "--generate", "skipped", "--run-url", RUN,
                              "--catalog", str(self.catalog)], gh=broken_for_generation, now=NOW)
        self.assertEqual(1, ctx.exception.code)
        self.assertIn("ERRO", out.getvalue())
        self.assertEqual(["Publicação parada"], self.titles())  # a terceira rodou mesmo com a primeira quebrada


if __name__ == "__main__":
    unittest.main()
