"""O estudo dos pesos (andorinha/weights_study.py): a grade, o refino, a escolha com os desempates, o sorteio da amostra
(igual ao dos estudos de 2026-11-14), o estudo de um município e de um estado com um roteador falso, e a parte paralela
sem processos de verdade. Nada aqui precisa do Valhalla; o último teste, sim, e é pulado sem ele."""
import json
import random
import sys
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path

GENERATOR_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GENERATOR_DIR))
from andorinha import weights_study as ws  # noqa: E402

SQUARE_WITH_HOLE = {"type": "Polygon", "coordinates": [
    [[-48.0, -16.0], [-47.0, -16.0], [-47.0, -15.0], [-48.0, -15.0], [-48.0, -16.0]],
    [[-47.6, -15.6], [-47.4, -15.6], [-47.4, -15.4], [-47.6, -15.4], [-47.6, -15.6]]]}
TWO_PARTS = {"type": "MultiPolygon", "coordinates": [
    [[[-50.0, -20.0], [-49.0, -20.0], [-49.0, -19.0], [-50.0, -19.0], [-50.0, -20.0]]],
    [[[-45.0, -10.0], [-44.0, -10.0], [-44.5, -9.0], [-45.0, -10.0]]]]}


def fake_distance(point):
    """Um ponto só vale se o sexto decimal da latitude (módulo 7) for 0 a 3: 4 em cada 7."""
    return (int(abs(point[0]) * 1e6) % 7) * 40.0


def flat_world(point):
    return 0.0


def route_from(table):
    """Um roteador falso: `table[(uh, ut)]` é (décimos de segundo, metros); combinação fora da tabela não tem rota."""
    return lambda origin, destination, combo: table.get(combo)


class GridTest(unittest.TestCase):
    def test_grid_has_the_eight_combinations_up_to_the_trails_ceiling(self):
        self.assertEqual([(0.2, 0.2), (0.2, 0.4), (0.4, 0.2), (0.4, 0.4), (0.6, 0.2), (0.6, 0.4), (0.8, 0.2),
                          (0.8, 0.4)], ws.grid())

    def test_grid_follows_the_ceiling(self):
        self.assertEqual(16, len(ws.grid(0.8)))
        self.assertEqual([(u, 0.2) for u in (0.2, 0.4, 0.6, 0.8)], ws.grid(0.2))

    def test_neighbours_stay_within_zero_to_one_and_under_the_ceiling(self):
        self.assertEqual(8, len(ws.neighbours((0.6, 0.2))))  # 0,1 / 0,2 / 0,3 de use_trails
        self.assertEqual([(0.7, 0.3), (0.7, 0.4), (0.8, 0.3), (0.9, 0.3), (0.9, 0.4)], ws.neighbours((0.8, 0.4)))
        self.assertEqual(5, len(ws.neighbours((0.6, 0.4))))
        self.assertNotIn((0.6, 0.4), ws.neighbours((0.6, 0.4)))
        self.assertTrue(all(0 < u <= 1 and 0 < t <= 0.4 for u, t in ws.neighbours((0.2, 0.4))))
        self.assertEqual(0.1, min(u for u, _ in ws.neighbours((0.2, 0.2))))

    def test_neighbours_are_rounded_to_one_decimal(self):
        for center in ws.grid():
            for u, t in ws.neighbours(center):
                self.assertEqual(u, round(u, 1))
                self.assertEqual(t, round(t, 1))


class ChoiceTest(unittest.TestCase):
    COMBOS = ws.grid()

    def rows(self, per_combo, trips=3):
        """Uma linha por viagem; `per_combo[combo]` = (tempo, metros) igual para todas as viagens."""
        return [[per_combo[c] for c in self.COMBOS] for _ in range(trips)]

    def flat(self, time=1000, meters=5000):
        return {c: (time, meters) for c in self.COMBOS}

    def test_center_is_the_fastest(self):
        table = self.flat()
        table[(0.4, 0.2)] = (900, 5000)
        self.assertEqual((0.4, 0.2), ws.pick_center(self.rows(table), self.COMBOS))

    def test_time_tie_goes_to_the_shorter_distance(self):
        table = self.flat()
        table[(0.2, 0.2)] = (900, 4800)
        table[(0.8, 0.4)] = (900, 4700)
        self.assertEqual((0.8, 0.4), ws.pick_center(self.rows(table), self.COMBOS))

    def test_full_tie_goes_to_the_closest_to_the_reference(self):
        self.assertEqual((0.6, 0.4), ws.pick_center(self.rows(self.flat()), self.COMBOS))
        table = self.flat()
        table[(0.6, 0.4)] = (1100, 5000)  # a referência fica de fora: a seguinte mais perto é (0.4, 0.4) ou (0.8, 0.4)
        winner = ws.pick_center(self.rows(table), self.COMBOS)
        self.assertIn(winner, [(0.4, 0.4), (0.8, 0.4), (0.6, 0.2)])
        self.assertEqual(winner, ws.pick_center(self.rows(table), self.COMBOS))  # determinístico

    def test_trips_without_a_route_in_some_combination_do_not_count(self):
        table = self.flat()
        rows = self.rows(table, trips=2)
        rows[1][3] = None                        # a segunda viagem não tem rota em (0.4, 0.4)
        rows[1][0] = (1, 1)                      # e seria a "melhor" em (0.2, 0.2) se contasse
        self.assertEqual([True, False], ws.complete(rows))
        self.assertEqual((0.6, 0.4), ws.pick_center(rows, self.COMBOS))

    def test_no_complete_trip_means_no_center(self):
        rows = [[None] * len(self.COMBOS) for _ in range(3)]
        self.assertIsNone(ws.pick_center(rows, self.COMBOS))
        self.assertIsNone(ws.pick_center([], self.COMBOS))

    def test_means_use_only_the_counted_trips(self):
        table = self.flat()
        rows = self.rows(table, trips=2)
        rows[0][0] = (500, 100)                  # (0.2, 0.2) é rápida na viagem 0...
        rows[1][0] = (3000, 100)                 # ...e lenta na 1: média 1750, pior que 1000
        self.assertEqual((0.6, 0.4), ws.pick_center(rows, self.COMBOS))

    def test_refinement_picks_the_fastest_neighbour(self):
        grid_rows = [[(1000, 100)] * len(self.COMBOS)] * 3
        around = ws.neighbours((0.6, 0.4))
        around_rows = [[(1000, 100)] * len(around) for _ in range(3)]
        for row in around_rows:
            row[2] = (990, 100)
        best, counted = ws.pick_best(grid_rows, self.COMBOS, (0.6, 0.4), around_rows, around)
        self.assertEqual((around[2], 3), (best, counted))

    def test_refinement_tie_stays_at_the_center(self):
        grid_rows = [[(1000, 100)] * len(self.COMBOS)] * 2
        around = ws.neighbours((0.8, 0.2))
        around_rows = [[(1000, 100)] * len(around)] * 2
        self.assertEqual(((0.8, 0.2), 2), ws.pick_best(grid_rows, self.COMBOS, (0.8, 0.2), around_rows, around))

    def test_refinement_counts_only_trips_with_a_route_everywhere(self):
        grid_rows = [[(1000, 100)] * len(self.COMBOS)] * 3
        around = ws.neighbours((0.6, 0.4))
        around_rows = [[(1000, 100)] * len(around) for _ in range(3)]
        around_rows[0][0] = None                     # a viagem 0 perde um vizinho: sai da conta
        around_rows[1][1] = (10, 100)                # e a 1 faz do vizinho 1 o mais rápido, mas só ela
        around_rows[2][1] = (10, 100)
        best, counted = ws.pick_best(grid_rows, self.COMBOS, (0.6, 0.4), around_rows, around)
        self.assertEqual((around[1], 2), (best, counted))


class MeasureTest(unittest.TestCase):
    def test_measure_fills_one_row_per_trip_in_the_order_of_the_combinations(self):
        calls = []

        def route(o, d, c):
            calls.append((o, d, c))
            return (int(c[0] * 10), int(c[1] * 10))

        pairs = [((1.0, 2.0), (3.0, 4.0)), ((5.0, 6.0), (7.0, 8.0))]
        rows = ws.measure(route, pairs, [(0.2, 0.2), (0.4, 0.2)])
        self.assertEqual([[(2, 2), (4, 2)], [(2, 2), (4, 2)]], rows)
        self.assertEqual(4, len(calls))

    def test_trip_without_a_route_in_the_first_combination_is_not_tried_in_the_others(self):
        calls = []

        def route(o, d, c):
            calls.append(c)
            return None if c == (0.2, 0.2) else (1, 1)

        rows = ws.measure(route, [((0.0, 0.0), (1.0, 1.0))], [(0.2, 0.2), (0.4, 0.2)], stop_on_first_failure=True)
        self.assertEqual([[None, None]], rows)
        self.assertEqual([(0.2, 0.2)], calls)
        calls.clear()
        rows = ws.measure(route, [((0.0, 0.0), (1.0, 1.0))], [(0.2, 0.2), (0.4, 0.2)])  # no refino tenta todas
        self.assertEqual([[None, (1, 1)]], rows)

    def test_wanted_skips_trips(self):
        calls = []
        rows = ws.measure(lambda o, d, c: calls.append(o) or (1, 1), [((1.0, 1.0), (2.0, 2.0)), ((3.0, 3.0), (4.0, 4.0))],
                          [(0.2, 0.2)], wanted=[False, True])
        self.assertEqual([[None], [(1, 1)]], rows)
        self.assertEqual([(3.0, 3.0)], calls)


class RouterTest(unittest.TestCase):
    class Actor:
        def __init__(self, reply=None, error=None):
            self.reply, self.error, self.requests = reply, error, []

        def route(self, request):
            self.requests.append(json.loads(request))
            if self.error:
                raise self.error
            return json.dumps(self.reply)

    def test_request_is_the_motorcycle_one_with_the_combination(self):
        actor = self.Actor({"trip": {"summary": {"time": 61.27, "length": 1.2345}}})
        measure = ws.Router(actor)((-16.5, -49.25), (-16.3, -48.95), (0.6, 0.4))
        self.assertEqual((613, 1234), measure)  # décimos de segundo e metros, arredondados
        request = actor.requests[0]
        self.assertEqual([{"lat": -16.5, "lon": -49.25}, {"lat": -16.3, "lon": -48.95}], request["locations"])
        self.assertEqual("motorcycle", request["costing"])
        self.assertEqual({"motorcycle": {"use_highways": 0.6, "use_tolls": 1.0, "use_trails": 0.4}},
                         request["costing_options"])
        self.assertEqual({"units": "kilometers"}, request["directions_options"])

    def test_no_route_is_none(self):
        self.assertIsNone(ws.Router(self.Actor(error=RuntimeError("No path could be found")))((0, 0), (1, 1), (0.2, 0.2)))

    def test_road_distance_is_the_nearest_edge(self):
        class Actor:
            def locate(self, request):
                return json.dumps([{"edges": [{"correlated_lat": 0.001, "correlated_lon": 0.0},
                                              {"correlated_lat": 0.0001, "correlated_lon": 0.0}]}])

        distance = ws.road_distance(Actor(), (0.0, 0.0))
        self.assertAlmostEqual(11.1, distance, delta=0.2)

    def test_road_distance_without_an_edge_or_with_an_error_is_none(self):
        class Empty:
            def locate(self, request):
                return json.dumps([{"edges": []}])

        class Broken:
            def locate(self, request):
                raise RuntimeError("fora do grafo")

        self.assertIsNone(ws.road_distance(Empty(), (0.0, 0.0)))
        self.assertIsNone(ws.road_distance(Broken(), (0.0, 0.0)))

    def test_haversine(self):
        self.assertAlmostEqual(111195, ws.haversine(0, 0, 1, 0), delta=50)
        self.assertEqual(0.0, ws.haversine(-15.0, -47.0, -15.0, -47.0))


class SamplingTest(unittest.TestCase):
    def test_points_are_the_ones_of_the_2026_11_14_studies(self):
        """O sorteio é o dos estudos que geraram as tabelas atuais: mesma semente, mesma ordem dos sorteios, mesmo
        arredondamento. Os valores abaixo saíram do script original com o mesmo localizador falso."""
        origins, destinations, tries = ws.sample_municipality("5300108", SQUARE_WITH_HOLE, fake_distance, count=4)
        self.assertEqual([(-15.607965, -47.62281), (-15.765267, -47.643539), (-15.360942, -47.288235),
                          (-15.213044, -47.730902)], origins)
        self.assertEqual([(-15.029848, -47.604631), (-15.068211, -47.802346), (-15.113169, -47.382155),
                          (-15.486621, -47.203001)], destinations)
        self.assertEqual(15, tries)

    def test_multipolygon_uses_every_part(self):
        origins, destinations, tries = ws.sample_municipality("3100203", TWO_PARTS, fake_distance, count=4)
        self.assertEqual([(-19.132152, -49.441968), (-9.816657, -44.519235), (-19.890593, -49.742228),
                          (-9.635411, -44.358969)], origins)
        self.assertEqual(14, tries)

    def test_points_fall_inside_the_outline_and_outside_the_holes(self):
        origins, destinations, _ = ws.sample_municipality("X", SQUARE_WITH_HOLE, flat_world, count=200)
        for lat, lon in origins + destinations:
            self.assertTrue(-48.0 <= lon <= -47.0 and -16.0 <= lat <= -15.0)
            self.assertFalse(-47.6 < lon < -47.4 and -15.6 < lat < -15.4, (lat, lon))

    def test_same_code_same_points_other_code_other_points(self):
        a = ws.sample_municipality("1", SQUARE_WITH_HOLE, flat_world, count=5)
        self.assertEqual(a, ws.sample_municipality("1", SQUARE_WITH_HOLE, flat_world, count=5))
        self.assertNotEqual(a[0], ws.sample_municipality("2", SQUARE_WITH_HOLE, flat_world, count=5)[0])

    def test_point_too_far_from_a_road_is_discarded(self):
        origins, destinations, tries = ws.sample_municipality("3", SQUARE_WITH_HOLE, lambda p: 151.0, count=3,
                                                              attempts=40)
        self.assertEqual(([], [], 40), (origins, destinations, tries))
        origins, _, _ = ws.sample_municipality("3", SQUARE_WITH_HOLE, lambda p: 150.0, count=3)
        self.assertEqual(3, len(origins))
        self.assertEqual(([], [], 40), ws.sample_municipality("3", SQUARE_WITH_HOLE, lambda p: None, count=3,
                                                              attempts=40))

    def test_attempts_are_shared_between_origins_and_destinations(self):
        origins, destinations, tries = ws.sample_municipality("4", SQUARE_WITH_HOLE, fake_distance, count=50,
                                                              attempts=60)
        self.assertEqual(60, tries)
        self.assertLess(len(origins) + len(destinations), 100)


class StudyTest(unittest.TestCase):
    """Um mundo falso em que o tempo é conhecido: o estudo tem de achar o mínimo."""

    @staticmethod
    def world(fastest):
        """Tempo = 1000 + 100 vezes a distância de `fastest` na grade; metros fixos."""
        def route(origin, destination, combo):
            u, t = combo
            return int(round(10000 + 1000 * (abs(u - fastest[0]) + abs(t - fastest[1])))), 5000
        return route

    def run_municipality(self, fastest, locate=flat_world):
        return ws.study_municipality("5300108", SQUARE_WITH_HOLE, locate, self.world(fastest))

    def test_finds_a_weight_off_the_grid(self):
        outcome = self.run_municipality((0.5, 0.3))
        self.assertEqual((0.5, 0.3), outcome.weight)
        self.assertEqual((1024, 1024), (outcome.trips, outcome.counted))

    def test_center_comes_from_the_grid_and_the_refinement_goes_around_it(self):
        outcome = self.run_municipality((0.9, 0.4))
        self.assertEqual((0.8, 0.4), outcome.center)
        self.assertEqual((0.9, 0.4), outcome.weight)

    def test_weight_never_goes_over_the_trails_ceiling(self):
        outcome = self.run_municipality((0.6, 0.8))  # o mais rápido do mundo falso pede use_trails 0,8
        self.assertEqual(0.4, outcome.weight[1])

    def test_flat_world_stays_at_the_reference(self):
        outcome = ws.study_municipality("5300108", SQUARE_WITH_HOLE, flat_world, lambda o, d, c: (1000, 5000))
        self.assertEqual((0.6, 0.4), outcome.weight)

    def test_municipality_without_points_has_no_weight(self):
        outcome = self.run_municipality((0.6, 0.4), locate=lambda p: None)
        self.assertIsNone(outcome.weight)
        self.assertEqual("sem pontos a até 150 m de uma via", outcome.reason)

    def test_municipality_where_nothing_routes_has_no_weight(self):
        outcome = ws.study_municipality("5300108", SQUARE_WITH_HOLE, flat_world, lambda o, d, c: None)
        self.assertIsNone(outcome.weight)
        self.assertEqual(0, outcome.counted)

    def test_study_is_deterministic(self):
        a, b = self.run_municipality((0.5, 0.3)), self.run_municipality((0.5, 0.3))
        self.assertEqual((a.weight, a.center, a.counted, a.attempts), (b.weight, b.center, b.counted, b.attempts))

    def test_refinement_only_routes_the_trips_that_had_a_route_in_the_whole_grid(self):
        routed = []

        def route(origin, destination, combo):
            routed.append((origin, destination, combo))
            if origin == first_origin[0] and combo == (0.8, 0.4):
                return None
            return 1000, 5000

        origins, _, _ = ws.sample_municipality("5300108", SQUARE_WITH_HOLE, flat_world)
        first_origin = [origins[0]]
        outcome = ws.study_municipality("5300108", SQUARE_WITH_HOLE, flat_world, route)
        self.assertEqual(1024 - 32, outcome.counted)  # as 32 viagens da primeira origem saíram
        neighbours = set(ws.neighbours(outcome.center))
        in_refinement = [r for r in routed if r[2] in neighbours and r[2] not in ws.grid()]
        self.assertTrue(all(r[0] != first_origin[0] for r in in_refinement))


class StateStudyTest(unittest.TestCase):
    @staticmethod
    def polygon():
        from shapely.geometry import box
        return box(-48.0, -17.0, -47.0, -16.0)

    @staticmethod
    def measurer(route):
        def run(pairs, combos, wanted, stop):
            return ws.measure(route, pairs, combos, wanted, stop)
        return run

    def test_state_study_uses_the_goal_to_decide_how_many_origins(self):
        route = lambda o, d, c: (1000, 5000)  # noqa: E731
        outcome = ws.study_state("52", self.polygon(), flat_world, route, self.measurer(route), goal=60,
                                 destinations=5, origins=5, step=3, max_origins=100)
        # 5 x 5 = 25 viagens; 3 origens a mais de cada vez (15 viagens): 40, 55 e 70, que passa de 60 (14 origens)
        self.assertEqual(14 * 5, outcome.trips)
        self.assertEqual(14 * 5, outcome.counted)
        self.assertEqual((0.6, 0.4), outcome.weight)

    def test_state_study_stops_at_the_origin_ceiling(self):
        route = lambda o, d, c: None if o[0] < -16.5 else (1000, 5000)  # noqa: E731
        outcome = ws.study_state("52", self.polygon(), flat_world, route, self.measurer(route), goal=10 ** 6,
                                 destinations=4, origins=4, step=4, max_origins=12)
        self.assertEqual(12 * 4, outcome.trips)
        self.assertLess(outcome.counted, outcome.trips)

    def test_state_study_finds_the_fastest(self):
        route = StudyTest.world((0.7, 0.3))
        outcome = ws.study_state("52", self.polygon(), flat_world, route, self.measurer(route), goal=50,
                                 destinations=5, origins=10)
        self.assertEqual((0.7, 0.3), outcome.weight)

    def test_state_study_gives_up_past_the_deadline(self):
        route = lambda o, d, c: None  # noqa: E731  (nenhuma viagem conta: pediria mais origens)
        with self.assertRaises(TimeoutError):
            ws.study_state("52", self.polygon(), flat_world, route, self.measurer(route), goal=100, destinations=3,
                           origins=3, step=3, max_origins=100, deadline=0.0)

    def test_state_sampler_gives_up_when_no_point_is_near_a_road(self):
        draw = ws.state_sampler(self.polygon(), random.Random(1), lambda p: None, attempts=50)
        with self.assertRaises(RuntimeError):
            draw(3)

    def test_state_points_are_inside_the_polygon(self):
        draw = ws.state_sampler(self.polygon(), random.Random(7), flat_world)
        for lat, lon in draw(50):
            self.assertTrue(-48.0 <= lon <= -47.0 and -17.0 <= lat <= -16.0)


class FakeExecutor:
    """Um executor que roda na hora, no mesmo processo, e conta quantas tarefas recebeu."""

    def __init__(self):
        self.submitted = []

    def submit(self, fn, *args):
        self.submitted.append(args)
        future = Future()
        try:
            future.set_result(fn(*args))
        except BaseException as e:  # noqa: BLE001
            future.set_exception(e)
        return future

    def shutdown(self, **kwargs):
        pass


class PoolTest(unittest.TestCase):
    """O agendamento do Pool, sem processos: o executor falso roda cada tarefa na hora."""

    def setUp(self):
        self.pool = ws.Pool.__new__(ws.Pool)
        self.pool.workers = 2
        self.pool.executor = FakeExecutor()
        self.original = {k: ws._actor_state.get(k) for k in ("actor", "route", "locate")}
        ws._actor_state.update(route=lambda o, d, c: (1000, 5000), locate=flat_world)

    def tearDown(self):
        ws._actor_state.update(self.original)

    def items(self, n):
        return [(f"{5300100 + i}", SQUARE_WITH_HOLE) for i in range(n)]

    def test_runs_every_municipality(self):
        seen = []
        results = self.pool.municipalities(self.items(5), on_result=seen.append)
        self.assertEqual(5, len(results))
        self.assertEqual(sorted(r.key for r in results), sorted(s.key for s in seen))
        self.assertTrue(all(r.weight == (0.6, 0.4) for r in results))

    def test_stops_starting_new_municipalities_after_the_deadline(self):
        results = self.pool.municipalities(self.items(50), deadline=0.0)
        self.assertEqual([], results)
        self.assertEqual([], self.pool.executor.submitted)

    def test_a_municipality_that_raises_becomes_a_result_without_weight(self):
        def route(o, d, c):
            raise ValueError("resposta estranha")

        ws._actor_state["route"] = route
        results = self.pool.municipalities(self.items(2))
        self.assertEqual([None, None], [r.weight for r in results])
        self.assertIn("ValueError: resposta estranha", results[0].reason)

    def test_a_dead_process_stops_the_batch_and_keeps_what_finished(self):
        from concurrent.futures.process import BrokenProcessPool
        original = ws._municipality_task

        def task(code, geometry):
            if code.endswith("2"):
                raise BrokenProcessPool("morreu")
            return original(code, geometry)

        ws._municipality_task = task
        try:
            results = self.pool.municipalities(self.items(8))
        finally:
            ws._municipality_task = original
        self.assertLessEqual({r.key for r in results}, {"5300100", "5300101"})  # nada depois do que morreu
        self.assertEqual(3, len(self.pool.executor.submitted))  # e nenhum município novo começou

    def test_measurer_joins_the_blocks_in_order(self):
        pairs = [((float(i), 0.0), (0.0, 0.0)) for i in range(60)]
        ws._actor_state["route"] = lambda o, d, c: (int(o[0]), 1)
        rows = self.pool.measurer(chunk=25)(pairs, [(0.2, 0.2)], None, False)
        self.assertEqual([[(i, 1)] for i in range(60)], rows)
        self.assertEqual(3, len(self.pool.executor.submitted))

    def test_measurer_passes_the_wanted_slice_of_each_block(self):
        pairs = [((float(i), 0.0), (0.0, 0.0)) for i in range(30)]
        wanted = [i % 2 == 0 for i in range(30)]
        ws._actor_state["route"] = lambda o, d, c: (int(o[0]), 1)
        rows = self.pool.measurer(chunk=10)(pairs, [(0.2, 0.2)], wanted, False)
        self.assertEqual([[(i, 1)] if i % 2 == 0 else [None] for i in range(30)], rows)


class ActorTest(unittest.TestCase):
    def test_open_actor_refuses_a_folder_without_tiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "pasta de tiles"):
                ws.open_actor(Path(tmp))

    def test_study_config_is_the_readers_config_with_a_small_cache(self):
        try:
            import valhalla  # noqa: F401
        except ImportError:
            self.skipTest("sem o pyvalhalla")
        with tempfile.TemporaryDirectory() as tmp:
            cfg = ws.study_config(Path(tmp))
        self.assertFalse(cfg["loki"]["use_connectivity"])
        self.assertEqual(5_000_000, cfg["service_limits"]["motorcycle"]["max_distance"])
        self.assertEqual(ws.ROUTE_CACHE_BYTES, cfg["mjolnir"]["max_cache_size"])
        self.assertEqual("", cfg["mjolnir"]["traffic_extract"])


if __name__ == "__main__":
    unittest.main()
