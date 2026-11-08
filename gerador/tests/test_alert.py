"""O aviso de geração agendada que falhou: a issue fixa "Geração falhando", aberta, comentada e fechada pelo workflow.
Nenhum teste chama o `gh` de verdade."""
import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
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


class ScriptTest(unittest.TestCase):
    """scripts/alert.py, a linha de comando do workflow, com o gh falso injetado."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("alert_script", GENERATOR / "scripts" / "alert.py")
        self.script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.script)
        self.gh = FakeGh()

    def run_script(self, interval, generate):
        out = io.StringIO()
        with redirect_stdout(out):
            self.script.main(["--interval", interval, "--generate", generate, "--run-url", RUN], gh=self.gh)
        return out.getvalue()

    def test_failure_opens_the_issue(self):
        log = self.run_script("success", "failure")
        self.assertIn("aberta", log)
        self.assertEqual(1, len(self.gh.issues))

    def test_skipped_build_does_nothing(self):
        self.assertIn("nada", self.run_script("success", "skipped"))
        self.assertEqual([], self.gh.commands)


if __name__ == "__main__":
    unittest.main()
