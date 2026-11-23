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
        self.assertEqual(3, seen)  # o job weights também instala do requirements.txt

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
        self.assertEqual(10, seen)


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
        """A publicação é do job generate, como sempre: nada entre a geração e ela (sem artefato, sem outro job)."""
        self.assertIn("PUBLISH: ${{ github.event_name == 'schedule' || inputs.publish == true }}", self.generate)
        publish_step = self.generate[self.generate.index("- name: Publicar"):]
        self.assertIn("if: env.PUBLISH == 'true'\n", publish_step)
        self.assertIn("python scripts/publish.py --release --commit --push", publish_step)
        self.assertNotIn("inputs.publish }}", publish_step)

    def test_manual_run_can_choose_the_extract_source(self):
        """Para testar a Geofabrik no Actions sem esperar o openstreetmap.fr cair; vazio = a ordem padrão."""
        self.assertRegex(self.text, r'(?s)osm_sources:\n.*?type: string\n\s+default: ""\n')
        self.assertIn("ANDORINHA_OSM_SOURCES: ${{ inputs.osm_sources }}\n", self.generate)

    def test_failed_scheduled_run_is_reported_in_an_issue(self):
        """De 2026-11-03 a 2026-11-08, 11 gerações agendadas falharam sem ninguém saber."""
        report = job(self.text, "alert")
        self.assertTrue(report, "falta o job alert")
        self.assertIn("if: always() && github.event_name == 'schedule' && "
                      "github.repository == 'Murkiriel/andorinha'\n", report)
        self.assertIn("scripts/alert.py", report)
        for value in ("${{ needs.interval.result }}", "${{ needs.generate.result }}", "${{ github.run_id }}"):
            self.assertIn(value, report)

    def test_the_alert_job_also_reads_the_weights_study(self):
        """O estudo dos pesos roda depois da publicação e falhava sem aviso: o alert espera por ele e abre a issue dele."""
        report = job(self.text, "alert")
        self.assertIn("needs: [interval, generate, weights]\n", report)
        self.assertIn("WEIGHTS_RESULT: ${{ needs.weights.result }}", report)
        self.assertIn('--weights "$WEIGHTS_RESULT"', report)

    def test_the_alert_job_reads_the_catalog_as_it_is_now(self):
        """A "Publicação parada" lê a idade do catalogo.json: do ramo de agora, que o disparo pode ter acabado de
        atualizar, e não do commit do disparo (que ficaria velho e abriria a issue por engano)."""
        report = job(self.text, "alert")
        self.assertIn("actions/checkout@", report)
        self.assertIn("ref: ${{ github.ref }}", report)

    def test_the_alert_job_runs_in_every_scheduled_run_whatever_the_interval_said(self):
        """A idade da geração publicada se confere também quando o interval diz "ainda não" e o build é pulado."""
        report = job(self.text, "alert")
        self.assertIn("if: always() &&", report)
        self.assertNotIn("needs.interval.outputs", report)
        self.assertNotIn("generate.result ==", report)

    def test_only_the_alert_job_writes_issues(self):
        top = self.text.split("\njobs:\n", 1)[0]
        self.assertNotIn("issues:", top)
        self.assertIn("      issues: write\n", job(self.text, "alert"))
        self.assertNotIn("issues:", self.generate)



class WeightsStudyJobTest(unittest.TestCase):
    """O estudo dos pesos da moto roda DEPOIS da publicação, num job à parte, sem artefato do Actions no caminho da
    geração: falhar, estourar o tempo ou faltar não muda nada do que foi publicado."""

    def setUp(self):
        self.text = workflows()["gerar.yml"].replace("\r\n", "\n")
        self.generate = job(self.text, "generate")
        self.weights = job(self.text, "weights")

    def commands(self, text):
        return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))

    def test_there_are_no_other_jobs(self):
        names = re.findall(r"(?m)^  (\w[\w-]*):\n", self.text.split("\njobs:\n", 1)[1])
        self.assertEqual(["interval", "generate", "weights", "alert"], names)

    def test_no_artifact_is_needed_by_anything(self):
        """A cota de artefatos da conta pode acabar: o único upload do job generate é de diagnóstico e não derruba o job,
        e o weights não baixa artefato nenhum (os pacotes vêm da release, pelas URLs públicas)."""
        self.assertNotIn("download-artifact", self.text)
        self.assertNotIn("name: geracao", self.text)
        for name in ("generate", "weights"):
            text = job(self.text, name)
            for match in re.finditer(r"- uses: actions/upload-artifact@[^\n]*\n(.*?)(?=\n      - |\Z)", text, flags=re.DOTALL):
                self.assertIn("continue-on-error: true", match.group(1), f"{name}: upload de artefato que pode derrubar o job")

    def test_study_waits_for_the_publication_and_only_runs_if_something_was_published(self):
        self.assertIn("needs: generate\n", self.weights)
        self.assertIn("if: needs.generate.result == 'success' && needs.generate.outputs.build_id != ''", self.weights)
        self.assertIn("build_id: ${{ steps.release.outputs.build_id }}", self.generate)
        publish_step = self.generate[self.generate.index("- name: Publicar"):]
        self.assertIn("id: release", publish_step)
        self.assertIn("$GITHUB_OUTPUT", publish_step)

    def test_the_publication_comes_before_anything_of_the_study(self):
        self.assertNotIn("weights.py", self.generate)
        self.assertNotIn("weights", self.commands(self.generate).replace("outputs.build_id", ""))

    def test_the_study_downloads_the_published_release_and_uploads_next_to_it(self):
        commands = self.commands(self.weights)
        self.assertIn('scripts/weights.py study --release "$TAG"', commands)
        self.assertIn('scripts/weights.py upload --tag "$TAG"', commands)
        self.assertIn("TAG: ${{ needs.generate.outputs.build_id }}", self.weights)

    def test_only_the_study_job_gets_the_token_to_upload_to_the_release(self):
        upload = self.weights[self.weights.index("- name: Subir o resultado na release"):]
        upload = upload[:upload.index("\n\n")]
        self.assertIn("GH_TOKEN: ${{ github.token }}", upload)
        self.assertIn("permissions:\n      contents: write", self.weights)

    def test_the_study_never_pushes_nor_touches_the_catalog(self):
        commands = self.commands(self.weights)
        for forbidden in ("git push", "git commit", "publish.py", "catalogo.json", "--clobber"):
            self.assertNotIn(forbidden, commands)

    def test_the_study_has_its_own_6_hour_limit_and_a_time_budget_inside_it(self):
        self.assertRegex(self.weights, r"timeout-minutes: 3[0-9]{2}\n")
        budget = int(re.search(r'ANDORINHA_WEIGHTS_MINUTES: "(\d+)"', self.weights).group(1))
        limit = int(re.search(r"timeout-minutes: (\d+)", self.weights).group(1))
        self.assertLessEqual(limit, 360)
        self.assertGreaterEqual(limit - budget, 30)  # folga para baixar, extrair e subir

    def test_the_upload_only_happens_if_the_study_gave_a_result(self):
        """Os passos são em fila: o upload não roda se o study falhou (sem `if: always()` nele)."""
        upload = self.weights[self.weights.index("- name: Subir o resultado na release"):]
        step = upload[:upload.index("\n\n")]
        self.assertNotIn("always()", step)

    def test_a_manual_run_can_ask_for_a_test_batch(self):
        self.assertRegex(self.text, r'(?s)weights_batch:\n.*?type: string\n\s+default: ""\n')
        self.assertIn("ANDORINHA_WEIGHTS_BATCH: ${{ inputs.weights_batch }}", self.weights)


class ScheduledGenerationRulesTest(unittest.TestCase):
    def setUp(self):
        self.text = workflows()["gerar.yml"].replace("\r\n", "\n")
        self.generate = job(self.text, "generate")

    def test_one_generation_at_a_time(self):
        self.assertRegex(self.text, r"(?m)^concurrency:\n  group: generate\n  cancel-in-progress: false")


if __name__ == "__main__":
    unittest.main()
