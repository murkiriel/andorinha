"""Fusos horários nos tiles (2026-10-05): o banco de fusos entra no build (`mjolnir.timezone`), só com as 16 zonas do
Brasil. Cada nó do grafo leva o fuso dele, e uma rota com `date_time` responde com a hora de chegada e o fuso; quem
usa os pacotes não precisa de arquivo nenhum a mais."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GENERATOR_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GENERATOR_DIR))
from andorinha import catalog, config, timezones, validation  # noqa: E402

BRASILIA = "POLYGON((-48.5 -16.2, -47.2 -16.2, -47.2 -15.3, -48.5 -15.3, -48.5 -16.2))"


def feature(tzid, ring):
    return {"type": "Feature", "properties": {"tzid": tzid}, "geometry": {"type": "Polygon", "coordinates": [ring]}}


SQUARE = [[-48.0, -16.0], [-47.0, -16.0], [-47.0, -15.0], [-48.0, -15.0], [-48.0, -16.0]]


class ZonesTest(unittest.TestCase):
    def test_only_the_brazilian_zones(self):
        gj = {"type": "FeatureCollection", "features": [feature(z, SQUARE) for z in
              ("America/Sao_Paulo", "America/Asuncion", "America/Manaus", "Etc/GMT+3", "America/Noronha")]}
        self.assertEqual(["America/Manaus", "America/Noronha", "America/Sao_Paulo"],
                         sorted(tzid for tzid, _ in timezones.brazil_features(gj)))

    def test_the_sixteen_zones_of_brazil(self):
        self.assertEqual(16, len(timezones.BRAZIL_TZIDS))
        for z in ("America/Sao_Paulo", "America/Manaus", "America/Rio_Branco", "America/Noronha",
                  "America/Cuiaba", "America/Porto_Velho", "America/Eirunepe"):
            self.assertIn(z, timezones.BRAZIL_TZIDS)

    def test_the_catalog_says_where_the_zones_came_from(self):
        cat_zones = catalog.timezones_entry()
        self.assertEqual(config.TIMEZONE_RELEASE, cat_zones["release"])
        self.assertEqual(sorted(timezones.BRAZIL_TZIDS), cat_zones["tzids"])

    def test_validation_wants_the_zone_in_the_answer(self):
        ok = {"trip": {"locations": [{"date_time": "2026-10-10T08:00", "time_zone_name": "America/Sao_Paulo"}]}}
        self.assertIsNone(validation.timezone_problem(ok, "America/Sao_Paulo"))
        without = {"trip": {"locations": [{"date_time": "2026-10-10T08:00"}]}}
        self.assertIn("sem fuso", validation.timezone_problem(without, "America/Sao_Paulo"))
        other = {"trip": {"locations": [{"time_zone_name": "America/Manaus"}]}}
        self.assertIn("America/Manaus", validation.timezone_problem(other, "America/Sao_Paulo"))

    def test_the_build_installs_spatialite(self):
        text = (GENERATOR_DIR.parent / ".github" / "workflows" / "gerar.yml").read_text(encoding="utf-8")
        self.assertIn("libsqlite3-mod-spatialite", text)


class ArchiveTest(unittest.TestCase):
    """O zip de fusos é conferido pelo sha256 fixado no config, como a malha do IBGE: o baixado agora e o que já estava
    em data/raw/. Nenhum teste aqui usa a rede: o download é trocado por uma função que grava bytes conhecidos."""

    GOOD = b"zip de fusos de mentira"

    def setUp(self):
        from unittest import mock
        import hashlib
        self._tmp = tempfile.TemporaryDirectory()
        self.raw = Path(self._tmp.name) / "raw"
        self.downloads = []
        self.served = self.GOOD

        def download(url, dest, *args, **kwargs):
            self.downloads.append(url)
            dest.write_bytes(self.served)

        self._patches = [mock.patch.object(config, "RAW", self.raw),
                         mock.patch.object(config, "TIMEZONE_SHA256", hashlib.sha256(self.GOOD).hexdigest()),
                         mock.patch.object(timezones.net, "download", download)]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def test_downloads_once_and_accepts_the_right_file(self):
        path = timezones.archive()
        self.assertEqual(self.GOOD, path.read_bytes())
        self.assertEqual(1, len(self.downloads))
        self.assertIn(config.TIMEZONE_RELEASE, self.downloads[0])
        timezones.archive()  # já baixado: confere de novo, sem baixar
        self.assertEqual(1, len(self.downloads))

    def test_refuses_and_deletes_a_downloaded_file_with_another_hash(self):
        self.served = b"outro conteudo"
        with self.assertRaises(RuntimeError) as ctx:
            timezones.archive()
        self.assertIn("sha256", str(ctx.exception))
        self.assertEqual([], list(self.raw.iterdir()))  # nada fica para a próxima geração usar

    def test_refuses_a_file_already_on_disk_with_another_hash(self):
        """Um zip deixado em data/raw/ (outra release com o mesmo nome, arquivo mexido) não passa sem conferir."""
        self.raw.mkdir(parents=True)
        (self.raw / f"timezones-with-oceans-{config.TIMEZONE_RELEASE}.geojson.zip").write_bytes(b"velho")
        with self.assertRaises(RuntimeError):
            timezones.archive()
        self.assertEqual([], self.downloads)

    def test_build_stops_before_reading_a_wrong_file(self):
        self.served = b"outro conteudo"
        with self.assertRaises(RuntimeError):
            timezones.build(Path(self._tmp.name) / "timezones.sqlite")
        self.assertFalse((Path(self._tmp.name) / "timezones.sqlite").exists())


class PinnedArchiveTest(unittest.TestCase):
    def test_the_pinned_release_and_hash(self):
        """Release 2026d: o sha256 do arquivo baixado em 2026-11-11, igual ao `digest` do GitHub para o arquivo da
        release. Trocar a release sem trocar o sha256 (ou o contrário) faz a geração parar no zip de fusos."""
        self.assertEqual("2026d", config.TIMEZONE_RELEASE)
        self.assertEqual("e391c92c7b90339c3afe8558082b4532b8d9afc9daad68da31f4569a1ca61034", config.TIMEZONE_SHA256)


def _ready():
    """Valhalla, pyosmium e o mod_spatialite presentes? (no CI de testes, não: o teste de ponta a ponta é pulado)"""
    try:
        import osmium  # noqa: F401
        from andorinha import engine
        engine.import_valhalla()
        timezones.connect(":memory:").close()
        return None
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"


class EndToEndTest(unittest.TestCase):
    """Uma malha pequena em Brasília, o banco com um polígono America/Sao_Paulo, o build de verdade e a rota."""

    @classmethod
    def setUpClass(cls):
        why = _ready()
        if why:
            raise unittest.SkipTest(f"sem Valhalla, pyosmium ou mod_spatialite ({why})")

    def _network(self, pbf):
        import osmium
        w = osmium.SimpleWriter(str(pbf))
        ids, nid = {}, 1
        for i in range(4):
            for j in range(4):
                w.add_node(osmium.osm.mutable.Node(id=nid, location=(-47.90 + j * 0.002, -15.80 + i * 0.002),
                                                   version=1))
                ids[(i, j)] = nid
                nid += 1
        # O Valhalla exige o PBF em ordem de id (fora de ordem, o valhalla_build_tiles cai ao ler as vias).
        for i in range(4):
            w.add_way(osmium.osm.mutable.Way(id=1 + i, nodes=[ids[(i, j)] for j in range(4)], version=1,
                                             tags={"highway": "residential"}))
        for i in range(4):
            w.add_way(osmium.osm.mutable.Way(id=101 + i, nodes=[ids[(j, i)] for j in range(4)], version=1,
                                             tags={"highway": "residential"}))
        w.close()

    def _route(self, tmp, with_zones):
        from andorinha import engine
        pbf, tiles = tmp / "mini.osm.pbf", tmp / ("tiles_tz" if with_zones else "tiles")
        tiles.mkdir()
        if not pbf.exists():
            self._network(pbf)
        db = None
        if with_zones:
            db = tmp / "timezones.sqlite"
            timezones.write_db([("America/Sao_Paulo", BRASILIA)], db)
        cfg = engine.config_build(tiles, tmp / "admins.sqlite", db)
        cfg["mjolnir"]["admin"] = ""
        (tmp / "cfg.json").write_text(json.dumps(cfg))
        bin_dir, env = engine.executables()
        r = subprocess.run([engine.exe(bin_dir, "valhalla_build_tiles"), "-c", str(tmp / "cfg.json"), str(pbf)],
                           env=env, capture_output=True, text=True)
        self.assertEqual(0, r.returncode, r.stdout[-500:] + r.stderr[-500:])
        return json.loads(engine.actor(tiles).route(json.dumps(validation.timezone_request())))

    def test_tiles_built_with_the_zones_answer_with_the_zone(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            with_zones = self._route(tmp, True)
            self.assertIsNone(validation.timezone_problem(with_zones, "America/Sao_Paulo"))
            self.assertEqual("-03:00", with_zones["trip"]["locations"][-1]["time_zone_offset"])
            without = self._route(tmp, False)
            self.assertIsNotNone(validation.timezone_problem(without, "America/Sao_Paulo"))


if __name__ == "__main__":
    unittest.main()
