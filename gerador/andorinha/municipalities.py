"""Os arquivos de apoio por UF: o contorno de cada município e o peso do perfil de moto que serve a ele.

Para um leitor escolher o peso do costing `motorcycle` quando a viagem começa e termina no mesmo município. Cada UF
ganha um arquivo, `andorinha-municipios-<UF>.geojson.zst`, publicado ao lado dos pacotes de tiles e descrito no
catálogo. Fica FORA do `.tar.zst` da UF: quem lê os pacotes recusa um caminho que não seja de tile e confere a contagem
e o tamanho dos tiles, e um arquivo a mais quebraria a instalação de um leitor que já existe.

Duas entradas, as duas cópias versionadas no repositório, conferidas antes do extrato e dos tiles e sem rede:
- a malha municipal do IBGE (config.IBGE_MUNICIPAL_MESH_COPY): sha256 do conteúdo e a contagem de municípios;
- a tabela de pesos (config.MOTORCYCLE_WEIGHTS_COPY): sha256, contagem, valores no intervalo e códigos que existem na
  malha. Ela vem de um estudo feito fora da geração; aqui só é empacotada.

Cada linha da tabela diz com que geração de tiles, com que Valhalla e em que dia aquele município foi calculado: a
tabela pode ser refeita aos pedaços. O catálogo resume isso por UF (`generations`), e `generation_notes` avisa, sem
parar a geração, quando há pesos calculados com tiles de outra geração.

O arquivo de uma UF (GeoJSON, longitude primeiro, como manda o formato):

    {"type": "FeatureCollection",
     "andorinha": {"format_version": 1, "state": "GO",
                   "weights": {"costing": "motorcycle", "use_tolls": 1.0, "count": 246, "generations": [...]}},
     "features": [{"type": "Feature",
                   "properties": {"codarea": "5208707", "use_highways": 0.6, "use_trails": 0.4,
                                  "tiles_build_id": "2026-11-09-c17dff60", "valhalla_version": "3.9.1",
                                  "date": "2026-11-14"},
                   "geometry": {...}}, ...]}

Município sem peso (sem amostra no estudo): as cinco chaves do peso vêm `null`, e o leitor usa o peso padrão dele. O
arquivo não leva o `build_id` da geração: com a mesma malha e a mesma tabela ele sai igual, byte a byte, de uma geração
para outra.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import config
from .util import file_hash

FORMAT_VERSION = 1
FILE_NAME = "andorinha-municipios-{state}.geojson.zst"
FILE_GLOB = "andorinha-municipios-*.geojson.zst"
INDEX_FILE = "municipios.json"  # o que a etapa `packages` gravou, para --from validate e --from catalog
WEIGHTS_HEADER = ["codigo_ibge", "use_highways", "use_trails", "geracao_tiles", "versao_valhalla", "data_calculo"]

_CODE = re.compile(r"^[0-9]{7}$")
_WEIGHT = re.compile(r"^(0\.[0-9]|1\.0)$")  # uma casa decimal, de 0.0 a 1.0: o que o estudo grava
_BUILD_ID = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9a-f]{8}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


@dataclass(frozen=True)
class Weight:
    """O peso de um município e com o que ele foi calculado."""
    use_highways: float
    use_trails: float
    tiles_build_id: str
    valhalla_version: str
    date: str


@dataclass
class Inputs:
    """As duas cópias versionadas, já conferidas."""
    mesh: Dict[str, dict]        # {código do município: geometria GeoJSON}
    weights: Dict[str, Weight]   # {código do município: peso}; os sem amostra ficam de fora


@dataclass
class MunicipalityFile:
    """O arquivo de apoio de uma UF, como vai para o catálogo."""
    state: str
    file: str
    size: int            # bytes do .geojson.zst (no catálogo, a chave `bytes`)
    sha256: str
    bytes_unpacked: int  # bytes do GeoJSON aberto
    municipalities: int
    weights: int         # quantos municípios da UF têm peso
    generations: List[dict] = field(default_factory=list)  # de que gerações de tiles vieram os pesos da UF


def state_of(code: str) -> Optional[str]:
    """A UF de um código de município do IBGE (os dois primeiros dígitos), ou None."""
    return config.IBGE_STATE_CODES.get(code[:2])


def mesh_problem(body: bytes) -> Optional[str]:
    """O que há de errado com a malha municipal (o GeoJSON aberto), ou None se ela tem os config.MUNICIPALITY_COUNT
    municípios, cada um com código de 7 dígitos de uma UF conhecida e um polígono, e nenhuma UF vazia.

    Uma API pode responder 200 com uma página de erro, ou com a malha de outro recorte: ao trocar a cópia, isto barra."""
    try:
        gj = json.loads(body.decode("utf-8"))
    except ValueError as e:
        return f"não é JSON ({e.__class__.__name__})"
    features = gj.get("features") if isinstance(gj, dict) else None
    if not isinstance(features, list):
        return "sem a lista `features`"
    codes = set()
    for f in features:
        code = str(((f.get("properties") or {}) if isinstance(f, dict) else {}).get("codarea"))
        if not _CODE.match(code) or state_of(code) is None:
            return f"código de município inesperado: {code}"
        if code in codes:
            return f"município repetido: {code}"
        geometry = f.get("geometry")
        if not isinstance(geometry, dict) or geometry.get("type") not in ("Polygon", "MultiPolygon") \
                or not geometry.get("coordinates"):
            return f"município {code} sem polígono"
        codes.add(code)
    if len(codes) != config.MUNICIPALITY_COUNT:
        return f"{len(codes)} municípios; o config espera {config.MUNICIPALITY_COUNT}"
    missing = sorted(set(config.STATES) - {state_of(code) for code in codes})
    return f"faltam as UFs {', '.join(missing)}" if missing else None


def load_mesh() -> Dict[str, dict]:
    """{código: geometria} da cópia versionada da malha municipal, conferida pelo sha256 do conteúdo e por
    `mesh_problem`. Nada é baixado: a API do IBGE não responde aos executores do Actions."""
    copy = config.IBGE_MUNICIPAL_MESH_COPY
    try:
        body = gzip.decompress(copy.read_bytes())
    except (OSError, EOFError) as e:
        raise RuntimeError(f"a malha municipal em {copy.name} não abre ({e.__class__.__name__}: {e})")
    sha256 = hashlib.sha256(body).hexdigest()
    if sha256 != config.IBGE_MUNICIPAL_MESH_COPY_SHA256:
        raise RuntimeError(f"{copy.name} mudou (sha256 do conteúdo {sha256}, o config espera "
                           f"{config.IBGE_MUNICIPAL_MESH_COPY_SHA256}): confira a malha nova e atualize o "
                           "IBGE_MUNICIPAL_MESH_COPY_SHA256")
    problem = mesh_problem(body)
    if problem is not None:
        raise RuntimeError(f"a malha municipal em {copy.name} não serve ({problem})")
    return {f["properties"]["codarea"]: f["geometry"] for f in json.loads(body.decode("utf-8"))["features"]}


def parse_weights(text: str, known_codes) -> Dict[str, Weight]:
    """A tabela de pesos (`;`, uma linha por município). Levanta ValueError no primeiro problema: cabeçalho, linha
    malformada, código repetido ou que não está na malha, peso fora de 0 a 1 (use_trails acima do teto do estudo),
    geração, versão ou data fora do formato."""
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    if not rows or rows[0] != WEIGHTS_HEADER:
        raise ValueError(f"cabeçalho inesperado: {rows[0] if rows else '(vazio)'}")
    out: Dict[str, Weight] = {}
    for number, row in enumerate(rows[1:], start=2):
        if len(row) != len(WEIGHTS_HEADER):
            raise ValueError(f"linha {number}: {len(row)} colunas")
        code, highways, trails, tiles, version, date = row
        if code in out:
            raise ValueError(f"linha {number}: município repetido ({code})")
        if code not in known_codes:
            raise ValueError(f"linha {number}: o município {code} não está na malha")
        if not _WEIGHT.match(highways) or not _WEIGHT.match(trails):
            raise ValueError(f"linha {number}: peso fora de 0.0 a 1.0 com uma casa ({highways}, {trails})")
        if float(trails) > config.MOTORCYCLE_WEIGHTS_MAX_USE_TRAILS:
            raise ValueError(f"linha {number}: use_trails {trails} acima do teto "
                             f"{config.MOTORCYCLE_WEIGHTS_MAX_USE_TRAILS}")
        if not _BUILD_ID.match(tiles) or not _VERSION.match(version) or not _DATE.match(date):
            raise ValueError(f"linha {number}: geração, versão ou data fora do formato ({tiles}, {version}, {date})")
        out[code] = Weight(float(highways), float(trails), tiles, version, date)
    return out


def load_weights(known_codes) -> Dict[str, Weight]:
    """{código: peso} da cópia versionada da tabela, conferida pelo sha256, pela contagem e por `parse_weights`."""
    copy = config.MOTORCYCLE_WEIGHTS_COPY
    try:
        sha256 = file_hash(copy, "sha256")
        text = copy.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise RuntimeError(f"a tabela de pesos em {copy.name} não abre ({e.__class__.__name__}: {e})")
    if sha256 != config.MOTORCYCLE_WEIGHTS_SHA256:
        raise RuntimeError(f"{copy.name} mudou (sha256 {sha256}, o config espera {config.MOTORCYCLE_WEIGHTS_SHA256}): "
                           "confira a tabela nova e atualize o MOTORCYCLE_WEIGHTS_SHA256 e o MOTORCYCLE_WEIGHTS_ROWS")
    try:
        weights = parse_weights(text, known_codes)
    except ValueError as e:
        raise RuntimeError(f"a tabela de pesos em {copy.name} não serve ({e})")
    if len(weights) != config.MOTORCYCLE_WEIGHTS_ROWS:
        raise RuntimeError(f"a tabela de pesos em {copy.name} tem {len(weights)} municípios; o config espera "
                           f"{config.MOTORCYCLE_WEIGHTS_ROWS}")
    return weights


def check() -> Inputs:
    """Confere as duas cópias e devolve o conteúdo. Vem antes do extrato e dos tiles: uma cópia que não serve para a
    geração em segundos."""
    mesh = load_mesh()
    print(f"[ibge] malha municipal: {config.IBGE_MUNICIPAL_MESH_COPY.name}, {len(mesh)} municípios, sha256 confere")
    weights = load_weights(mesh)
    print(f"[pesos] {config.MOTORCYCLE_WEIGHTS_COPY.name}: {len(weights)} municípios com peso, "
          f"{len(mesh) - len(weights)} sem, sha256 confere")
    return Inputs(mesh, weights)


def generations(weights) -> List[dict]:
    """De que gerações de tiles vieram os pesos dados: uma entrada por (geração, Valhalla, dia), com a contagem, em
    ordem."""
    counts: Dict[tuple, int] = {}
    for w in weights:
        key = (w.tiles_build_id, w.valhalla_version, w.date)
        counts[key] = counts.get(key, 0) + 1
    return [{"tiles_build_id": tiles, "valhalla_version": version, "date": date, "count": count}
            for (tiles, version, date), count in sorted(counts.items())]


def by_state(inputs: Inputs) -> Dict[str, List[str]]:
    """{UF: códigos dos municípios dela, em ordem}, pelos dois primeiros dígitos do código do IBGE."""
    out: Dict[str, List[str]] = {state: [] for state in sorted(config.STATES)}
    for code in sorted(inputs.mesh):
        out[state_of(code)].append(code)
    return out


def _feature(code: str, geometry: dict, weight: Optional[Weight]) -> dict:
    properties = {"codarea": code,
                  "use_highways": weight.use_highways if weight else None,
                  "use_trails": weight.use_trails if weight else None,
                  "tiles_build_id": weight.tiles_build_id if weight else None,
                  "valhalla_version": weight.valhalla_version if weight else None,
                  "date": weight.date if weight else None}
    return {"type": "Feature", "properties": properties, "geometry": geometry}


def state_document(state: str, codes: List[str], inputs: Inputs) -> dict:
    """O GeoJSON de uma UF: os municípios dela em ordem de código, cada um com o peso e a origem do peso."""
    with_weight = [inputs.weights[code] for code in codes if code in inputs.weights]
    return {
        "type": "FeatureCollection",
        "andorinha": {
            "format_version": FORMAT_VERSION,
            "state": state,
            "weights": {"costing": config.MOTORCYCLE_WEIGHTS_COSTING, "use_tolls": config.MOTORCYCLE_WEIGHTS_USE_TOLLS,
                        "count": len(with_weight), "generations": generations(with_weight)},
        },
        "features": [_feature(code, inputs.mesh[code], inputs.weights.get(code)) for code in codes],
    }


def encode(document: dict) -> bytes:
    """O GeoJSON em bytes, sempre igual para o mesmo conteúdo (sem espaços, na ordem das chaves do documento)."""
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def write_state_file(state: str, codes: List[str], inputs: Inputs, output: Path) -> MunicipalityFile:
    """Grava o arquivo de uma UF em `output` (zstd no nível dos pacotes, um quadro só com o tamanho no cabeçalho) e
    lê de volta o que gravou."""
    import zstandard

    document = state_document(state, codes, inputs)
    body = encode(document)
    dest = output / FILE_NAME.format(state=state)
    dest.write_bytes(zstandard.ZstdCompressor(level=config.ZSTD_LEVEL).compress(body))
    if zstandard.ZstdDecompressor().decompress(dest.read_bytes()) != body:
        raise RuntimeError(f"{dest.name} não abre de volta igual ao que foi gravado")
    weights = document["andorinha"]["weights"]
    return MunicipalityFile(state, dest.name, dest.stat().st_size, file_hash(dest), len(body), len(codes),
                            weights["count"], weights["generations"])


def clean_output(output: Path) -> None:
    """Apaga os arquivos de apoio e o índice da geração anterior."""
    for old in [*output.glob(FILE_GLOB), output / INDEX_FILE]:
        if old.exists():
            old.unlink()


def generate(inputs: Inputs, output: Path) -> Dict[str, MunicipalityFile]:
    """Grava o arquivo de apoio de cada UF em `output` e o índice deles (INDEX_FILE)."""
    output.mkdir(parents=True, exist_ok=True)
    clean_output(output)
    out: Dict[str, MunicipalityFile] = {}
    for state, codes in by_state(inputs).items():
        f = write_state_file(state, codes, inputs, output)
        print(f"[municípios] {state}: {f.municipalities:4d} municípios, {f.weights:4d} com peso, "
              f"{f.bytes_unpacked / 1e3:7.1f} kB -> {f.size / 1e3:6.1f} kB")
        out[state] = f
    save_index(out, output)
    total = sum(f.size for f in out.values())
    print(f"[municípios] {len(out)} arquivos, {sum(f.municipalities for f in out.values())} municípios, "
          f"{total / 1e6:.2f} MB compactados")
    return out


def save_index(files: Dict[str, MunicipalityFile], output: Path) -> None:
    (output / INDEX_FILE).write_text(json.dumps({k: vars(v) for k, v in files.items()}, indent=2), encoding="utf-8")


def load_index(output: Path) -> Dict[str, MunicipalityFile]:
    data = json.loads((output / INDEX_FILE).read_text(encoding="utf-8"))
    return {k: MunicipalityFile(**v) for k, v in data.items()}


def all_generations(files: Dict[str, MunicipalityFile]) -> List[dict]:
    """O resumo do país: as gerações dos pesos de todas as UFs, somadas."""
    counts: Dict[tuple, int] = {}
    for f in files.values():
        for g in f.generations:
            key = (g["tiles_build_id"], g["valhalla_version"], g["date"])
            counts[key] = counts.get(key, 0) + g["count"]
    return [{"tiles_build_id": tiles, "valhalla_version": version, "date": date, "count": count}
            for (tiles, version, date), count in sorted(counts.items())]


def same_generation(generations_: List[dict], build_id: str) -> bool:
    """Todos os pesos foram calculados com os tiles da geração `build_id`? (Sem peso nenhum: sim.)"""
    return all(g["tiles_build_id"] == build_id for g in generations_)


def generation_notes(files: Dict[str, MunicipalityFile], build_id: str, valhalla_version: str) -> List[str]:
    """Os avisos de uma geração `build_id` sobre a idade dos pesos (vazio = todos são dos tiles dela). Não é motivo
    para parar: o estudo dos pesos não roda a cada geração, e o catálogo diz de que geração cada UF tem os pesos."""
    notes = []
    for g in all_generations(files):
        if g["tiles_build_id"] != build_id:
            states = sorted(s for s, f in files.items()
                            if any(x["tiles_build_id"] == g["tiles_build_id"] for x in f.generations))
            notes.append(f"pesos de {g['count']} municípios ({len(states)} UFs) calculados com os tiles da geração "
                         f"{g['tiles_build_id']} (Valhalla {g['valhalla_version']}, em {g['date']}), não com os da "
                         f"{build_id}")
        if g["valhalla_version"] != valhalla_version:
            notes.append(f"pesos de {g['count']} municípios calculados com o Valhalla {g['valhalla_version']}; os "
                         f"tiles desta geração são do {valhalla_version}")
    return notes
