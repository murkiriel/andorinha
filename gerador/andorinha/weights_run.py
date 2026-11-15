"""A execução do estudo de um lote: escolhe o que refazer, roda o estudo, junta os resultados na tabela e grava o que a
publicação precisa. A linha de comando fica em scripts/weights.py; aqui a lógica, testável com um estudo falso.

Entradas: as tiles da geração (a pasta de tiles com a base e as UFs extraídas juntas), o `build_id` dela, a tabela
de que se parte e o dia. Saída, em `out`:
- `pesos_moto_municipios.csv` e `pesos_moto_estados.csv`: as tabelas com as linhas refeitas (só se alguma mudou);
- `estudo.json`: o que foi escolhido, o que terminou, os pesos de cada unidade e os tempos.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import config, municipalities, packing, weights_study, weights_tables
from .municipalities import STATE_WEIGHTS_FILE, WEIGHTS_FILE
from .weights_study import Outcome

SUMMARY_FILE = "estudo.json"
MUNICIPALITY_SHARE = 0.7  # o lote dos municípios pára de começar municípios aos 70% do orçamento; o estado tem o resto


@dataclass
class Plan:
    """O que o estudo vai fazer."""
    tiles_dir: Path
    build_id: str
    out: Path
    tables_dir: Optional[Path] = None   # de onde vêm as tabelas de partida; sem ela, a semente do repositório
    date: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    workers: int = config.WEIGHTS_WORKERS
    batch: int = config.WEIGHTS_BATCH
    states: int = config.WEIGHTS_STATES
    minutes: float = config.WEIGHTS_MINUTES
    only_codes: Optional[List[str]] = None   # municípios escolhidos à mão (em vez do lote da rotação)
    only_states: Optional[List[str]] = None  # UFs escolhidas à mão
    state_goal: int = weights_study.STATE_TRIP_GOAL
    state_destinations: int = weights_study.STATE_DESTINATIONS
    state_origins: int = weights_study.STATE_ORIGINS


@dataclass
class Result:
    municipalities: List[Outcome] = field(default_factory=list)
    states: List[Outcome] = field(default_factory=list)
    selected_codes: List[str] = field(default_factory=list)
    selected_states: List[str] = field(default_factory=list)
    skipped_states: Dict[str, str] = field(default_factory=dict)
    changed: bool = False
    seconds: float = 0.0


def _progress(done: int, total: int, started: float) -> str:
    elapsed = time.time() - started
    eta = elapsed / done * (total - done) if done else 0
    return f"{done}/{total}, {elapsed / 60:.1f} min, faltam ~{eta / 60:.1f} min"


def load_start_tables(plan: Plan, mesh: Dict[str, dict]):
    """(textos, pesos dos municípios, pesos dos estados) de que o estudo parte."""
    states = {municipalities.state_of(code) for code in mesh}
    if plan.tables_dir is None:
        return weights_tables.seed_tables(mesh, states)
    texts = municipalities.TableTexts((plan.tables_dir / WEIGHTS_FILE).read_bytes().decode("utf-8"),
                                      (plan.tables_dir / STATE_WEIGHTS_FILE).read_bytes().decode("utf-8"),
                                      str(plan.tables_dir))
    weights, state_weights = weights_tables.check_tables(texts.weights, texts.state_weights, mesh, states)
    return texts, weights, state_weights


def run(plan: Plan, pool_factory: Optional[Callable] = None, actor_factory: Optional[Callable] = None,
        clock: Callable[[], float] = time.time) -> Result:
    """Faz o estudo do plano. Levanta weights_tables.TableError se a tabela de partida não serve. Um estado que não
    termina no tempo, ou que falha, fica de fora e é dito em `skipped_states`; os municípios que terminaram valem
    mesmo assim."""
    pool_factory = pool_factory or weights_study.Pool
    actor_factory = actor_factory or weights_study.open_actor
    started = clock()
    mesh = municipalities.load_mesh()
    texts, weights, state_weights = load_start_tables(plan, mesh)
    all_states = sorted({municipalities.state_of(code) for code in mesh})
    result = Result()
    result.selected_codes = plan.only_codes if plan.only_codes is not None \
        else weights_tables.select_batch(weights, mesh, plan.batch)
    result.selected_states = plan.only_states if plan.only_states is not None \
        else weights_tables.select_states(state_weights, all_states, plan.states)
    unknown = [c for c in result.selected_codes if c not in mesh] + \
              [s for s in result.selected_states if s not in all_states]
    if unknown:
        raise weights_tables.TableError(f"unidades que não estão na malha: {', '.join(unknown)}")
    deadline = started + plan.minutes * 60
    municipality_deadline = started + plan.minutes * 60 * (MUNICIPALITY_SHARE if result.selected_states else 1.0)
    print(f"[pesos] lote: {len(result.selected_codes)} municípios e {len(result.selected_states)} estado(s) "
          f"({', '.join(result.selected_states) or '-'}), {plan.workers} processos, orçamento de {plan.minutes:.0f} min")
    plan.out.mkdir(parents=True, exist_ok=True)

    with pool_factory(plan.tiles_dir, plan.workers) as pool:
        if result.selected_codes:
            todo = [(code, mesh[code]) for code in result.selected_codes]
            counter = [0]

            def on_result(outcome: Outcome) -> None:
                counter[0] += 1
                if counter[0] % 25 == 0 or counter[0] == len(todo):
                    print(f"[pesos] municípios {_progress(counter[0], len(todo), started)}", flush=True)

            result.municipalities = pool.municipalities(todo, municipality_deadline, on_result)
            if len(result.municipalities) < len(todo):
                print(f"[pesos] AVISO: o orçamento de tempo acabou com {len(result.municipalities)} de {len(todo)} "
                      "municípios; o resto fica para a geração seguinte")
        for state in result.selected_states:
            try:
                result.states.append(_study_state(plan, state, pool, actor_factory, deadline))
            except (TimeoutError, RuntimeError) as e:
                result.skipped_states[state] = f"{e.__class__.__name__}: {e}"
                print(f"[pesos] AVISO: o estado {state} não entrou nesta geração ({result.skipped_states[state]})")

    new_weights, updated = weights_tables.merge(weights, result.municipalities, plan.build_id, config.VALHALLA_VERSION,
                                                plan.date)
    new_states, state_updated = weights_tables.merge(state_weights, result.states, plan.build_id,
                                                     config.VALHALLA_VERSION, plan.date)
    result.changed = bool(updated or state_updated)
    if result.changed:
        (plan.out / WEIGHTS_FILE).write_bytes(weights_tables.serialize_weights(new_weights).encode("utf-8"))
        (plan.out / STATE_WEIGHTS_FILE).write_bytes(weights_tables.serialize_state_weights(new_states).encode("utf-8"))
    result.seconds = clock() - started
    write_summary(plan, result, texts.origin, updated, state_updated)
    print(f"[pesos] {updated} municípios e {state_updated} estado(s) refeitos em {result.seconds / 60:.1f} min")
    return result


def _study_state(plan: Plan, state: str, pool, actor_factory: Callable, deadline: float) -> Outcome:
    """O estudo de uma UF: o sorteio no processo principal (precisa de um Actor para achar a via mais próxima) e as
    rotas nos processos do `pool`."""
    mesh_path = _uf_mesh()
    polygon = packing.state_polygons(mesh_path)[state]
    actor = actor_factory(plan.tiles_dir)
    locate = lambda point: weights_study.road_distance(actor, point)  # noqa: E731
    route = weights_study.Router(actor)
    print(f"[pesos] estado {state}: sorteando e medindo as viagens...", flush=True)
    outcome = weights_study.study_state(state, polygon, locate, route, pool.measurer(deadline=deadline),
                                        goal=plan.state_goal, destinations=plan.state_destinations,
                                        origins=plan.state_origins, deadline=deadline)
    print(f"[pesos] estado {state}: {outcome.counted} viagens contaram de {outcome.trips}, "
          f"peso {outcome.weight}, {outcome.seconds / 60:.1f} min", flush=True)
    return outcome


def _uf_mesh() -> Path:
    """A cópia versionada da malha das UFs, conferida (a mesma que decide os pacotes)."""
    from . import sources

    return sources.ibge_mesh()


def write_summary(plan: Plan, result: Result, origin: str, updated: int, state_updated: int) -> None:
    def item(o: Outcome) -> dict:
        return {"key": o.key, "weight": list(o.weight) if o.weight else None,
                "center": list(o.center) if o.center else None, "trips": o.trips, "counted": o.counted,
                "attempts": o.attempts, "seconds": round(o.seconds, 1), "reason": o.reason}

    summary = {
        "build_id": plan.build_id, "valhalla_version": config.VALHALLA_VERSION, "date": plan.date,
        "started_from": origin, "workers": plan.workers, "minutes_budget": plan.minutes,
        "minutes": round(result.seconds / 60, 2),
        "municipalities": {"selected": len(result.selected_codes), "finished": len(result.municipalities),
                           "updated": updated,
                           "without_weight": sorted(o.key for o in result.municipalities if o.weight is None),
                           "results": [item(o) for o in sorted(result.municipalities, key=lambda o: o.key)]},
        "states": {"selected": result.selected_states, "updated": state_updated,
                   "skipped": result.skipped_states, "results": [item(o) for o in result.states]},
    }
    (plan.out / SUMMARY_FILE).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare_tiles(dist: Path, work: Path) -> Path:
    """Extrai os pacotes de uma geração (a base e as UFs de `dist`, pelo catalogo.json) numa pasta só: os tiles do
    Brasil, como o motor os vê. Devolve a pasta."""
    from . import publishing

    cat = json.loads((dist / "catalogo.json").read_text(encoding="utf-8"))
    tiles = work / "tiles"
    tiles.mkdir(parents=True, exist_ok=True)
    names = publishing.entries(cat)
    for name in ["base"] + sorted(n for n in names if n != "base"):
        packing.extract_package(dist / names[name]["file"], tiles)
    return tiles
