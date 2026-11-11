"""Os workflows do GitHub Actions: o que precisa continuar valendo neles, lido do texto dos arquivos (sem YAML)."""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
WORKFLOWS = REPO / ".github" / "workflows"


def workflows():
    return {f.name: f.read_text(encoding="utf-8") for f in sorted(WORKFLOWS.glob("*.yml"))}


class RunnerTest(unittest.TestCase):
    def test_there_are_workflows(self):
        self.assertEqual(["gerar.yml", "testes.yml"], list(workflows()))

    def test_runner_image_is_pinned(self):
        """Imagem com versão, nunca `-latest`: quando o GitHub troca a imagem por trás do `ubuntu-latest`, o passo
        que libera disco e o pyvalhalla fixado passam a rodar numa máquina em que nunca foram testados."""
        for name, text in workflows().items():
            images = re.findall(r"^\s*runs-on:\s*(\S+)\s*$", text, flags=re.MULTILINE)
            self.assertTrue(images, f"{name}: nenhum runs-on")
            for image in images:
                self.assertRegex(image, r"^ubuntu-\d+\.\d+$", f"{name}: runs-on {image}")


class PinnedDependenciesTest(unittest.TestCase):
    """Só a geração corrente precisa ser reproduzível, mas ela inteira: dependências travadas com hashes."""

    def test_every_pip_install_requires_hashes(self):
        """Todo `pip install` instala de um requirements travado, com --require-hashes: nada de pacote solto, que o
        pip resolveria na hora para a versão (e o arquivo) do dia."""
        seen = 0
        for name, text in workflows().items():
            for line in text.splitlines():
                command = line.split("#", 1)[0]
                if "pip install" not in command:
                    continue
                seen += 1
                m = re.search(r"pip install --require-hashes -r (\S+)\s*$", command)
                self.assertTrue(m, f"{name}: {line.strip()}")
                folder = REPO / "gerador" if name == "testes.yml" else REPO  # o testes roda de dentro de gerador/
                self.assertTrue((folder / m.group(1)).is_file(), f"{name}: {m.group(1)} não existe")
        self.assertEqual(2, seen)

    def test_every_action_is_pinned_by_commit_sha(self):
        """Toda `uses:` pelo SHA de 40 caracteres do commit, com a tag exata em comentário: uma tag (`v7`) pode ser
        movida para outro commit sem aviso; o SHA não."""
        seen = 0
        for name, text in workflows().items():
            for line in text.splitlines():
                if not re.match(r"^\s*(- )?uses:", line):
                    continue
                seen += 1
                self.assertRegex(line, r"^\s*(- )?uses: [\w.-]+/[\w./-]+@[0-9a-f]{40}  # v\d+\.\d+\.\d+$",
                                 f"{name}: {line.strip()}")
        self.assertEqual(7, seen)


def job(text: str, name: str) -> str:
    """O trecho de um job: da linha `  nome:` até o próximo job (ou o fim do arquivo)."""
    m = re.search(rf"^  {name}:\n(.*?)(?=^  \w[\w-]*:\n|\Z)", text.replace("\r\n", "\n"), flags=re.MULTILINE | re.DOTALL)
    return m.group(1) if m else ""


class ScheduledGenerationTest(unittest.TestCase):
    """O workflow gerar roda todo dia e só segue para o build quando a geração publicada é de antes do domingo."""

    def setUp(self):
        self.text = workflows()["gerar.yml"].replace("\r\n", "\n")
        self.interval = job(self.text, "interval")
        self.generate = job(self.text, "generate")

    def crons(self):
        return re.findall(r'(?m)^    - cron: "([^"]+)"', self.text)

    def test_runs_twice_a_day(self):
        """Dois disparos por dia: um disparo que o GitHub pula não avisa ninguém, o segundo cobre o primeiro."""
        self.assertEqual(["17 6 * * *", "17 12 * * *"], self.crons())

    def test_never_on_the_hour(self):
        """Na hora cheia o GitHub atrasa ou pula os disparos agendados."""
        for cron in self.crons():
            self.assertNotEqual("0", cron.split()[0], cron)

    def test_interval_job_asks_only_the_weekly_rule(self):
        """A geração extra marcada (GENERATE_FROM) saiu com a regra dos 29 dias: o disparo à mão cobre."""
        self.assertIn("run: python3 gerador/scripts/due.py $MANUAL\n", self.interval)
        self.assertNotIn("GENERATE_FROM", self.text)

    def test_published_date_is_the_one_on_the_branch_now(self):
        """Um run que esperou na fila atrás de outro que publicou lê o catálogo de agora, não o do disparo: sem
        isso geraria de novo logo depois da publicação."""
        self.assertRegex(self.interval, r"- uses: actions/checkout@[0-9a-f]{40}  # v[\d.]+\n\s+with:\n"
                                        r"\s+ref: \$\{\{ github\.ref \}\}\n")

    def test_can_still_be_started_by_hand(self):
        self.assertIn("  workflow_dispatch:\n", self.text)

    def test_manual_run_does_not_publish_by_default(self):
        self.assertRegex(self.text, r"(?s)publish:\n.*?type: boolean\n\s+default: false\n")

    def test_interval_job_asks_the_tested_rule(self):
        self.assertTrue(self.interval, "falta o job interval")
        self.assertIn("scripts/due.py", self.interval)
        self.assertIn("run: ${{ steps.age.outputs.run }}", self.interval)

    def test_published_date_comes_from_the_checkout(self):
        """Com o repositório privado, raw.githubusercontent.com responde 404: a data sumiria e o job geraria todo dia."""
        self.assertIn("actions/checkout@", self.interval)
        commands = "\n".join(line for line in self.text.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("raw.githubusercontent.com", commands)

    def test_manual_run_always_generates(self):
        self.assertIn("github.event_name != 'schedule' && '--manual' || ''", self.interval)

    def test_a_fork_does_not_generate_on_schedule(self):
        self.assertIn("if: github.event_name != 'schedule' || github.repository == 'Murkiriel/andorinha'",
                      self.interval)

    def test_build_waits_for_the_interval_job(self):
        self.assertIn("needs: interval\n", self.generate)
        self.assertIn("if: needs.interval.outputs.run == 'true'\n", self.generate)

    def test_scheduled_run_publishes_and_manual_only_if_asked(self):
        self.assertIn("PUBLISH: ${{ github.event_name == 'schedule' || inputs.publish == true }}", self.generate)
        publish_step = self.generate[self.generate.index("- name: Publicar"):]
        self.assertIn("if: env.PUBLISH == 'true'\n", publish_step)
        self.assertNotIn("inputs.publish }}", publish_step)

    def test_manual_run_can_choose_the_extract_source(self):
        """Para testar a Geofabrik no Actions sem esperar o openstreetmap.fr cair; vazio = a ordem padrão."""
        self.assertRegex(self.text, r'(?s)osm_sources:\n.*?type: string\n\s+default: ""\n')
        self.assertIn("ANDORINHA_OSM_SOURCES: ${{ inputs.osm_sources }}\n", self.generate)

    def test_failed_scheduled_run_is_reported_in_an_issue(self):
        """De 2026-11-03 a 2026-11-08, 11 gerações agendadas falharam sem ninguém saber."""
        report = job(self.text, "alert")
        self.assertTrue(report, "falta o job alert")
        self.assertIn("needs: [interval, generate]\n", report)
        self.assertIn("if: always() && github.event_name == 'schedule' && "
                      "github.repository == 'Murkiriel/andorinha'\n", report)
        self.assertIn("scripts/alert.py", report)
        for value in ("${{ needs.interval.result }}", "${{ needs.generate.result }}", "${{ github.run_id }}"):
            self.assertIn(value, report)

    def test_only_the_alert_job_writes_issues(self):
        top = self.text.split("\njobs:\n", 1)[0]
        self.assertNotIn("issues:", top)
        self.assertIn("      issues: write\n", job(self.text, "alert"))
        self.assertNotIn("issues:", self.generate)

    def test_one_generation_at_a_time(self):
        self.assertRegex(self.text, r"(?m)^concurrency:\n  group: generate\n  cancel-in-progress: false")


if __name__ == "__main__":
    unittest.main()
