"""Põe a issue "Geração falhando" de acordo com o resultado de uma execução agendada do workflow gerar.

    python scripts/alert.py --interval failure --generate skipped --run-url https://github.com/.../runs/123

`--interval` e `--generate` são os resultados dos jobs (success, failure, cancelled, skipped). A lógica fica em
andorinha/alert.py; o gh precisa de GH_TOKEN com permissão de escrever issues.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from andorinha import alert  # noqa: E402


def main(argv: Optional[Sequence[str]] = None, gh: alert.Gh = alert.gh_real) -> None:
    for stream in (sys.stdout, sys.stderr):  # acentos certos também com a saída redirecionada para arquivo
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--interval", required=True, help="resultado do job interval")
    ap.add_argument("--generate", required=True, help="resultado do job generate")
    ap.add_argument("--run-url", required=True, help="link da execução")
    args = ap.parse_args(argv)

    result = alert.outcome(args.interval, args.generate)
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    done = alert.sync(gh, result, args.run_url, when)
    print(f"interval {args.interval}, generate {args.generate} -> {result}; issue \"{alert.TITLE}\": {done}")


if __name__ == "__main__":
    main()
