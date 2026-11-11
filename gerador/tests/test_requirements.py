"""Dependências travadas: o requirements.txt e o requirements-testes.txt fixam todas as dependências, transitivas
inclusive, com o sha256 de cada arquivo, e são gerados pelo `uv` a partir dos .in. Lidos como texto: nada é instalado."""
import re
import sys
import unittest
from pathlib import Path

GENERATOR_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GENERATOR_DIR))
from andorinha import config  # noqa: E402

PAIRS = {"requirements.in": "requirements.txt", "requirements-testes.in": "requirements-testes.txt"}


def pins_in(path: Path) -> dict:
    """{pacote: versão} das linhas `pacote==versão` de um .in (comentários de fim de linha à parte)."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            m = re.fullmatch(r"([A-Za-z0-9_.-]+)==(\S+)", line)
            if not m:
                raise AssertionError(f"{path.name}: '{line}' sem versão fixa (pacote==versão)")
            out[m.group(1).lower()] = m.group(2)
    return out


def locked(path: Path) -> dict:
    """{pacote: (versão, [sha256...])} de um .txt gerado pelo `uv pip compile --generate-hashes`."""
    out = {}
    text = path.read_text(encoding="utf-8")
    for block in re.split(r"\n(?=\S)", text):
        if block.startswith("#") or not block.strip():
            continue
        m = re.match(r"([A-Za-z0-9_.-]+)==(\S+) \\\n", block)
        if not m:
            raise AssertionError(f"{path.name}: requisito sem versão fixa ou sem hash: {block.splitlines()[0]}")
        out[m.group(1).lower()] = (m.group(2), re.findall(r"--hash=sha256:([0-9a-f]{64})", block))
    return out


class LockedRequirementsTest(unittest.TestCase):
    def test_every_requirement_is_pinned_with_hashes(self):
        for txt in PAIRS.values():
            packages = locked(GENERATOR_DIR / txt)
            self.assertTrue(packages, txt)
            for name, (version, hashes) in packages.items():
                self.assertRegex(version, r"^\d+(\.\d+)*$", f"{txt}: {name}")
                self.assertTrue(hashes, f"{txt}: {name} sem hash")

    def test_lock_follows_the_in_file(self):
        """Cada dependência direta do .in está no .txt, na mesma versão: um .in editado sem gerar o .txt de novo falha."""
        for src, txt in PAIRS.items():
            direct, packages = pins_in(GENERATOR_DIR / src), locked(GENERATOR_DIR / txt)
            for name, version in direct.items():
                self.assertIn(name, packages, f"{txt} sem {name}: gere de novo a partir do {src}")
                self.assertEqual(version, packages[name][0], f"{txt}: {name}")

    def test_regeneration_command_on_top(self):
        for src, txt in PAIRS.items():
            command = f"uv pip compile {src} --generate-hashes --python-version 3.12 -o {txt}"
            head = (GENERATOR_DIR / txt).read_text(encoding="utf-8").splitlines()[:3]
            self.assertIn(command, "\n".join(head), txt)
            self.assertIn(command, (GENERATOR_DIR / src).read_text(encoding="utf-8"), src)

    def test_tests_use_the_generator_versions(self):
        """O CI testa com as mesmas versões que geram os pacotes (sem o pyvalhalla, que lá não é instalado)."""
        generator, tests = locked(GENERATOR_DIR / "requirements.txt"), locked(GENERATOR_DIR / "requirements-testes.txt")
        self.assertNotIn("pyvalhalla", tests)
        for name, (version, hashes) in tests.items():
            self.assertIn(name, generator, name)
            self.assertEqual(generator[name], (version, hashes), name)

    def test_pyvalhalla_is_the_pinned_valhalla(self):
        self.assertEqual(config.VALHALLA_VERSION, pins_in(GENERATOR_DIR / "requirements.in")["pyvalhalla"])
        self.assertEqual(config.VALHALLA_VERSION, locked(GENERATOR_DIR / "requirements.txt")["pyvalhalla"][0])


if __name__ == "__main__":
    unittest.main()
