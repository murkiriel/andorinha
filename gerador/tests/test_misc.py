"""Itens pequenos: espaço em disco, etapas do generate.py, limpeza da saída anterior, registro da geração."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

GENERATOR_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GENERATOR_DIR))
from andorinha import catalog, config, builder, packing, run_record  # noqa: E402
from andorinha.packing import Package  # noqa: E402

GB = 1_000_000_000


class DiskSpaceTest(unittest.TestCase):
    def test_low_space_stops_immediately(self):
        with self.assertRaises(RuntimeError) as ctx:
            builder.check_disk_space(Path("."), minimum=25 * GB, free=10 * GB)
        self.assertIn("25", str(ctx.exception))

    def test_enough_space_passes(self):
        builder.check_disk_space(Path("."), minimum=25 * GB, free=30 * GB)

    def test_default_minimum_comes_from_config(self):
        self.assertEqual(25 * GB, config.MIN_FREE_DISK_BYTES)


class StagesTest(unittest.TestCase):
    def setUp(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("generate", GENERATOR_DIR / "generate.py")
        self.script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.script)

    def test_from_catalog_does_not_validate(self):
        self.assertFalse(self.script.should_validate("catalog"))

    def test_other_stages_validate(self):
        for stage in ("osm", "tiles", "packages", "validate"):
            self.assertTrue(self.script.should_validate(stage), stage)

    def test_mesh_comes_before_the_long_stages(self):
        """Uma malha que não serve tem de parar a geração em segundos, não depois do extrato e dos tiles (~25 min no
        Actions), como nas gerações agendadas de 2026-11-03 a 2026-11-08, quando a API do IBGE não respondia."""
        calls = []

        class Stop(Exception):
            pass

        def osm_extract():
            calls.append("osm")
            raise Stop

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(self.script.sources, "ibge_mesh", side_effect=lambda: calls.append("mesh")), \
                mock.patch.object(self.script.sources, "osm_extract", side_effect=osm_extract):
            recorder = run_record.RunRecorder(Path(tmp), interval=3600)
            try:
                with self.assertRaises(Stop):
                    self.script._generate("osm", recorder)
            finally:
                recorder.save(Path(tmp) / "geracao.json", "teste")
        self.assertEqual(["mesh", "osm"], calls)


class CleanOutputTest(unittest.TestCase):
    def test_removes_previous_packages_and_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp)
            for name in ("andorinha-base.tar.gz", "andorinha-GO.tar.gz", "catalogo.json", "pacotes.json", "outro.txt"):
                (dist / name).write_text("x")
            packing.clean_output(dist)
            self.assertEqual(["outro.txt"], sorted(p.name for p in dist.iterdir()))


class SizeTest(unittest.TestCase):
    def test_size_attribute_and_bytes_key_in_catalog(self):
        p = Package(name="GO", file="andorinha-GO.tar.gz", size=1234, sha256="a" * 64, tiles=1, bytes_tiles=2,
                    bbox=[-20.0, -54.0, -12.0, -45.0], bbox_tiles=[-20.0, -54.0, -12.0, -45.0])
        self.assertFalse(hasattr(p, "bytes"))  # não esconde mais o tipo bytes do Python
        osm = {"timestamp": "2026-09-29T00:59:51Z", "md5": "c" * 32, "url": "u"}
        cat = catalog.build_catalog("2026-09-30-cccccccc", osm, {"base": p, "GO": p}, generator_commit="abc1234")
        self.assertEqual(1234, cat["states"]["GO"]["bytes"])
        self.assertNotIn("size", cat["states"]["GO"])


class ReadmeCompatibilityTest(unittest.TestCase):
    """A seção "Compatibilidade" do README da raiz é o que quem usa os pacotes lê antes de escolher o motor."""

    def setUp(self):
        import re

        readme = (config.REPO / "README.md").read_text(encoding="utf-8")
        self.section = readme.split("## Compatibilidade", 1)[1].split("\n## ", 1)[0]
        self.versions = set(re.findall(r"\b3\.\d+\.\d+\b", self.section))

    def test_says_the_generator_version_and_no_other(self):
        """Trocar VALHALLA_VERSION sem mexer no README deixaria o README mandando usar o motor errado."""
        self.assertEqual({config.VALHALLA_VERSION}, self.versions)

    def test_tells_users_to_check_the_version_of_a_generation(self):
        """O formato do tile muda entre versões: quem lê uma geração de outra versão não consegue rotear."""
        self.assertIn("`valhalla_version`", self.section)
        self.assertIn("recuse", self.section)

    def test_points_to_what_the_next_version_waits_for(self):
        self.assertIn("gerador/PENDENCIAS.md", self.section)

    def test_does_not_promise_every_valhalla_mobile_0_6(self):
        """O valhalla-mobile 0.6.4 (2026-10-06) já traz o Valhalla 3.9.1 e não lê os tiles 3.6.3: "0.6.x" mandaria
        quem usa a biblioteca mais nova baixar pacotes que ela não abre. Vale para o README inteiro, inglês incluso."""
        readme = (config.REPO / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("0.6.x", readme)
        self.assertIn("0.6.4", self.section)


class RunRecorderTest(unittest.TestCase):
    def test_saves_stages_disk_peak_and_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "a.bin").write_bytes(b"x" * 5000)
            recorder = run_record.RunRecorder(folder, interval=0.01)
            with recorder.stage("packages"):
                (folder / "b.bin").write_bytes(b"y" * 7000)
            recorder.save(folder / "geracao.json", "ok")
            data = json.loads((folder / "geracao.json").read_text(encoding="utf-8"))
        self.assertEqual("ok", data["result"])
        self.assertEqual(["packages"], [e["stage"] for e in data["stages"]])
        self.assertGreaterEqual(data["stages"][0]["minutes"], 0)
        self.assertGreaterEqual(data["peak_disk_gb"], 12000 / GB)

    def test_failed_stage_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            recorder = run_record.RunRecorder(Path(tmp), interval=0.01)
            with self.assertRaises(ValueError):
                with recorder.stage("tiles"):
                    raise ValueError("caiu")
            recorder.save(Path(tmp) / "geracao.json", "failed: caiu")
            data = json.loads((Path(tmp) / "geracao.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", data["stages"][0]["status"])
        self.assertEqual("failed: caiu", data["result"])


    def test_notes_are_saved_and_empty_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            recorder = run_record.RunRecorder(Path(tmp), interval=3600)
            recorder.save(Path(tmp) / "a.json", "ok")
            self.assertEqual([], json.loads((Path(tmp) / "a.json").read_text(encoding="utf-8"))["notes"])
            recorder = run_record.RunRecorder(Path(tmp), interval=3600)
            recorder.notes.append("pesos de outra geração")
            recorder.save(Path(tmp) / "b.json", "ok")
            self.assertEqual(["pesos de outra geração"],
                             json.loads((Path(tmp) / "b.json").read_text(encoding="utf-8"))["notes"])


class MunicipalitiesInTheGenerationTest(unittest.TestCase):
    """O generate.py com os arquivos de apoio: as cópias são conferidas antes de tudo, e pesos calculados com tiles de
    outra geração não param a geração; ficam ditos no catálogo, no log e no geracao.json."""

    def setUp(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("generate", GENERATOR_DIR / "generate.py")
        self.script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.script)

    def test_copies_are_checked_before_the_extract(self):
        calls = []

        class Stop(Exception):
            pass

        def refuse():
            calls.append("municipalities")
            raise Stop

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(self.script.sources, "ibge_mesh", side_effect=lambda: calls.append("mesh")), \
                mock.patch.object(self.script.municipalities, "check", side_effect=refuse), \
                mock.patch.object(self.script.sources, "osm_extract", side_effect=lambda: calls.append("osm")):
            recorder = run_record.RunRecorder(Path(tmp), interval=3600)
            try:
                with self.assertRaises(Stop):
                    self.script._generate("osm", recorder)
            finally:
                recorder.save(Path(tmp) / "geracao.json", "teste")
        self.assertEqual(["mesh", "municipalities"], calls)

    def test_weights_of_an_earlier_generation_do_not_stop_the_generation(self):
        import contextlib
        import io
        from tests.test_catalog import MD5, OSM, WEIGHTS_BUILD_ID, municipality_files, package
        from andorinha import municipalities
        packages = {"base": package("base", [-33.75, -73.99, 5.27, -34.79], [-36.0, -76.0, 8.0, -32.0]),
                    "DF": package("DF", [-16.05, -48.29, -15.5, -47.31], [-17.0, -49.0, -15.0, -47.0]),
                    "GO": package("GO", [-19.5, -53.25, -12.4, -45.91], [-20.0, -54.0, -12.0, -45.0])}
        with tempfile.TemporaryDirectory() as tmp:
            raw, dist = Path(tmp) / "raw", Path(tmp) / "dist"
            raw.mkdir()
            dist.mkdir()
            (raw / "brazil-latest.meta.json").write_text(json.dumps(OSM), encoding="utf-8")
            (dist / "pacotes.json").write_text(json.dumps({k: vars(v) for k, v in packages.items()}), encoding="utf-8")
            municipalities.save_index(municipality_files(), dist)
            log = io.StringIO()
            with mock.patch.object(config, "RAW", raw), mock.patch.object(config, "DIST", dist), \
                    contextlib.redirect_stdout(log):
                recorder = run_record.RunRecorder(Path(tmp), interval=3600)
                summary = self.script._generate("catalog", recorder)
                recorder.save(dist / "geracao.json", "ok")
            cat = json.loads((dist / "catalogo.json").read_text(encoding="utf-8"))
            record = json.loads((dist / "geracao.json").read_text(encoding="utf-8"))
        self.assertIn(f"geração {cat['build_id']}", summary)
        self.assertTrue(cat["build_id"].endswith(MD5[:8]))
        self.assertNotEqual(WEIGHTS_BUILD_ID, cat["build_id"])
        # no catálogo
        self.assertIs(False, cat["municipalities"]["weights"]["same_generation"])
        self.assertEqual([WEIGHTS_BUILD_ID],
                         [g["tiles_build_id"] for g in cat["municipalities"]["states"]["GO"]["generations"]])
        # no registro da execução e no log
        self.assertEqual("ok", record["result"])
        self.assertEqual(1, len(record["notes"]))
        self.assertIn(WEIGHTS_BUILD_ID, record["notes"][0])
        self.assertIn("AVISO: pesos de 4 municípios (2 UFs)", log.getvalue())


if __name__ == "__main__":
    unittest.main()
