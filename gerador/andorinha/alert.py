"""Avisos do workflow gerar, em issues fixas do repositório: "Geração falhando", "Estudo dos pesos falhando" e
"Publicação parada".

De 2026-11-03 a 2026-11-08, 11 gerações agendadas falharam (a API do IBGE não respondia ao Actions) sem ninguém
saber: o GitHub só manda e-mail para quem editou o cron por último, e só com as notificações de Actions ligadas. Uma
issue notifica quem acompanha o repositório e fica visível na página dele.

O job `alert` do workflow gerar chama scripts/alert.py no fim de toda execução agendada. Para cada issue, o mesmo
desenho: falhou, abre a issue (ou comenta nela, se já estiver aberta) com o link da execução; deu certo, comenta e
fecha, se estiver aberta; não houve o que conferir, não faz nada, nem consulta o GitHub.
- "Geração falhando": o interval ou o generate falhou; passou quando o generate gerou e publicou.
- "Estudo dos pesos falhando": o job weights falhou (ele roda depois da publicação, e falhar não muda nada do que foi
  publicado, mas os pesos pararam de se renovar sem ninguém ver); passou quando o weights passou; pulado não conta.
- "Publicação parada": a geração publicada (`built_at` do catalogo.json) tem mais de `STALE_DAYS` dias. Cobre o que as
  outras duas não veem: o agendamento do GitHub que não dispara, o interval que diz "ainda não" por engano, uma release
  que não sobe. Confere em todo disparo agendado, mesmo quando o build é pulado.

Só a stdlib e o config: o job roda no python3 da máquina, sem instalar as dependências do gerador.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Sequence, Tuple

from . import config

# A geração é semanal, no domingo: a próxima já devia ter saído 7 dias depois, e mais 2 dias de folga para um
# agendamento atrasado (7 a 10 horas em 2026-11) e para uma falha que o disparo seguinte conserta.
STALE_DAYS = 9

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


@dataclass(frozen=True)
class Notice:
    """Uma issue fixa: o título e os textos de abrir, comentar e fechar. `{when}`, `{run_url}` e `{detail}` entram."""
    title: str
    opening: str
    again: str
    closing: str


GENERATION = Notice(
    "Geração falhando",
    "A geração agendada de {when} falhou: {run_url}\n\n"
    "A release publicada continua valendo. O disparo seguinte tenta de novo; cada nova falha comenta aqui, e esta "
    "issue fecha sozinha na primeira geração agendada que passar.",
    "Falhou de novo em {when}: {run_url}",
    "A geração agendada de {when} passou: {run_url}")

WEIGHTS = Notice(
    "Estudo dos pesos falhando",
    "O estudo dos pesos de {when} falhou: {run_url}\n\n"
    "A geração e a release publicadas não mudam, mas os pesos da moto pararam de ser refeitos: a geração seguinte "
    "parte das tabelas da última vez que o estudo deu certo. Cada nova falha comenta aqui, e esta issue fecha sozinha "
    "na primeira vez que o estudo passar.",
    "Falhou de novo em {when}: {run_url}",
    "O estudo dos pesos de {when} passou: {run_url}")

STALE = Notice(
    "Publicação parada",
    "{detail}\n\nA semanal já devia ter saído: o agendamento pode não ter disparado, o job interval pode ter dito "
    "\"ainda não\" por engano ou a release pode não ter subido. Execução que conferiu: {run_url}\n\n"
    "Cada conferência seguinte comenta aqui, e esta issue fecha sozinha quando a geração publicada voltar a ser "
    "recente.",
    "Segue parada em {when}: {detail} ({run_url})",
    "A geração publicada voltou a ser recente em {when}: {detail} ({run_url})")

TITLE = GENERATION.title

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


def weights_outcome(weights: str) -> str:
    """'falhou', 'ok' ou 'nada' para o job weights. Pulado (não houve publicação) ou cancelado não diz nada: a
    issue fica como está."""
    return {"failure": "falhou", "success": "ok"}.get(weights, "nada")


def stale_outcome(built_at: Optional[str], now: datetime) -> Tuple[str, str]:
    """('falhou', 'ok' ou 'nada', detalhe) para a idade da geração publicada. Sem a data não dá para dizer: 'nada'
    (o job interval já manda gerar quando o catálogo não tem `built_at`)."""
    try:
        built = datetime.strptime(built_at or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return "nada", "o catálogo publicado não tem a data da geração"
    age = now - built
    days = age.total_seconds() / 86400
    detail = f"a geração publicada é de {built_at} ({days:.1f} dias)"
    if age > timedelta(days=STALE_DAYS):
        return "falhou", f"{detail}, mais velha que o limite de {STALE_DAYS} dias"
    return "ok", detail


def open_issue(gh: Gh, title: str = TITLE) -> Optional[int]:
    """O número da issue aberta com o título exato, ou None. A lista, e não a busca, que demora a ver issue nova."""
    issues = json.loads(_run(gh, ["issue", "list", "--state", "open", "--limit", "200", "--json", "number,title"]))
    numbers = [i["number"] for i in issues if i.get("title") == title]
    return min(numbers) if numbers else None


def sync(gh: Gh, result: str, run_url: str, when: str, notice: Notice = GENERATION, detail: str = "") -> str:
    """Põe a issue de `notice` de acordo com o resultado. Devolve o que fez: aberta, comentada, fechada ou nada."""
    if result not in ("falhou", "ok"):
        return "nada"
    text = dict(when=when, run_url=run_url, detail=detail)
    number = open_issue(gh, notice.title)
    if result == "falhou":
        if number is None:
            _run(gh, ["issue", "create", "--title", notice.title, "--body", notice.opening.format(**text)])
            return "aberta"
        _run(gh, ["issue", "comment", str(number), "--body", notice.again.format(**text)])
        return "comentada"
    if number is None:
        return "nada"
    _run(gh, ["issue", "close", str(number), "--comment", notice.closing.format(**text)])
    return "fechada"
