"""Quando gerar de novo: toda semana, no domingo. O agendamento dispara todo dia, e só segue para o build quando a
geração publicada (`built_at` do catalogo.json) é de antes do último domingo, 00:00 em Brasília. Assim, num domingo
normal, gera no primeiro disparo e os seguintes só conferem a data; se o domingo falhar, os dias seguintes tentam de
novo até sair. A linha de comando fica em scripts/due.py.

Até 2026-11-08 a regra era "a cada 29 dias desde a geração publicada", com uma geração extra marcada; mudou para
semanal porque os dados mudam pouco de uma semana para outra e o domingo fica previsível para quem usa os pacotes."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

# O domingo é o de Brasília: sem horário de verão desde 2019, sempre UTC-3.
BRASILIA = timezone(timedelta(hours=-3), "Brasília")


def catalog_built_at(catalog: Path) -> Optional[str]:
    """O `built_at` do catálogo publicado; None se o catálogo não existe, não é JSON ou não tem a data."""
    try:
        built_at = json.loads(catalog.read_text(encoding="utf-8")).get("built_at")
    except (OSError, ValueError, AttributeError):
        return None
    return built_at if isinstance(built_at, str) else None


def week_start(now: datetime) -> datetime:
    """O domingo mais recente (o de hoje, se hoje é domingo), 00:00 em Brasília, em UTC."""
    local = now.astimezone(BRASILIA)
    sunday = local.date() - timedelta(days=(local.weekday() + 1) % 7)  # weekday(): segunda 0 ... domingo 6
    return datetime(sunday.year, sunday.month, sunday.day, tzinfo=BRASILIA).astimezone(timezone.utc)


def _day(moment: datetime) -> str:
    return moment.astimezone(BRASILIA).strftime("%d/%m")


def generation_due(built_at: Optional[str], now: datetime, manual: bool = False) -> Tuple[bool, str]:
    """(gera?, motivo). Gera se a geração publicada é de antes do último domingo (00:00 em Brasília)."""
    if manual:
        return True, "disparado à mão: gera"
    try:
        built = datetime.strptime(built_at or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return True, "sem a data da geração publicada: gera"
    start = week_start(now)
    if built < start:
        return True, f"geração publicada em {built_at}, antes do domingo {_day(start)}: gera"
    return False, (f"geração publicada em {built_at}, já nesta semana (desde o domingo {_day(start)}): "
                   f"ainda não, gera no domingo {_day(start + timedelta(days=7))}")
