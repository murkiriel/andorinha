"""As tabelas de pesos da moto, atualizadas pelo próprio gerador: de onde a geração parte, qual lote ela refaz, como
os resultados entram na tabela e como a tabela nova chega à release.

O ciclo de uma geração:
1. **Parte da tabela anterior.** A geração lê a tabela da release da geração publicada (`pesos_moto_municipios.csv` e
   `pesos_moto_estados.csv`, ao lado dos pacotes), conferida pelo sha256 e pela contagem que o `catalogo.json`
   publicado diz (`previous_tables`). Sem tabela na release (as gerações de antes deste ciclo), ou com uma que não
   confere, parte da semente, a cópia do repositório (conferida pelo sha256 do config), e diz isso no log e nas `notes`
   do `geracao.json`. A geração sai do mesmo jeito, só com pesos mais velhos.
2. **Estuda o lote.** Depois dos tiles, num job à parte do workflow, andorinha/weights_study.py refaz os pesos do lote
   da semana (`select_batch`: os municípios de peso mais velho; `select_states`: o estado de peso mais velho) sobre os
   tiles dessa geração. O que o estudo devolve entra na tabela (`merge`), cada linha com a geração dos tiles, o
   Valhalla e o dia dela.
3. **Aplica e publica.** `apply_to_dist` troca as tabelas da geração pelas novas, refaz os arquivos de apoio por UF e o
   catálogo, e a publicação sobe as tabelas na release junto com tudo. O Actions não escreve no repositório: a tabela
   fica na release. Se o estudo falhar, a publicação segue com a tabela anterior.

A rotação: a cada geração, os `BATCH` municípios cujas linhas são mais velhas (os sem linha vão sempre na frente); mais
um estado, o mais velho. Com as linhas todas do mesmo dia, o desempate é o código (município) ou a sigla (UF), então a
volta é sempre a mesma: 5.570 municípios em lotes de 600 levam 10 gerações (10 semanas), e os 27 estados, 27.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import catalog, config, municipalities, net
from .municipalities import (STATE_WEIGHTS_HEADER, STATE_WEIGHTS_FILE, WEIGHTS_FILE, WEIGHTS_HEADER, Inputs,
                             TableTexts, Weight)
from .weights_study import Outcome

# Quantos municípios e estados cada geração refaz. ANDORINHA_WEIGHTS_BATCH e ANDORINHA_WEIGHTS_STATES mudam (0 desliga).
BATCH = config.WEIGHTS_BATCH
STATES_PER_GENERATION = config.WEIGHTS_STATES

Fetch = Callable[[str], str]


class TableError(RuntimeError):
    """Uma tabela de pesos que não serve; a mensagem diz por quê."""


# ---------------------------------------------------------------------------------------------------------------------
# O formato: o mesmo texto que o gerador lê (andorinha/municipalities.py), escrito de volta byte a byte igual.

def _number(value: float) -> str:
    return f"{value:.1f}"


def serialize_weights(weights: Dict[str, Weight]) -> str:
    """A tabela de municípios em texto (`;`, LF, ordem de código)."""
    lines = [";".join(WEIGHTS_HEADER)]
    for code, w in sorted(weights.items()):
        lines.append(";".join([code, _number(w.use_highways), _number(w.use_trails), w.tiles_build_id,
                               w.valhalla_version, w.date]))
    return "\n".join(lines) + "\n"


def serialize_state_weights(weights: Dict[str, Weight]) -> str:
    """A tabela de estados em texto (`;`, LF, ordem de sigla)."""
    codes = {state: code for code, state in config.IBGE_STATE_CODES.items()}
    lines = [";".join(STATE_WEIGHTS_HEADER)]
    for state, w in sorted(weights.items()):
        lines.append(";".join([codes[state], state, _number(w.use_highways), _number(w.use_trails), w.tiles_build_id,
                               w.valhalla_version, w.date]))
    return "\n".join(lines) + "\n"


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_tables(weights_text: str, state_text: str, known_codes: Iterable[str], states: Iterable[str],
                 ) -> Tuple[Dict[str, Weight], Dict[str, Weight]]:
    """Confere as duas tabelas e devolve (pesos dos municípios, pesos dos estados). Levanta TableError se: o formato
    não é o de municipalities.py (cabeçalho, colunas, pesos de 0 a 1 com `use_trails` até o teto, geração, versão e
    data), há município repetido ou fora da malha, há menos municípios que a semente (a tabela só cresce: o estudo
    troca linhas, nunca tira), ou falta uma UF da malha."""
    known = set(known_codes)
    try:
        weights = municipalities.parse_weights(weights_text, known)
        state_weights = municipalities.parse_state_weights(state_text)
    except ValueError as e:
        raise TableError(str(e)) from e
    if len(weights) < config.MOTORCYCLE_WEIGHTS_ROWS:
        raise TableError(f"{len(weights)} municípios com peso; a semente tem {config.MOTORCYCLE_WEIGHTS_ROWS} e a "
                         "tabela não perde linhas")
    if set(state_weights) != set(states):
        raise TableError("a tabela de estados não tem uma linha para cada UF da malha "
                         f"({', '.join(sorted(set(state_weights) ^ set(states)))})")
    return weights, state_weights


# ---------------------------------------------------------------------------------------------------------------------
# De onde a geração parte.

def seed_tables(known_codes: Iterable[str], states: Iterable[str]) -> Tuple[TableTexts, Dict[str, Weight], Dict[str, Weight]]:
    """As cópias do repositório (a semente), conferidas pelo sha256, pela contagem e pelo formato."""
    weights = municipalities.load_weights(set(known_codes))
    state_weights = municipalities.load_state_weights(set(states))
    texts = TableTexts(config.MOTORCYCLE_WEIGHTS_COPY.read_bytes().decode("utf-8"),
                       config.MOTORCYCLE_STATE_WEIGHTS_COPY.read_bytes().decode("utf-8"), "a cópia do repositório")
    return texts, weights, state_weights


def _published_tables(cat: dict) -> Optional[Tuple[str, str, int, int, str]]:
    """(sha256 dos municípios, sha256 dos estados, contagem, contagem, url da release) do catálogo, ou None se ele não
    diz as duas tabelas."""
    municipal = cat.get("municipalities") or {}
    weights, state = municipal.get("weights"), municipal.get("state_weights")
    if not weights or not state or not cat.get("release_url"):
        return None
    return weights["sha256"], state["sha256"], int(weights["count"]), int(state["count"]), cat["release_url"]


def release_tables(cat: dict, known_codes: Iterable[str], states: Iterable[str], fetch: Fetch = net.fetch_text,
                   ) -> Tuple[TableTexts, Dict[str, Weight], Dict[str, Weight]]:
    """As tabelas da release do catálogo `cat`, baixadas e conferidas: o sha256 e a contagem do catálogo, e o formato
    de `check_tables`. Levanta TableError (ou net.DownloadError, se a rede falhar)."""
    published = _published_tables(cat)
    if published is None:
        raise TableError("o catálogo publicado não diz as tabelas de pesos")
    weights_sha, state_sha, weights_count, state_count, url = published
    texts = []
    for name, expected in ((WEIGHTS_FILE, weights_sha), (STATE_WEIGHTS_FILE, state_sha)):
        text = fetch(url + name)
        if sha256_of(text) != expected:
            raise TableError(f"{name} da release não bate com o sha256 do catálogo ({sha256_of(text)}, o catálogo diz "
                             f"{expected})")
        texts.append(text)
    weights, state_weights = check_tables(texts[0], texts[1], known_codes, states)
    if (len(weights), len(state_weights)) != (weights_count, state_count):
        raise TableError(f"a release tem {len(weights)} municípios e {len(state_weights)} UFs; o catálogo diz "
                         f"{weights_count} e {state_count}")
    return TableTexts(texts[0], texts[1], f"a release {cat['build_id']}"), weights, state_weights


def previous_tables(known_codes: Iterable[str], states: Iterable[str], catalog_path: Optional[Path] = None,
                    fetch: Fetch = net.fetch_text,
                    ) -> Tuple[TableTexts, Dict[str, Weight], Dict[str, Weight], List[str]]:
    """De onde a geração parte: (textos, pesos dos municípios, pesos dos estados, avisos).

    1. O catálogo publicado (o `catalogo.json` do checkout) diz o sha256 das tabelas da geração publicada. Se são o
       da semente, a semente vale e nada é baixado.
    2. Senão, baixa as tabelas da release dela e confere (`release_tables`).
    3. Sem catálogo, ou com a release sem tabela (404, as gerações de antes deste ciclo), ou com tabela que não
       confere, ou sem rede: a semente, com um aviso que vai para o log e para as `notes` do `geracao.json`. Nunca
       pára a geração."""
    known, state_set = list(known_codes), set(states)
    path = catalog_path or config.REPO / "catalogo.json"
    notes: List[str] = []
    cat: Optional[dict] = None
    if path.is_file():
        try:
            cat = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            notes.append(f"catálogo publicado ilegível ({e}); os pesos partem da semente do repositório")
    published = _published_tables(cat) if cat else None
    seed = (config.MOTORCYCLE_WEIGHTS_SHA256, config.MOTORCYCLE_STATE_WEIGHTS_SHA256)
    if cat and published and published[:2] != seed:
        try:
            texts, weights, state_weights = release_tables(cat, known, state_set, fetch)
            return texts, weights, state_weights, notes
        except net.PermanentError as e:
            notes.append(f"a release {cat['build_id']} não tem as tabelas de pesos ({e}); os pesos partem da "
                         "semente do repositório")
        except (TableError, net.DownloadError, ValueError) as e:
            notes.append(f"as tabelas de pesos da release {cat['build_id']} não servem ({e}); os pesos partem da "
                         "semente do repositório")
    elif cat and published is None and cat.get("municipalities") and not notes:
        # Um catálogo de antes dos pesos (sem a chave `municipalities`) é o caso da primeira geração: sem aviso.
        notes.append("o catálogo publicado não diz as tabelas de pesos; os pesos partem da semente do repositório")
    texts, weights, state_weights = seed_tables(known, state_set)
    return texts, weights, state_weights, notes


def check(catalog_path: Optional[Path] = None, fetch: Fetch = net.fetch_text) -> Tuple[Inputs, List[str]]:
    """O que a geração confere antes de tudo (no lugar de municipalities.check): a malha municipal e as tabelas de
    pesos de que ela parte. Devolve as entradas e os avisos."""
    mesh = municipalities.load_mesh()
    print(f"[ibge] malha municipal: {config.IBGE_MUNICIPAL_MESH_COPY.name}, {len(mesh)} municípios, sha256 confere")
    states = {municipalities.state_of(code) for code in mesh}
    texts, weights, state_weights, notes = previous_tables(mesh, states, catalog_path, fetch)
    print(f"[pesos] partem de {texts.origin}: {len(weights)} municípios com peso, {len(mesh) - len(weights)} sem, "
          f"{len(state_weights)} UFs, sha256 e formato conferem")
    for note in notes:
        print(f"[pesos] AVISO: {note}")
    return Inputs(mesh, weights, state_weights, texts), notes


# ---------------------------------------------------------------------------------------------------------------------
# A rotação.

def _age_key(weight: Optional[Weight], valhalla_version: str, name: str) -> Tuple[int, str, str]:
    if weight is None:
        return -1, "", name  # sem linha: um município novo, ou um que nunca deu amostra; tenta de novo a cada geração
    return (0 if weight.valhalla_version != valhalla_version else 1), weight.date, name


def select_batch(weights: Dict[str, Weight], codes: Iterable[str], size: int,
                 valhalla_version: str = config.VALHALLA_VERSION) -> List[str]:
    """Os `size` municípios que esta geração refaz: primeiro os sem linha (município novo, ou um que não deu amostra:
    são seis e custam pouco), depois os de peso calculado com outra versão do Valhalla e, então, os de linha mais velha
    (a data do cálculo); o desempate é o código. Sem sorteio: com a mesma tabela, o mesmo lote."""
    ordered = sorted(codes, key=lambda code: _age_key(weights.get(code), valhalla_version, code))
    return ordered[:max(0, size)]


def select_states(state_weights: Dict[str, Weight], states: Iterable[str], count: int,
                  valhalla_version: str = config.VALHALLA_VERSION) -> List[str]:
    """Os `count` estados que esta geração refaz, pela mesma regra de `select_batch` (o desempate é a sigla)."""
    ordered = sorted(states, key=lambda state: _age_key(state_weights.get(state), valhalla_version, state))
    return ordered[:max(0, count)]


def generations_to_cycle(total: int, size: int) -> int:
    """Quantas gerações para o lote dar a volta em `total` unidades."""
    return -(-total // size) if size > 0 else 0


# ---------------------------------------------------------------------------------------------------------------------
# Os resultados entram na tabela.

def merge(table: Dict[str, Weight], outcomes: Iterable[Outcome], tiles_build_id: str, valhalla_version: str,
          date: str) -> Tuple[Dict[str, Weight], int]:
    """(a tabela com as linhas novas, quantas linhas mudaram de data). Unidade sem peso no estudo (sem amostra) deixa a
    linha que já existia, se havia."""
    merged = dict(table)
    updated = 0
    for outcome in outcomes:
        if outcome.weight is None:
            continue
        merged[outcome.key] = Weight(outcome.weight[0], outcome.weight[1], tiles_build_id, valhalla_version, date)
        updated += 1
    return merged, updated


# ---------------------------------------------------------------------------------------------------------------------
# A tabela nova chega à geração.

class ApplyError(RuntimeError):
    """As tabelas novas não entraram na geração; `dist` ficou como estava."""


def apply_to_dist(dist: Path, tables_dir: Path, study_note: Optional[str] = None) -> List[str]:
    """Põe as tabelas novas (`tables_dir`) na geração de `dist`: confere o formato, a contagem (a tabela nova não
    perde linhas da que a geração usava) e a malha, refaz os arquivos de apoio por UF e o catalogo.json (a chave
    `municipalities` e o `contract`) e atualiza as `notes` do geracao.json. Tudo é preparado numa pasta à parte e só
    então trocado em `dist`; qualquer problema levanta ApplyError antes de tocar nele. Devolve os avisos novos."""
    catalog_path = dist / "catalogo.json"
    try:
        cat = json.loads(catalog_path.read_text(encoding="utf-8"))
        texts = TableTexts((tables_dir / WEIGHTS_FILE).read_bytes().decode("utf-8"),
                           (tables_dir / STATE_WEIGHTS_FILE).read_bytes().decode("utf-8"), "o estudo desta geração")
        mesh = municipalities.load_mesh()
        states = {municipalities.state_of(code) for code in mesh}
        weights, state_weights = check_tables(texts.weights, texts.state_weights, mesh, states)
        if (dist / WEIGHTS_FILE).is_file():
            before = municipalities.parse_weights((dist / WEIGHTS_FILE).read_text(encoding="utf-8"), set(mesh))
            if len(weights) < len(before):
                raise TableError(f"a tabela nova tem {len(weights)} municípios e a da geração {len(before)}")
    except (OSError, ValueError, KeyError, RuntimeError) as e:  # TableError é RuntimeError
        raise ApplyError(f"as tabelas novas não servem: {e}") from e

    with tempfile.TemporaryDirectory(prefix="andorinha-pesos-", dir=dist) as tmp:
        staging = Path(tmp)
        files = municipalities.generate(Inputs(mesh, weights, state_weights, texts), staging)
        in_catalog = {state: f for state, f in files.items() if state in cat["states"]}
        updated = dict(cat)
        updated["municipalities"] = catalog.municipalities_entry(in_catalog, cat["build_id"], texts.digests())
        commit = (cat.get("contract") or {}).get("generator", {}).get("commit")
        updated["contract"] = catalog.contract(updated, commit)
        notes = municipalities.generation_notes(in_catalog, cat["build_id"], config.VALHALLA_VERSION)
        record = dist / "geracao.json"
        run = json.loads(record.read_text(encoding="utf-8")) if record.is_file() else None

        # Preparado: agora a troca.
        municipalities.clean_output(dist)
        for item in sorted(staging.iterdir()):
            shutil.move(str(item), str(dist / item.name))
    catalog.save(updated, catalog_path)
    if run is not None:
        kept = [n for n in run.get("notes", []) if not n.startswith("pesos de ")]
        run["notes"] = kept + notes + ([study_note] if study_note else [])
        record.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return notes
