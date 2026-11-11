"""O catalogo.json: o índice que quem usa os pacotes lê para saber o que baixar, de onde e como conferir.

    {
      "schema": 1,
      "build_id": "2026-09-30-cb859dc3",    data + 8 primeiros do md5 do extrato; ids diferentes não se misturam
      "built_at": "2026-09-30T12:00:00Z",
      "valhalla_version": "3.9.1",          o motor que lê estes tiles tem de ser desta versão
      "generator_commit": "2d3c2f6",        commit do gerador que produziu a geração
      "required_config": {"loki.use_connectivity": false, ...},    ajustes obrigatórios na config de quem usa
      "osm": {"timestamp": ..., "md5": ..., "url": ...},
      "release_url": "https://github.com/Murkiriel/andorinha/releases/download/2026-09-30-cb859dc3/",
      "attribution": "...",
      "timezones": {"release": "2026d", "tzids": [...]},   fusos dentro dos tiles (as zonas do Brasil)
      "base": {"file", "bytes", "sha256", "tiles", "bytes_tiles", "bbox", "bbox_tiles"},
      "states": {"GO": {"name": "Goiás", ...os mesmos campos}, ...},
      "contract": {...}                     o contrato de catálogo comum v1 (docs/CATALOGO.md; `contract` abaixo)
    }

`bbox` = [lat mín, lon mín, lat máx, lon máx] da área que o pacote atende (polígono da UF no IBGE; o Brasil, na
base), a mesma ordem do catálogo do Pardal; `bbox_tiles` = o dos tiles do pacote (em geral maior; menor onde a
UF não tem via, como ilhas oceânicas). Coordenadas em WGS84, latitude primeiro: o contrário do GeoJSON. A ordem é
declarada no README da raiz e não muda no `schema` 1 (decisão D-05 da ETD do navegador).

`contract` é o mesmo catálogo no formato comum aos quatro geradores irmãos (docs/CATALOGO.md, esquema em
docs/catalogo-v1.schema.json): só acrescenta, nenhuma chave acima muda. Sai inteiro das chaves acima e do config, então
dá para refazê-lo de um catálogo já publicado (scripts/recatalog.py).
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Mapping, Optional

from . import config, engine, timezones
from .packing import Package

SCHEMA = 1


def _entry(p: Package) -> dict:
    d = asdict(p)
    d.pop("name")
    d["bytes"] = d.pop("size")
    return d


def timezones_entry() -> dict:
    """De onde vêm os fusos dos tiles: a release do timezone-boundary-builder e as zonas incluídas (as do Brasil).
    Rota com `date_time` só responde com hora e fuso onde o nó tem uma dessas zonas."""
    return {"release": config.TIMEZONE_RELEASE, "tzids": sorted(timezones.BRAZIL_TZIDS)}


def build_id(data: str, extract_md5: str) -> str:
    """'2026-09-30' + md5 do extrato -> '2026-09-30-cb859dc3'. Dois mapas diferentes nunca dão o mesmo id, nem no
    mesmo dia; o mesmo extrato gerado de novo dá (e o publish.py barra release repetida)."""
    return f"{data}-{extract_md5[:8]}"


def git_commit_full(revision: str = "HEAD") -> Optional[str]:
    """O SHA completo (40 dígitos) de `revision` no repositório, ou None fora de um checkout do git."""
    try:
        r = subprocess.run(["git", "-C", str(config.REPO), "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    sha = r.stdout.strip()
    return sha if r.returncode == 0 and len(sha) == 40 else None


def git_commit_hash() -> str:
    """Hash curto do commit do repositório, ou 'desconhecido' fora de um checkout do git."""
    try:
        r = subprocess.run(["git", "-C", str(config.REPO), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "desconhecido"
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "desconhecido"


def build_catalog(build_id: str, osm: Dict[str, object], packages: Dict[str, Package],
                  generator_commit: Optional[str] = None, generator_commit_full: Optional[str] = None) -> dict:
    """O catálogo de uma geração. `generator_commit` (curto) e `generator_commit_full` (40 dígitos) vêm do git quando
    não são dados; um SHA completo em `generator_commit` vale para os dois."""
    if generator_commit_full is None:
        if generator_commit and len(generator_commit) == 40:
            generator_commit_full = generator_commit
        elif not generator_commit:
            generator_commit_full = git_commit_full()
    cat = {
        "schema": SCHEMA,
        "build_id": build_id,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "valhalla_version": config.VALHALLA_VERSION,
        "generator_commit": generator_commit or git_commit_hash(),
        "required_config": dict(engine.REQUIRED_CONFIG),
        "osm": {"timestamp": osm["timestamp"], "md5": osm["md5"], "url": osm["url"]},
        "release_url": config.RELEASE_URL.format(tag=build_id),
        "attribution": config.ATTRIBUTION,
        "timezones": timezones_entry(),
        "base": _entry(packages["base"]),
        "states": {state: {"name": config.STATES[state], **_entry(packages[state])}
                   for state in sorted(packages) if state != "base"},
    }
    cat["contract"] = contract(cat, generator_commit_full)
    return cat


# O contrato de catálogo comum (docs/CATALOGO.md). Os nomes e valores fixos são os da especificação v1.
CONTRACT_NAME = "catalogo-comum"
CONTRACT_VERSION = 1
CONTRACT_MIN_READER_VERSION = 1
DATASET = "andorinha"
BBOX_ORDER = "lat_min,lon_min,lat_max,lon_max"
TILES_FORMAT = "valhalla-tiles"
# Os pacotes são .tar.zst: zstd por fora, tar por dentro (packing.write_package).
PACKAGE_MEDIA_TYPE = "application/zstd"
PACKAGE_UNPACK = ["zstd", "tar"]

# As constantes do config que descrevem as entradas da geração (`sources`). O scripts/recatalog.py troca pelas do config
# no commit que gerou a geração, para não atribuir a ela um checksum que veio depois.
SOURCE_CONSTANTS = ("IBGE_MESH_URL", "IBGE_MESH_COPY_SHA256", "IBGE_MESH_COPY_DATE", "TIMEZONE_RELEASE",
                    "TIMEZONE_SHA256", "TIMEZONE_URL")


def config_sources() -> Dict[str, str]:
    return {name: getattr(config, name) for name in SOURCE_CONSTANTS}


def _sources(cat: dict, constants: Mapping[str, str]) -> list:
    osm = cat["osm"]
    sources = [{"id": "openstreetmap", "name": "OpenStreetMap (extrato do Brasil)", "url": osm["url"],
                "date": osm["timestamp"], "license": "ODbL-1.0", "checksums": {"md5": osm["md5"]}}]
    if constants.get("IBGE_MESH_COPY_SHA256"):
        sources.append({"id": "ibge-malha-uf", "name": "IBGE, malha das UFs (cópia versionada no repositório)",
                        "url": constants.get("IBGE_MESH_URL"), "date": constants.get("IBGE_MESH_COPY_DATE"),
                        "license": None, "checksums": {"sha256": constants["IBGE_MESH_COPY_SHA256"]}})
    release = (cat.get("timezones") or {}).get("release")
    if release:
        same = release == constants.get("TIMEZONE_RELEASE") and constants.get("TIMEZONE_SHA256")
        url = constants.get("TIMEZONE_URL")
        sources.append({"id": "timezone-boundary-builder", "name": "timezone-boundary-builder (fusos horários)",
                        "url": url.format(release=release) if url else None, "date": None, "version": release,
                        "license": "ODbL-1.0", "checksums": {"sha256": constants["TIMEZONE_SHA256"]} if same else {}})
    return sources


def _file(file_id: str, kind: str, region: str, entry: dict, release_url: str) -> dict:
    return {
        "id": file_id, "kind": kind, "region": region, "url": release_url + entry["file"],
        "sha256": entry["sha256"], "bytes": entry["bytes"], "bytes_unpacked": entry["bytes_tiles"],
        "media_type": PACKAGE_MEDIA_TYPE, "unpack": list(PACKAGE_UNPACK), "format": TILES_FORMAT,
        "bbox": list(entry["bbox"]),
        "coverage_level": "BASE_HIGHWAYS_ONLY" if kind == "base" else "DETAILED",
        "requires": [] if kind == "base" else ["base"],
    }


def compatibility(cat: dict) -> dict:
    """O bloco `compatibility`: o motor que lê os tiles, tudo do config. `required_config` repete a chave do topo."""
    return {
        "mix_generations": False,
        "data_format": {"name": TILES_FORMAT, "version": config.TILE_FORMAT_VERSION},
        DATASET: {
            "engine": "valhalla",
            "valhalla_engine_version": config.VALHALLA_VERSION,
            "valhalla_commit": config.VALHALLA_COMMIT,
            "tile_format_version": config.TILE_FORMAT_VERSION,
            "valhalla_mobile_version": config.VALHALLA_MOBILE_VERSION,
            "valhalla_mobile_artifact": config.VALHALLA_MOBILE_ARTIFACT,
            "valhalla_mobile_aar_sha256": config.VALHALLA_MOBILE_AAR_SHA256,
            "valhalla_mobile_abis": list(config.VALHALLA_MOBILE_ABIS),
            "required_config": dict(cat["required_config"]),
        },
    }


def contract(cat: dict, generator_commit_full: Optional[str],
             source_constants: Optional[Mapping[str, str]] = None) -> dict:
    """O bloco `contract` (docs/CATALOGO.md) a partir das chaves antigas do catálogo `cat` e do config. Recusa um
    catálogo de outra versão do Valhalla: o bloco de compatibilidade descreveria um motor que não lê aqueles tiles."""
    if cat["valhalla_version"] != config.VALHALLA_VERSION:
        raise ValueError(f"catálogo do Valhalla {cat['valhalla_version']}, o config é do {config.VALHALLA_VERSION}")
    if generator_commit_full is not None and not generator_commit_full.startswith(cat["generator_commit"]):
        raise ValueError(f"o commit {generator_commit_full} não é o {cat['generator_commit']} do catálogo")
    release_url = cat["release_url"]
    files = [_file("base", "base", "BR", cat["base"], release_url)]
    files += [_file(state, "uf", state, entry, release_url) for state, entry in sorted(cat["states"].items())]
    return {
        "name": CONTRACT_NAME,
        "version": CONTRACT_VERSION,
        "min_reader_version": CONTRACT_MIN_READER_VERSION,
        "dataset": DATASET,
        "build_id": cat["build_id"],
        "built_at": cat["built_at"],
        "release_url": release_url,
        "generator": {"repository": config.REPOSITORY_URL, "commit": generator_commit_full},
        "license": {"data": config.DATA_LICENSE, "code": config.CODE_LICENSE, "attribution": cat["attribution"]},
        "coordinates": {"crs": "EPSG:4326", "bbox_order": BBOX_ORDER},
        "osm_snapshot": {"date": cat["osm"]["timestamp"], "md5": cat["osm"]["md5"]},
        "sources": _sources(cat, config_sources() if source_constants is None else source_constants),
        "compatibility": compatibility(cat),
        "files": files,
    }


def save(sample_catalog: dict, dest: Path) -> None:
    dest.write_text(json.dumps(sample_catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
