"""O estudo dos pesos da moto, feito pelo gerador sobre os tiles da própria geração.

Os pesos de `pesos_moto_municipios.csv` e `pesos_moto_estados.csv` (andorinha/municipalities.py) saem daqui. O estudo
inteiro do país não cabe numa execução (o dos municípios leva ~7 h numa máquina de 12 núcleos): ele roda em lotes, uns
600 municípios e um estado por geração, escolhidos por andorinha/weights_tables.py. Este módulo faz o estudo de UMA
unidade, município ou estado, e a parte paralela de um lote.

O método, o mesmo dos estudos que geraram as tabelas de 2026-11-14:
1. **Amostra.** Município: 32 origens e 32 destinos sorteados de modo uniforme dentro do contorno (malha municipal do
   IBGE), cada um a até 150 m de uma via (1.024 viagens). Estado: 100 destinos e 100 origens uniformes no polígono da
   UF, a até 150 m de uma via; se as viagens que têm rota em toda a grade não chegam a 10.000, entram mais origens, de
   10 em 10, até chegar (no máximo 1.000 origens).
2. **Grade.** Cada viagem é roteada com o costing `motorcycle` (`use_tolls` 1,0, a config de quem usa os pacotes) para
   `use_highways` em 0,2, 0,4, 0,6 e 0,8 e `use_trails` em 0,2 e 0,4: as combinações com `use_trails` até o teto
   (config.MOTORCYCLE_WEIGHTS_MAX_USE_TRAILS). Só contam as viagens que têm rota em todas as combinações.
3. **Centro.** A combinação de menor tempo médio; no empate, a de menor distância média e, depois, a mais perto de
   0,6/0,4.
4. **Refino.** As vizinhas a 0,1 do centro (com `use_trails` até o teto) são roteadas nas mesmas viagens; vence o menor
   tempo médio, e no empate fica o centro.

Os estudos de 2026-11-14 roteavam as 16 combinações da grade (`use_trails` até 0,8) e só depois aplicavam o teto de
0,4: como o teto tira as combinações de 0,6 e 0,8 do concurso, aqui elas nem são roteadas (metade da grade a menos).
Quem vence é o mesmo, a menos de uma viagem que tenha rota com `use_trails` 0,4 e não com 0,6 ou 0,8, o que tira ou
põe uma viagem em mil.

Cada unidade devolve um `Outcome`; o sorteio é determinístico (semente fixa mais o código da unidade): com os mesmos
tiles, a mesma unidade dá o mesmo peso.
"""
from __future__ import annotations

import json
import math
import random
import time
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, TimeoutError as FutureTimeout, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from . import config

Point = Tuple[float, float]                  # (latitude, longitude)
Combo = Tuple[float, float]                  # (use_highways, use_trails)
Measure = Optional[Tuple[int, int]]          # (décimos de segundo, metros) de uma rota, ou None se não há rota
Row = List[Measure]                          # as medidas de uma viagem, uma por combinação
Pair = Tuple[Point, Point]                   # (origem, destino)

GRID_VALUES = (0.2, 0.4, 0.6, 0.8)
REFERENCE = (0.6, 0.4)                       # o desempate final: a mais perto da melhor combinação única do país
MAX_ROAD_DISTANCE_M = 150.0                  # um ponto sorteado só vale a até 150 m de uma via
MUNICIPALITY_POINTS = 32                     # origens e destinos por município (32 x 32 viagens)
MUNICIPALITY_ATTEMPTS = 50_000               # sorteios de um município antes de desistir (as 64 vagas juntas)
STATE_DESTINATIONS = 100
STATE_ORIGINS = 100
STATE_TRIP_GOAL = 10_000                     # viagens com rota em toda a grade
STATE_ORIGIN_STEP = 10
STATE_MAX_ORIGINS = 1_000
STATE_ATTEMPTS = 2_000_000                   # sorteios de um estado antes de desistir
MUNICIPALITY_SEED = 20261120                 # as mesmas sementes dos estudos de 2026-11-14, para reproduzir os pontos
STATE_SEED = 20261124
ROUTE_CACHE_BYTES = 300_000_000              # cache de tiles de cada processo (o padrão do Valhalla é maior)


# ---------------------------------------------------------------------------------------------------------------------
# A grade, o refino e a escolha: puros, sem Valhalla.

def grid(max_trails: float = config.MOTORCYCLE_WEIGHTS_MAX_USE_TRAILS) -> List[Combo]:
    """As combinações da grade, `use_highways` de fora e `use_trails` de dentro, só as com `use_trails` até o teto."""
    return [(u, t) for u in GRID_VALUES for t in GRID_VALUES if t <= max_trails + 1e-9]


def neighbours(center: Combo, max_trails: float = config.MOTORCYCLE_WEIGHTS_MAX_USE_TRAILS) -> List[Combo]:
    """As vizinhas a 0,1 do centro (3 x 3 em volta, menos ele), com os dois pesos entre 0 e 1 e `use_trails` até o
    teto."""
    out = []
    for a in (-0.1, 0, 0.1):
        for b in (-0.1, 0, 0.1):
            if (a, b) == (0, 0):
                continue
            highways, trails = round(center[0] + a, 2), round(center[1] + b, 2)
            if 0 < highways <= 1 and 0 < trails <= max_trails + 1e-9:
                out.append((highways, trails))
    return out


def complete(rows: Sequence[Row]) -> List[bool]:
    """Para cada viagem, se tem rota em todas as combinações medidas."""
    return [bool(row) and all(m is not None for m in row) for row in rows]


def _sums(rows: Sequence[Row], usable: Sequence[bool], column: int) -> Tuple[int, int]:
    """(soma do tempo, soma da distância) da combinação `column` nas viagens usáveis. Somas em vez de médias: o número
    de viagens é o mesmo em todas as combinações, e a conta com inteiros não tem desempate por arredondamento."""
    time_sum = meters_sum = 0
    for row, ok in zip(rows, usable):
        if ok:
            time_sum += row[column][0]
            meters_sum += row[column][1]
    return time_sum, meters_sum


def pick_center(rows: Sequence[Row], combos: Sequence[Combo]) -> Optional[Combo]:
    """O centro da grade: menor tempo médio, depois menor distância média, depois a mais perto de 0,6/0,4, depois a
    primeira da lista. None se nenhuma viagem tem rota em toda a grade."""
    usable = complete(rows)
    if not any(usable):
        return None

    def key(k: int):
        time_sum, meters_sum = _sums(rows, usable, k)
        c = combos[k]
        return time_sum, meters_sum, (c[0] - REFERENCE[0]) ** 2 + (c[1] - REFERENCE[1]) ** 2, k

    return combos[min(range(len(combos)), key=key)]


def pick_best(grid_rows: Sequence[Row], combos: Sequence[Combo], center: Combo,
              neighbour_rows: Sequence[Row], neighbour_combos: Sequence[Combo]) -> Tuple[Combo, int]:
    """(o peso vencedor, quantas viagens contaram). Contam as viagens com rota em toda a grade e em todas as vizinhas;
    vence o menor tempo médio entre o centro e as vizinhas, e no empate fica o centro."""
    usable = [g and n for g, n in zip(complete(grid_rows), complete(neighbour_rows))] if neighbour_combos \
        else complete(grid_rows)
    count = sum(1 for ok in usable if ok)
    if not count:
        return center, 0
    center_column = list(combos).index(center)
    candidates = [(_sums(grid_rows, usable, center_column)[0], 0, center)]
    for k, combo in enumerate(neighbour_combos):
        candidates.append((_sums(neighbour_rows, usable, k)[0], k + 1, combo))
    best = min(candidates, key=lambda c: (c[0], c[1]))  # empate: o menor índice, que é o centro
    return best[2], count


# ---------------------------------------------------------------------------------------------------------------------
# O roteamento: o Valhalla fica atrás de duas funções, para o resto poder ser testado sem ele.

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância em metros entre dois pontos."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    delta = math.radians(lon2 - lon1)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(delta / 2) ** 2
    return 2 * 6371000 * math.asin(min(1.0, math.sqrt(h)))


def study_config(tiles_dir: Path) -> dict:
    """A config de quem usa os pacotes (engine.routing_config), com o cache de tiles menor: cada processo tem o seu."""
    from . import engine

    cfg = engine.routing_config(tiles_dir)
    cfg["mjolnir"]["max_cache_size"] = ROUTE_CACHE_BYTES
    return cfg


def open_actor(tiles_dir: Path):
    """Um `valhalla.Actor` sobre a pasta de tiles (as UFs e a base extraídas juntas)."""
    from . import engine

    if not (Path(tiles_dir) / "2").is_dir():
        raise RuntimeError(f"{tiles_dir} não parece uma pasta de tiles do Valhalla (falta o nível 2)")
    return engine.import_valhalla().Actor(study_config(Path(tiles_dir)))


def road_distance(actor, point: Point) -> Optional[float]:
    """A distância, em metros, do ponto à via mais próxima que o Valhalla acha para a moto; None se não acha."""
    try:
        reply = json.loads(actor.locate(json.dumps({"locations": [{"lat": point[0], "lon": point[1]}],
                                                    "costing": "motorcycle", "verbose": True})))
    except Exception:  # noqa: BLE001 - o Valhalla levanta RuntimeError e ValueError; ponto sem via é só um sorteio perdido
        return None
    edges = (reply[0].get("edges") or []) if reply else []
    if not edges:
        return None
    return min(haversine(point[0], point[1], e["correlated_lat"], e["correlated_lon"]) for e in edges)


def route_request(origin: Point, destination: Point, combo: Combo) -> dict:
    return {"locations": [{"lat": origin[0], "lon": origin[1]}, {"lat": destination[0], "lon": destination[1]}],
            "costing": config.MOTORCYCLE_WEIGHTS_COSTING,
            "costing_options": {config.MOTORCYCLE_WEIGHTS_COSTING: {
                "use_highways": combo[0], "use_tolls": config.MOTORCYCLE_WEIGHTS_USE_TOLLS, "use_trails": combo[1]}},
            "directions_options": {"units": "kilometers"}}


class Router:
    """Roteia uma viagem com uma combinação. `Router(actor)(origem, destino, combinação)` -> (décimos de segundo,
    metros) ou None se não há rota."""

    def __init__(self, actor) -> None:
        self.actor = actor

    def __call__(self, origin: Point, destination: Point, combo: Combo) -> Measure:
        try:
            summary = json.loads(self.actor.route(json.dumps(route_request(origin, destination, combo))))["trip"]["summary"]
        except Exception:  # noqa: BLE001 - sem rota o Valhalla levanta RuntimeError; para o estudo é só uma viagem a menos
            return None
        return round(summary["time"] * 10), round(summary["length"] * 1000)


RouteFn = Callable[[Point, Point, Combo], Measure]
LocateFn = Callable[[Point], Optional[float]]


def measure(route: RouteFn, pairs: Sequence[Pair], combos: Sequence[Combo], wanted: Optional[Sequence[bool]] = None,
            stop_on_first_failure: bool = False) -> List[Row]:
    """As medidas de cada viagem em cada combinação. `wanted` pula as viagens marcadas como falsas (a linha delas fica
    toda vazia). Com `stop_on_first_failure`, uma viagem sem rota na primeira combinação (um ponto isolado) não é
    tentada nas outras."""
    rows: List[Row] = []
    for i, (origin, destination) in enumerate(pairs):
        row: Row = [None] * len(combos)
        if wanted is None or wanted[i]:
            for k, combo in enumerate(combos):
                row[k] = route(origin, destination, combo)
                if row[k] is None and k == 0 and stop_on_first_failure:
                    break
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------------------------------------------------
# A amostra de um município.

def _outer_rings(geometry: dict) -> List[list]:
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    return polygons


def _inside_polygon(lon: float, lat: float, polygon: list) -> bool:
    """Dentro do anel de fora e fora dos buracos (regra par-ímpar)."""
    inside = False
    for index, ring in enumerate(polygon):
        crossing = False
        for i in range(len(ring)):
            x1, y1 = ring[i]
            x2, y2 = ring[i - 1]
            if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
                crossing = not crossing
        if index == 0:
            inside = crossing
        elif crossing:
            inside = False
        if not inside:
            break
    return inside


def _polygon_sampler(geometry: dict, rng: random.Random, give_up: int = 5_000_000) -> Callable[[], Optional[Point]]:
    """Um sorteador de pontos uniformes dentro do contorno (Polygon ou MultiPolygon do GeoJSON): sorteia no retângulo
    e descarta o que cai fora. Devolve None se o contorno não aceita nenhum ponto em `give_up` sorteios."""
    polygons = _outer_rings(geometry)
    boxes = []
    for polygon in polygons:
        xs = [p[0] for p in polygon[0]]
        ys = [p[1] for p in polygon[0]]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)

    def draw() -> Optional[Point]:
        for _ in range(give_up):
            lon, lat = rng.uniform(x0, x1), rng.uniform(y0, y1)
            for polygon, (bx0, by0, bx1, by1) in zip(polygons, boxes):
                if bx0 <= lon <= bx1 and by0 <= lat <= by1 and _inside_polygon(lon, lat, polygon):
                    return lat, lon
        return None

    return draw


def _snap(point: Point, locate: LocateFn) -> bool:
    distance = locate(point)
    return distance is not None and distance <= MAX_ROAD_DISTANCE_M


def sample_municipality(code: str, geometry: dict, locate: LocateFn, count: int = MUNICIPALITY_POINTS,
                        seed: int = MUNICIPALITY_SEED, attempts: int = MUNICIPALITY_ATTEMPTS
                        ) -> Tuple[List[Point], List[Point], int]:
    """(origens, destinos, sorteios feitos): `count` de cada, uniformes no contorno e a até 150 m de uma via. Se o
    município não dá `count` de cada em `attempts` sorteios, devolve os que deu (a lista pode ficar curta ou vazia)."""
    draw = _polygon_sampler(geometry, random.Random(f"{seed}-{code}"))
    points: Dict[str, List[Point]] = {"O": [], "D": []}
    tries = 0
    for kind in "OD":
        while len(points[kind]) < count and tries < attempts:
            tries += 1
            point = draw()
            if point is None:
                tries = attempts
                break
            if _snap(point, locate):
                points[kind].append((round(point[0], 6), round(point[1], 6)))
    return points["O"], points["D"], tries


# ---------------------------------------------------------------------------------------------------------------------
# Uma unidade: o resultado, o município e o estado.

@dataclass
class Outcome:
    """O resultado do estudo de uma unidade (município ou estado)."""
    key: str                      # código do município ou sigla da UF
    weight: Optional[Combo]       # None se a unidade não deu amostra
    trips: int = 0                # viagens sorteadas
    counted: int = 0              # viagens que contaram (com rota em tudo)
    attempts: int = 0             # sorteios de ponto
    seconds: float = 0.0
    center: Optional[Combo] = None
    reason: str = ""              # por que não deu peso


def study_trips(key: str, pairs: Sequence[Pair], route: RouteFn, max_trails: float, attempts: int, started: float,
                measure_fn: Optional[Callable[..., List[Row]]] = None) -> Outcome:
    """Da amostra ao peso: a grade, o centro, o refino. `measure_fn` troca a medição (o estudo do estado a faz em
    vários processos); o padrão mede aqui, com `route`."""
    do_measure = measure_fn or (lambda pairs_, combos, wanted, stop: measure(route, pairs_, combos, wanted, stop))
    combos = grid(max_trails)
    grid_rows = do_measure(pairs, combos, None, True)
    return finish_trips(key, pairs, grid_rows, combos, route, max_trails, attempts, started, do_measure)


def finish_trips(key: str, pairs: Sequence[Pair], grid_rows: Sequence[Row], combos: Sequence[Combo], route: RouteFn,
                 max_trails: float, attempts: int, started: float,
                 do_measure: Callable[..., List[Row]]) -> Outcome:
    """A segunda metade de `study_trips`, com a grade já medida (o estudo do estado mede a grade aos poucos)."""
    center = pick_center(grid_rows, combos)
    if center is None:
        return Outcome(key, None, len(pairs), 0, attempts, time.time() - started, reason="nenhuma viagem com rota")
    around = neighbours(center, max_trails)
    wanted = complete(grid_rows)
    around_rows = do_measure(pairs, around, wanted, False) if around else [[] for _ in pairs]
    best, counted = pick_best(grid_rows, combos, center, around_rows, around)
    return Outcome(key, best, len(pairs), counted, attempts, time.time() - started, center=center)


def study_municipality(code: str, geometry: dict, locate: LocateFn, route: RouteFn,
                       max_trails: float = config.MOTORCYCLE_WEIGHTS_MAX_USE_TRAILS) -> Outcome:
    """O peso de um município: 32 x 32 viagens, a grade e o refino. Sem amostra (nenhum ponto a até 150 m de uma
    via), o peso vem None e o motivo, em `reason`."""
    started = time.time()
    origins, destinations, tries = sample_municipality(code, geometry, locate)
    if not origins or not destinations:
        return Outcome(code, None, 0, 0, tries, time.time() - started, reason="sem pontos a até 150 m de uma via")
    pairs = [(o, d) for o in origins for d in destinations]
    return study_trips(code, pairs, route, max_trails, tries, started)


def state_sampler(polygon, rng: random.Random, locate: LocateFn, attempts: int = STATE_ATTEMPTS):
    """Um sorteador de pontos uniformes no polígono shapely de uma UF, a até 150 m de uma via. `draw(n)` devolve n
    pontos; levanta RuntimeError se passar de `attempts` sorteios no total."""
    from shapely.geometry import Point as ShapelyPoint
    from shapely.prepared import prep

    prepared = prep(polygon)
    x0, y0, x1, y1 = polygon.bounds
    tries = [0]

    def draw(n: int) -> List[Point]:
        out: List[Point] = []
        while len(out) < n:
            if tries[0] >= attempts:
                raise RuntimeError(f"{attempts} sorteios e só {len(out)} de {n} pontos a até 150 m de uma via")
            tries[0] += 1
            lon, lat = rng.uniform(x0, x1), rng.uniform(y0, y1)
            if prepared.contains(ShapelyPoint(lon, lat)) and _snap((lat, lon), locate):
                out.append((round(lat, 6), round(lon, 6)))
        return out

    draw.tries = tries  # type: ignore[attr-defined]
    return draw


def study_state(state: str, polygon, locate: LocateFn, route: RouteFn, measure_fn: Callable[..., List[Row]],
                max_trails: float = config.MOTORCYCLE_WEIGHTS_MAX_USE_TRAILS, goal: int = STATE_TRIP_GOAL,
                destinations: int = STATE_DESTINATIONS, origins: int = STATE_ORIGINS,
                step: int = STATE_ORIGIN_STEP, max_origins: int = STATE_MAX_ORIGINS,
                seed: int = STATE_SEED, deadline: Optional[float] = None) -> Outcome:
    """O peso de uma UF: `destinations` destinos e `origins` origens uniformes no polígono; se as viagens com rota em
    toda a grade não chegam a `goal`, mais origens, `step` de cada vez, até `max_origins`. `measure_fn(pairs, combos,
    wanted, stop_on_first_failure)` mede em paralelo. Passando de `deadline` (relógio de `time.time()`), levanta
    TimeoutError: um estado pela metade não vale."""
    started = time.time()
    draw = state_sampler(polygon, random.Random(f"{seed}-{state}"), locate)
    dests = draw(destinations)
    origs = draw(origins)
    combos = grid(max_trails)
    grid_rows: List[Row] = []
    measured = 0
    while True:
        new = [(o, d) for o in origs[measured:] for d in dests]
        grid_rows += measure_fn(new, combos, None, True)
        measured = len(origs)
        counted = sum(complete(grid_rows))
        if counted >= goal or len(origs) >= max_origins:
            break
        if deadline is not None and time.time() > deadline:
            raise TimeoutError(f"{state}: sem tempo para medir mais origens")
        origs += draw(step)
    pairs = [(o, d) for o in origs for d in dests]
    return finish_trips(state, pairs, grid_rows, combos, route, max_trails, draw.tries[0], started, measure_fn)


# ---------------------------------------------------------------------------------------------------------------------
# A parte paralela: um processo por núcleo, cada um com o seu Actor.

_actor_state: Dict[str, object] = {}


def _init_worker(tiles_dir: str) -> None:
    actor = open_actor(Path(tiles_dir))
    _actor_state["actor"] = actor
    _actor_state["route"] = Router(actor)
    _actor_state["locate"] = lambda point: road_distance(actor, point)


def _municipality_task(code: str, geometry: dict) -> Outcome:
    return study_municipality(code, geometry, _actor_state["locate"], _actor_state["route"])  # type: ignore[arg-type]


def _measure_task(pairs, combos, wanted, stop) -> List[Row]:
    return measure(_actor_state["route"], pairs, combos, wanted, stop)  # type: ignore[arg-type]


def _chunks(items: Sequence, size: int) -> Iterator[Tuple[int, Sequence]]:
    for start in range(0, len(items), size):
        yield start, items[start:start + size]


class Pool:
    """Os processos do estudo. Cada um abre o `Actor` uma vez (a inicialização lê o cache de tiles) e fica com ele."""

    def __init__(self, tiles_dir: Path, workers: int) -> None:
        self.workers = max(1, workers)
        self.executor = ProcessPoolExecutor(max_workers=self.workers, initializer=_init_worker,
                                            initargs=(str(tiles_dir),))

    def __enter__(self) -> "Pool":
        return self

    def __exit__(self, *exc) -> None:
        self.executor.shutdown(wait=True, cancel_futures=True)

    def municipalities(self, items: Iterable[Tuple[str, dict]], deadline: Optional[float] = None,
                       on_result: Optional[Callable[[Outcome], None]] = None) -> List[Outcome]:
        """Estuda os municípios `(código, contorno)` em ordem, no máximo `workers` ao mesmo tempo mais um de folga. Não
        começa mais nenhum depois de `deadline` (relógio de `time.time()`); os que já começaram terminam. Devolve os
        resultados na ordem em que chegaram. Um município que dá erro vira um resultado sem peso, com o erro em
        `reason`; se um processo morre (o Valhalla caiu), o lote pára ali e devolve o que já terminou."""
        pending = iter(items)
        running: Dict[Future, str] = {}
        results: List[Outcome] = []
        stopped = False
        while True:
            while not stopped and len(running) < self.workers + 1:
                if deadline is not None and time.time() >= deadline:
                    stopped = True
                    break
                nxt = next(pending, None)
                if nxt is None:
                    stopped = True
                    break
                running[self.executor.submit(_municipality_task, *nxt)] = nxt[0]
            if not running:
                return results
            done, _ = wait(set(running), return_when=FIRST_COMPLETED)
            for future in done:
                code = running.pop(future)
                try:
                    outcome = future.result()
                except BrokenProcessPool:
                    print(f"[pesos] AVISO: um processo do estudo morreu em {code}; o lote pára aqui", flush=True)
                    return results
                except Exception as e:  # noqa: BLE001 - um município com erro não derruba o lote
                    outcome = Outcome(code, None, reason=f"erro: {e.__class__.__name__}: {e}")
                results.append(outcome)
                if on_result:
                    on_result(outcome)

    def measurer(self, chunk: int = 25, deadline: Optional[float] = None) -> Callable[..., List[Row]]:
        """A `measure_fn` do estudo do estado: reparte as viagens em blocos de `chunk` entre os processos e junta as
        linhas na ordem. Passando de `deadline` (relógio de `time.time()`), cancela o que falta e levanta
        TimeoutError."""
        def run(pairs, combos, wanted, stop):
            blocks: List[Future] = [
                self.executor.submit(_measure_task, list(block), list(combos),
                                     None if wanted is None else list(wanted[start:start + len(block)]), stop)
                for start, block in _chunks(pairs, chunk)]
            rows: List[Row] = []
            for future in blocks:
                while True:
                    try:
                        rows += future.result(timeout=5)
                        break
                    except FutureTimeout:
                        if deadline is not None and time.time() > deadline:
                            for other in blocks:
                                other.cancel()
                            raise TimeoutError("o orçamento de tempo do estudo acabou") from None
            return rows
        return run
