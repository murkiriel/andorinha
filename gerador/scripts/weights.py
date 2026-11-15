"""O estudo dos pesos da moto em lotes, depois da publicação, e a subida do resultado na release.

    # o que o workflow faz, no job weights: baixa a release recém-publicada, estuda o lote da semana, grava as tabelas novas
    python scripts/weights.py study --release 2026-11-16-0a1b2c3d --work /tmp/pesos --out pesos
    python scripts/weights.py upload --tag 2026-11-16-0a1b2c3d --out pesos

    # uma execução à mão, sobre uma pasta de tiles já extraída (a base e as UFs juntas), com municípios escolhidos
    python scripts/weights.py study --tiles /caminho/tiles --build-id 2026-11-09-c17dff60 --out /caminho/saida \\
        --municipalities 5201108,5300108 --states 0 --workers 4

`study` sai com 0 quando refez ao menos uma unidade (e gravou as tabelas `proximo` e o `pesos_proximo.json` em --out) e
com 1 quando não refez nenhuma. `upload` sobe só esses três arquivos como assets extras da release, que já tem de estar
publicada, sem mexer em mais nada dela. A publicação da geração é o scripts/publish.py, que não depende de nada disto.
A lógica fica em andorinha/weights_run.py (o estudo) e andorinha/weights_tables.py (as tabelas).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from andorinha import config, publishing, weights_run, weights_tables  # noqa: E402


def _csv(value: str):
    return [item.strip() for item in value.split(",") if item.strip()]


def study(args: argparse.Namespace) -> int:
    if args.release:
        work = args.work or config.DATA / "pesos-trabalho"
        print(f"[pesos] baixando a release {args.release} em {work}...", flush=True)
        tiles, tables, build_id = weights_run.prepare_from_release(args.release, work)
    else:
        if not (args.tiles and args.build_id):
            sys.exit("sem --release, o study pede --tiles e --build-id")
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


def upload(args: argparse.Namespace) -> int:
    try:
        names = weights_tables.upload_next(publishing.gh_real, args.tag, args.out)
    except weights_tables.UploadError as e:
        print(f"[pesos] resultado do estudo NÃO subiu: {e}")
        return 1
    print(f"[pesos] subiram na release {args.tag}: {', '.join(names)}")
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
    s.add_argument("--release", help="o build_id de uma release já publicada: baixa dela os pacotes e as tabelas")
    s.add_argument("--work", type=Path, help="com --release: onde baixar e extrair (padrão: data/pesos-trabalho)")
    s.add_argument("--tiles", type=Path, help="sem --release: a pasta de tiles já extraída")
    s.add_argument("--build-id", help="sem --release: o build_id dos tiles")
    s.add_argument("--tables", type=Path, help="sem --release: a pasta das tabelas de partida (sem ela, a semente)")
    s.add_argument("--out", type=Path, required=True, help="onde gravar as tabelas próximo, o json e o estudo.json")
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

    u = sub.add_parser("upload", help="sobe o resultado do estudo na release")
    u.add_argument("--tag", required=True, help="o build_id da release (já publicada)")
    u.add_argument("--out", type=Path, required=True, help="a --out do study")
    u.set_defaults(run=upload)

    args = ap.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
