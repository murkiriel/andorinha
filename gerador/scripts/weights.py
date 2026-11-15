"""O estudo dos pesos da moto em lotes e a aplicação do resultado numa geração.

    # o que o workflow faz: extrai os pacotes de data/dist/, estuda o lote da semana, grava as tabelas novas
    python scripts/weights.py study --dist data/dist --out data/pesos

    # uma execução à mão, sobre uma pasta de tiles já extraída (a base e as UFs juntas), com municípios escolhidos
    python scripts/weights.py study --tiles /caminho/tiles --build-id 2026-11-09-c17dff60 --out /caminho/saida \\
        --municipalities 5201108,5300108 --states 0 --workers 4

    # põe as tabelas novas na geração de data/dist/ (arquivos de apoio por UF e catálogo refeitos)
    python scripts/weights.py apply --dist data/dist --tables data/pesos

`study` sai com 0 quando refez ao menos uma unidade (e gravou as tabelas novas em --out) e com 1 quando não refez
nenhuma. Nenhum dos dois comandos publica nada; a publicação é o scripts/publish.py. A lógica fica em
andorinha/weights_run.py (o estudo) e andorinha/weights_tables.py (as tabelas).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from andorinha import config, weights_run, weights_tables  # noqa: E402


def _csv(value: str):
    return [item.strip() for item in value.split(",") if item.strip()]


def study(args: argparse.Namespace) -> int:
    if args.dist:
        cat = json.loads((args.dist / "catalogo.json").read_text(encoding="utf-8"))
        build_id = cat["build_id"]
        tables = args.tables or args.dist
        work = args.work or config.DATA / "pesos-trabalho"
        print(f"[pesos] extraindo os pacotes de {args.dist} em {work / 'tiles'}...", flush=True)
        tiles = weights_run.prepare_tiles(args.dist, work)
    else:
        if not (args.tiles and args.build_id):
            sys.exit("sem --dist, o study pede --tiles e --build-id")
        build_id, tables, tiles = args.build_id, args.tables, args.tiles
    plan = weights_run.Plan(tiles_dir=tiles, build_id=build_id, out=args.out, tables_dir=tables)
    for name in ("date", "workers", "batch", "states", "minutes", "state_goal", "state_destinations", "state_origins"):
        value = getattr(args, name)
        if value is not None:
            setattr(plan, name, value)
    if args.municipalities is not None:
        plan.only_codes = args.municipalities
    if args.state is not None:
        plan.only_states = args.state
    result = weights_run.run(plan)
    return 0 if result.changed else 1


def study_note(tables: Path) -> Optional[str]:
    """Uma frase com o resumo do estudo (o `estudo.json` ao lado das tabelas), para as `notes` do geracao.json."""
    try:
        summary = json.loads((tables / weights_run.SUMMARY_FILE).read_text(encoding="utf-8"))
        municipal, states = summary["municipalities"], summary["states"]
        return (f"estudo dos pesos: {municipal['updated']} municípios e {states['updated']} estado(s) refeitos com os "
                f"tiles desta geração, em {summary['minutes']:.0f} min")
    except (OSError, ValueError, KeyError):
        return None


def apply(args: argparse.Namespace) -> int:
    try:
        notes = weights_tables.apply_to_dist(args.dist, args.tables, args.note or study_note(args.tables))
    except weights_tables.ApplyError as e:
        print(f"[pesos] tabelas novas NÃO aplicadas: {e}")
        return 1
    print(f"[pesos] tabelas novas aplicadas em {args.dist}")
    for note in notes:
        print(f"[pesos] AVISO: {note}")
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # acentos certos também com a saída redirecionada para arquivo
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)

    s = sub.add_parser("study", help="estuda o lote da semana")
    s.add_argument("--dist", type=Path, help="a geração (data/dist/): os pacotes, o catálogo e as tabelas de partida")
    s.add_argument("--work", type=Path, help="com --dist: onde extrair os tiles (padrão: data/pesos-trabalho)")
    s.add_argument("--tiles", type=Path, help="sem --dist: a pasta de tiles já extraída")
    s.add_argument("--build-id", help="sem --dist: o build_id dos tiles")
    s.add_argument("--tables", type=Path, help="a pasta das tabelas de partida (padrão: --dist; sem os dois, a semente)")
    s.add_argument("--out", type=Path, required=True, help="onde gravar as tabelas novas e o estudo.json")
    s.add_argument("--date", help="o dia gravado nas linhas (AAAA-MM-DD; padrão: hoje, UTC)")
    s.add_argument("--workers", type=int, help=f"processos (padrão: {config.WEIGHTS_WORKERS})")
    s.add_argument("--batch", type=int, help=f"municípios do lote (padrão: {config.WEIGHTS_BATCH})")
    s.add_argument("--states", type=int, help=f"estados do lote (padrão: {config.WEIGHTS_STATES}; 0 desliga)")
    s.add_argument("--minutes", type=float, help=f"orçamento de tempo (padrão: {config.WEIGHTS_MINUTES:g})")
    s.add_argument("--state-goal", type=int, help="viagens com rota em toda a grade que o estudo do estado quer (padrão: 10.000)")
    s.add_argument("--state-destinations", type=int, help="destinos sorteados no estado (padrão: 100)")
    s.add_argument("--state-origins", type=int, help="origens sorteadas no estado no começo (padrão: 100)")
    s.add_argument("--municipalities", type=_csv, help="códigos IBGE escolhidos à mão, em vez do lote da rotação")
    s.add_argument("--state", type=_csv, help="siglas de UF escolhidas à mão, em vez do estado da rotação")
    s.set_defaults(run=study)

    a = sub.add_parser("apply", help="põe as tabelas novas na geração")
    a.add_argument("--dist", type=Path, default=config.DIST)
    a.add_argument("--tables", type=Path, required=True, help="a pasta com as tabelas novas (a --out do study)")
    a.add_argument("--note", help="uma frase para as notes do geracao.json (padrão: o resumo do estudo.json das tabelas)")
    a.set_defaults(run=apply)

    args = ap.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
