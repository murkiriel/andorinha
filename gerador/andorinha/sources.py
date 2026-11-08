"""Fontes: o extrato do Brasil no OpenStreetMap (baixado) e a malha das UFs do IBGE (cópia versionada).

O extrato vem da primeira fonte de `config.OSM_SOURCES` que entregar um arquivo conferido pelo md5 publicado: o
openstreetmap.fr e, se ele falhar (fora do ar, tempo esgotado, md5 que não confere), a Geofabrik. Nas duas, o extrato
só é baixado de novo quando há versão mais nova que a local.

O openstreetmap.fr troca o extrato uma vez por dia (por volta de 01:00 UTC), sempre na mesma URL. Se a troca acontece
durante o download (~25 min), o arquivo baixado é o novo e o md5 lido antes é o velho: nesse caso o md5 e a data são
lidos de novo e, se mudaram, a versão nova é baixada uma vez. Se não mudaram, o arquivo veio corrompido.

Na Geofabrik, o `-latest` redireciona para o arquivo datado do dia (brazil-AAMMDD.osm.pbf), que não muda depois de
publicado e tem o próprio .md5: o download não corre contra a troca diária. Só o redirecionamento é lido (sem
segui-lo). Em 2026-09, a partir de uma rede doméstica, seguir o `-latest` entrou em redirecionamento sem fim.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import config, net
from .util import file_hash

# Espera base entre tentativas, em segundos (multiplicada pelo número da tentativa). Os testes zeram.
RETRY_DELAY = 10

Extract = Tuple[str, str, str]  # (url do arquivo baixado, timestamp, md5)


def _state_timestamp(url: str) -> str:
    """O `timestamp` de um state.txt de replicação (os `:` vêm escapados)."""
    state = dict(line.split("=", 1) for line in net.fetch_text(url, delay=RETRY_DELAY).splitlines()
                 if "=" in line and not line.startswith("#"))
    return state["timestamp"].strip().replace("\\:", ":")


def _published() -> Tuple[str, str]:
    """(timestamp, md5) do extrato publicado agora no openstreetmap.fr."""
    timestamp = _state_timestamp(config.OSM_STATE_URL)
    md5 = net.fetch_text(config.OSM_MD5_URL, delay=RETRY_DELAY).split()[0]
    return timestamp, md5


def _local_matches(pbf: Path, local_meta: Path, md5: str) -> bool:
    """O extrato local é o publicado? Confia no .meta.json só se o tamanho também bater; senão confere pelo md5."""
    if not pbf.exists():
        return False
    if local_meta.exists():
        meta = json.loads(local_meta.read_text(encoding="utf-8"))
        if meta.get("md5") == md5 and meta.get("size") == pbf.stat().st_size:
            return True
    return file_hash(pbf, "md5") == md5  # extrato copiado à mão, meta antigo ou arquivo mexido


def _from_openstreetmap_fr(pbf: Path, local_meta: Path) -> Extract:
    timestamp, md5 = _published()
    if _local_matches(pbf, local_meta, md5):
        print(f"[osm] extrato local já é o mais novo ({timestamp})")
        return config.OSM_PBF_URL, timestamp, md5
    for attempt in (1, 2):
        print(f"[osm] baixando o extrato de {timestamp} do openstreetmap.fr...")
        net.download(config.OSM_PBF_URL, pbf, identity=md5, delay=RETRY_DELAY)
        local = file_hash(pbf, "md5")
        if local == md5:
            break
        timestamp_now, md5_now = _published()
        if attempt == 1 and md5_now != md5:
            print(f"[osm] o extrato foi atualizado durante o download ({timestamp_now}); baixando a versão nova")
            timestamp, md5 = timestamp_now, md5_now
            continue
        pbf.unlink()
        raise RuntimeError(f"md5 do extrato não confere: {local} != {md5_now} publicado")
    return config.OSM_PBF_URL, timestamp, md5


def _geofabrik_dated() -> Tuple[str, str]:
    """(url do arquivo datado de agora, timestamp dele). O state.txt é lido depois do `-latest`; se a troca diária
    cair entre os dois, a data do state não é a do arquivo, e os dois são lidos de novo uma vez."""
    for attempt in (1, 2):
        url = net.redirect_target(config.GEOFABRIK_LATEST_URL, delay=RETRY_DELAY)
        name = re.search(r"/brazil-(\d{6})\.osm\.pbf$", url)
        if name is None:
            raise RuntimeError(f"o -latest da Geofabrik aponta um arquivo inesperado: {url}")
        day = name.group(1)
        timestamp = _state_timestamp(config.GEOFABRIK_STATE_URL)
        if timestamp[2:10].replace("-", "") == day:
            return url, timestamp
    raise RuntimeError(f"o state.txt da Geofabrik ({timestamp}) não é do dia do arquivo brazil-{day}")


def _from_geofabrik(pbf: Path, local_meta: Path) -> Extract:
    url, timestamp = _geofabrik_dated()
    md5 = net.fetch_text(url + ".md5", delay=RETRY_DELAY).split()[0]
    if _local_matches(pbf, local_meta, md5):
        print(f"[osm] extrato local já é o mais novo ({timestamp})")
        return url, timestamp, md5
    print(f"[osm] baixando o extrato de {timestamp} da Geofabrik ({url.rsplit('/', 1)[-1]})...")
    net.download(url, pbf, identity=md5, delay=RETRY_DELAY)
    local = file_hash(pbf, "md5")
    if local != md5:  # o arquivo datado não muda: md5 diferente é arquivo corrompido
        pbf.unlink()
        raise RuntimeError(f"md5 do extrato não confere: {local} != {md5} publicado")
    return url, timestamp, md5


SOURCES: Dict[str, Callable[[Path, Path], Extract]] = {
    "openstreetmap.fr": _from_openstreetmap_fr,
    "geofabrik": _from_geofabrik,
}


def osm_extract() -> Dict[str, object]:
    """Garante data/raw/brazil-latest.osm.pbf na versão mais nova da primeira fonte que funcionar.
    Devolve {path, url, timestamp, md5, size}; `url` diz de qual fonte (e, na Geofabrik, de qual dia) ele veio."""
    unknown = [s for s in config.OSM_SOURCES if s not in SOURCES]
    if unknown or not config.OSM_SOURCES:
        raise ValueError(f"fonte do extrato desconhecida: {', '.join(unknown) or '(nenhuma)'} "
                         f"(as que existem: {', '.join(SOURCES)})")
    config.RAW.mkdir(parents=True, exist_ok=True)
    pbf = config.RAW / "brazil-latest.osm.pbf"
    local_meta = config.RAW / "brazil-latest.meta.json"

    failures: List[str] = []
    found: Optional[Extract] = None
    for source in config.OSM_SOURCES:
        try:
            found = SOURCES[source](pbf, local_meta)
            break
        except (RuntimeError, OSError, KeyError, IndexError) as e:  # RuntimeError inclui net.DownloadError
            failures.append(f"{source}: {e.__class__.__name__}: {e}")
            print(f"[osm] AVISO: {source} falhou ({e.__class__.__name__}: {e})")
    if found is None:
        raise RuntimeError("nenhuma fonte entregou o extrato: " + "; ".join(failures))
    url, timestamp, md5 = found
    if failures:
        print(f"[osm] extrato de {timestamp} veio da reserva: {url}")
    print(f"[osm] {pbf.stat().st_size / 1e9:.2f} GB, md5 confere")
    meta = {"path": str(pbf), "url": url, "timestamp": timestamp, "md5": md5, "size": pbf.stat().st_size}
    local_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def mesh_problem(path: Path) -> Optional[str]:
    """O que há de errado com o arquivo da malha das UFs, ou None se é um GeoJSON com as 27 UFs.

    Uma API pode responder 200 com uma página de erro: ao trocar a cópia versionada por uma baixada de novo, esta
    conferência barra uma página de erro ou uma malha sem alguma UF."""
    try:
        gj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return f"não é JSON ({e.__class__.__name__})"
    features = gj.get("features") if isinstance(gj, dict) else None
    if not isinstance(features, list):
        return "sem a lista `features`"
    codes = {str((f.get("properties") or {}).get("codarea")) for f in features if isinstance(f, dict)}
    missing = sorted(state for code, state in config.IBGE_STATE_CODES.items() if code not in codes)
    return f"faltam as UFs {', '.join(missing)}" if missing else None


def ibge_mesh() -> Path:
    """A malha das UFs (GeoJSON das 27 UFs, código IBGE em `codarea`): a cópia versionada, config.IBGE_MESH_COPY,
    conferida pelas 27 UFs e pelo sha256 do config. Nada é baixado: a mesma malha gera sempre a mesma divisão dos
    pacotes, e a geração não depende da API do IBGE (que deixou de responder ao Actions em 2026-11-03)."""
    copy = config.IBGE_MESH_COPY
    problem = mesh_problem(copy)
    if problem is not None:
        raise RuntimeError(f"a malha do IBGE em {copy.name} não serve ({problem})")
    sha256 = file_hash(copy, "sha256")
    if sha256 != config.IBGE_MESH_COPY_SHA256:
        raise RuntimeError(f"{copy.name} mudou (sha256 {sha256}, o config espera {config.IBGE_MESH_COPY_SHA256}): "
                           "uma malha nova muda a divisão dos pacotes; confira e atualize o IBGE_MESH_COPY_SHA256")
    print(f"[ibge] malha das UFs: {copy.name}, 27 UFs, sha256 confere")
    return copy
