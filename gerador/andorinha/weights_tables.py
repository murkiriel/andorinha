"""As tabelas de pesos da moto, atualizadas pelo próprio gerador: de onde a geração parte, qual lote ela refaz, como
os resultados entram na tabela e como a tabela nova chega à release.

O ciclo, sem nenhum artefato do Actions no caminho da publicação:
1. **A geração publica como sempre**, partindo de uma tabela. `previous_tables` escolhe, da release da geração
   publicada, nesta ordem: as tabelas `proximo` (o que o estudo da geração anterior devolveu), as tabelas normais
   (`pesos_moto_municipios.csv` e `pesos_moto_estados.csv`, as de que ela mesma partiu) e, por último, a semente do
   repositório, com um aviso quando uma delas existia e não serviu. Cada passo é conferido (sha256, contagem, formato,
   malha). A tabela de que a geração partiu sobe na release dela, ao lado dos pacotes.
2. **Estuda o lote depois de publicar.** Um job à parte (`weights`) baixa os pacotes da release recém-publicada pelas
   URLs públicas, extrai os tiles e refaz os pesos do lote da semana (`select_batch`: os municípios de peso mais
   velho; `select_states`: o estado de peso mais velho). O que o estudo devolve entra na tabela (`merge`), cada linha
   com a geração dos tiles, o Valhalla e o dia dela.
3. **Sobe o resultado na MESMA release**, como assets extras com nomes próprios: `pesos_moto_municipios.proximo.csv`,
   `pesos_moto_estados.proximo.csv` e `pesos_proximo.json` (sha256, contagem, lote e tempos; `next_manifest`). Não mexe
   no `catalogo.json` nem nos arquivos já publicados. A geração seguinte parte deles (passo 1): os pesos refeitos
   chegam ao app UMA GERAÇÃO DEPOIS de calculados. Se o estudo falha ou estoura o tempo, nada publicado muda.

A rotação: a cada geração, os `BATCH` municípios cujas linhas são mais velhas (os sem linha vão sempre na frente); mais
um estado, o mais velho. Com as linhas todas do mesmo dia, o desempate é o código (município) ou a sigla (UF), então a
volta é sempre a mesma: 5.570 municípios em lotes de 600 levam 10 gerações (10 semanas), e os 27 estados, 27.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from . import config, municipalities, net, publishing
from .municipalities import (NEXT_MANIFEST_FILE, NEXT_STATE_WEIGHTS_FILE, NEXT_WEIGHTS_FILE, STATE_WEIGHTS_FILE,
                             STATE_WEIGHTS_HEADER, WEIGHTS_FILE, WEIGHTS_HEADER, Inputs, TableTexts, Weight)
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


MANIFEST_VERSION = 1


def next_manifest(weights_text: str, state_text: str, weights: Dict[str, Weight], state_weights: Dict[str, Weight],
                  build_id: str, **study) -> dict:
    """O `pesos_proximo.json`: descreve as tabelas `proximo` de uma release. `build_id` é o da release (e dos tiles) em
    que o estudo rodou; `study` entra como está em `study` (lote, tempos, de onde partiu)."""
    return {"format_version": MANIFEST_VERSION, "build_id": build_id, "valhalla_version": config.VALHALLA_VERSION,
            "weights": {"file": NEXT_WEIGHTS_FILE, "sha256": sha256_of(weights_text), "count": len(weights)},
            "state_weights": {"file": NEXT_STATE_WEIGHTS_FILE, "sha256": sha256_of(state_text),
                              "count": len(state_weights)},
            "study": study}


def next_tables(cat: dict, known_codes: Iterable[str], states: Iterable[str], fetch: Fetch = net.fetch_text,
                ) -> Tuple[TableTexts, Dict[str, Weight], Dict[str, Weight]]:
    """As tabelas `proximo` da release do catálogo `cat`: baixa o json e as duas tabelas e confere que o json é da
    release do catálogo (`build_id`), o sha256 e a contagem de cada tabela contra o json, e o formato de
    `check_tables` (cabeçalho, pesos, malha, uma UF cada, no mínimo as linhas da semente). Levanta net.PermanentError
    se a release não tem o json (o estudo não rodou ou falhou: o caso comum), TableError se algo não confere."""
    url = cat["release_url"]
    manifest = json.loads(fetch(url + NEXT_MANIFEST_FILE))
    try:
        if manifest["format_version"] != MANIFEST_VERSION:
            raise TableError(f"{NEXT_MANIFEST_FILE} de versão {manifest['format_version']}")
        if manifest["build_id"] != cat["build_id"]:
            raise TableError(f"{NEXT_MANIFEST_FILE} é da geração {manifest['build_id']}, não da {cat['build_id']}")
        if manifest["valhalla_version"] != config.VALHALLA_VERSION:
            raise TableError(f"tabelas calculadas com o Valhalla {manifest['valhalla_version']}")
        entries = (manifest["weights"], manifest["state_weights"])
        names = (NEXT_WEIGHTS_FILE, NEXT_STATE_WEIGHTS_FILE)
        if [e["file"] for e in entries] != list(names):
            raise TableError(f"{NEXT_MANIFEST_FILE} descreve outros arquivos")
        texts = []
        for entry, name in zip(entries, names):
            text = fetch(url + name)
            if sha256_of(text) != entry["sha256"]:
                raise TableError(f"{name} não bate com o sha256 do {NEXT_MANIFEST_FILE}")
            texts.append(text)
        weights, state_weights = check_tables(texts[0], texts[1], known_codes, states)
        if (len(weights), len(state_weights)) != (int(entries[0]["count"]), int(entries[1]["count"])):
            raise TableError(f"{len(weights)} municípios e {len(state_weights)} UFs; o {NEXT_MANIFEST_FILE} diz "
                             f"{entries[0]['count']} e {entries[1]['count']}")
    except (KeyError, TypeError) as e:
        raise TableError(f"{NEXT_MANIFEST_FILE} sem a chave {e}") from e
    return TableTexts(texts[0], texts[1], f"as tabelas próximo da release {cat['build_id']}"), weights, state_weights


def previous_tables(known_codes: Iterable[str], states: Iterable[str], catalog_path: Optional[Path] = None,
                    fetch: Fetch = net.fetch_text,
                    ) -> Tuple[TableTexts, Dict[str, Weight], Dict[str, Weight], List[str]]:
    """De onde a geração parte: (textos, pesos dos municípios, pesos dos estados, avisos).

    O catálogo publicado (o `catalogo.json` do checkout) diz qual é a release da geração publicada. Dela, nesta ordem:
    1. as tabelas `proximo` (`next_tables`), se existem e passam em todas as conferências. Release sem elas (o estudo
       não rodou ou falhou) segue para o passo 2 sem aviso; tabelas `proximo` que existem e não servem, com aviso;
    2. as tabelas normais, de que aquela geração partiu, conferidas pelo sha256 e pela contagem do catálogo
       (`release_tables`); se são as da semente, nada é baixado;
    3. a semente do repositório (sha256 e contagem do `config.py`), com um aviso que vai para o log e para as `notes`
       do `geracao.json` quando a release devia ter as tabelas e não serviram (rede, sha256, formato). Catálogo de
       antes dos pesos, ou nenhum: a semente, sem aviso. Nunca pára a geração."""
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
    if cat and cat.get("municipalities") and cat.get("release_url"):
        try:
            texts, weights, state_weights = next_tables(cat, known, state_set, fetch)
            return texts, weights, state_weights, notes
        except net.PermanentError:
            pass  # a release não tem as tabelas próximo: o estudo daquela geração não rodou ou não terminou
        except (TableError, net.DownloadError, ValueError) as e:
            notes.append(f"as tabelas próximo da release {cat['build_id']} não servem ({e}); os pesos partem das "
                         "tabelas normais dela")
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
# O resultado sobe na release.

NEXT_FILES = (NEXT_WEIGHTS_FILE, NEXT_STATE_WEIGHTS_FILE, NEXT_MANIFEST_FILE)


class UploadError(RuntimeError):
    """O resultado do estudo não subiu; nada do que já estava publicado mudou."""


def upload_next(gh: publishing.Gh, tag: str, folder: Path) -> List[str]:
    """Sobe `pesos_moto_*.proximo.csv` e `pesos_proximo.json` de `folder` na release `tag`, que tem de estar publicada.
    Só estes três nomes: nenhum outro asset da release é tocado (o `catalogo.json`, os pacotes e as tabelas normais
    ficam como estão). Antes de subir, confere que as tabelas são as que o json descreve; depois, que os três assets
    estão lá com o tamanho certo. O json sobe por último: sem ele a geração seguinte ignora os outros dois. Devolve os
    nomes subidos. Levanta UploadError."""
    paths = [folder / name for name in NEXT_FILES]
    missing = [p.name for p in paths if not p.is_file()]
    if missing:
        raise UploadError(f"falta {', '.join(missing)} em {folder}")
    try:
        manifest = json.loads(paths[2].read_text(encoding="utf-8"))
        for entry, path in zip((manifest["weights"], manifest["state_weights"]), paths[:2]):
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                raise UploadError(f"{path.name} não bate com o sha256 do {NEXT_MANIFEST_FILE}")
        if manifest["build_id"] != tag:
            raise UploadError(f"o {NEXT_MANIFEST_FILE} é da geração {manifest['build_id']}, não da {tag}")
    except (ValueError, KeyError, TypeError) as e:
        raise UploadError(f"{NEXT_MANIFEST_FILE} ilegível ({e.__class__.__name__}: {e})") from e
    try:
        state, assets = publishing.release_state(gh, tag)
        if state != "publicada":
            raise UploadError(f"a release {tag} não está publicada ({state}); nada foi subido")
        for path in paths:
            publishing._run(gh, ["release", "upload", tag, str(path), "--clobber"])
        _, assets = publishing.release_state(gh, tag)
    except publishing.GhError as e:
        raise UploadError(str(e)) from e
    wrong = [p.name for p in paths if assets.get(p.name) != p.stat().st_size]
    if wrong:
        raise UploadError(f"a release {tag} continua sem {', '.join(wrong)} (ou com outro tamanho)")
    return [p.name for p in paths]
