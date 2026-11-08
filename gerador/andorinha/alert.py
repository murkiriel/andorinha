"""Aviso de geração agendada que falhou: a issue fixa "Geração falhando" no repositório.

De 2026-11-03 a 2026-11-08, 11 gerações agendadas falharam (a API do IBGE não respondia ao Actions) sem ninguém
saber: o GitHub só manda e-mail para quem editou o cron por último, e só com as notificações de Actions ligadas. Uma
issue notifica quem acompanha o repositório e fica visível na página dele.

O job `alert` do workflow gerar chama scripts/alert.py no fim de toda execução agendada:
- o interval ou o build falhou: abre a issue, ou comenta nela se já estiver aberta, com o link da execução;
- o build passou (gerou e publicou): comenta e fecha a issue, se estiver aberta;
- o build foi pulado (não era hora de gerar) ou cancelado: não faz nada, nem consulta o GitHub.

Só a stdlib e o config: o job roda no python3 da máquina, sem instalar as dependências do gerador.
"""
from __future__ import annotations

import json
import subprocess
from typing import Callable, Optional, Sequence, Tuple

from . import config

TITLE = "Geração falhando"

Gh = Callable[[Sequence[str]], Tuple[int, str, str]]


class AlertError(RuntimeError):
    """Um comando do gh falhou; a mensagem traz o comando e o que o gh disse."""


def gh_real(args: Sequence[str]) -> Tuple[int, str, str]:
    """O `gh` de verdade, no repositório do projeto."""
    r = subprocess.run(["gh", *args, "--repo", config.GITHUB_REPO], capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def _run(gh: Gh, args: Sequence[str]) -> str:
    code, output, error = gh(list(args))
    if code != 0:
        raise AlertError(f"gh {' '.join(args[:2])} falhou (código {code}): {error.strip() or output.strip()}")
    return output


def outcome(interval: str, generate: str) -> str:
    """'falhou', 'ok' ou 'nada', a partir dos resultados dos jobs (success, failure, cancelled, skipped)."""
    if interval == "failure" or generate == "failure":
        return "falhou"
    if generate == "success":
        return "ok"
    return "nada"


def open_issue(gh: Gh) -> Optional[int]:
    """O número da issue aberta com o título exato, ou None. A lista, e não a busca, que demora a ver issue nova."""
    issues = json.loads(_run(gh, ["issue", "list", "--state", "open", "--limit", "200", "--json", "number,title"]))
    numbers = [i["number"] for i in issues if i.get("title") == TITLE]
    return min(numbers) if numbers else None


def sync(gh: Gh, result: str, run_url: str, when: str) -> str:
    """Põe a issue de acordo com o resultado. Devolve o que fez: aberta, comentada, fechada ou nada."""
    if result not in ("falhou", "ok"):
        return "nada"
    number = open_issue(gh)
    if result == "falhou":
        if number is None:
            body = (f"A geração agendada de {when} falhou: {run_url}\n\n"
                    "A release publicada continua valendo. O disparo seguinte tenta de novo; cada nova falha comenta "
                    "aqui, e esta issue fecha sozinha na primeira geração agendada que passar.")
            _run(gh, ["issue", "create", "--title", TITLE, "--body", body])
            return "aberta"
        _run(gh, ["issue", "comment", str(number), "--body", f"Falhou de novo em {when}: {run_url}"])
        return "comentada"
    if number is None:
        return "nada"
    _run(gh, ["issue", "close", str(number), "--comment", f"A geração agendada de {when} passou: {run_url}"])
    return "fechada"
