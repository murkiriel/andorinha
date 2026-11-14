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
      "municipalities": {...},              os arquivos de apoio por UF: contornos dos municípios e pesos da moto
      "contract": {...}                     o contrato de catálogo comum v1 (docs/CATALOGO.md; `contract` abaixo)
    }

`bbox` = [lat mín, lon mín, lat máx, lon máx] da área que o pacote atende (polígono da UF no IBGE; o Brasil, na
base), a mesma ordem do catálogo do Pardal; `bbox_tiles` = o dos tiles do pacote (em geral maior; menor onde a
UF não tem via, como ilhas oceânicas). Coordenadas em WGS84, latitude primeiro: o contrário do GeoJSON. A ordem é
declarada no README da raiz e não muda no `schema` 1.

`municipalities` (desde 2026-11-14; andorinha/municipalities.py) descreve um arquivo por UF, fora dos pacotes de tiles:

    {"format_version": 1,
     "mesh": {"url", "date", "sha256", "count"},          a cópia da malha municipal do IBGE (sha256 do conteúdo aberto)
     "weights": {"costing", "use_tolls", "sha256", "count", "generations", "same_generation"},   a tabela de pesos
     "state_weights": {...as mesmas chaves},              a tabela de pesos por estado (desde os pesos por estado)
     "states": {"GO": {"file", "bytes", "sha256", "bytes_unpacked", "municipalities", "weights", "generations",
                       "same_generation", "state_weights"}, ...}}

`states.<UF>.state_weights` é o peso do estado: {"use_highways", "use_trails", "tiles_build_id", "valhalla_version",
"date", "same_generation"}.

`generations` diz com que tiles os pesos foram calculados: [{"tiles_build_id", "valhalla_version", "date", "count"}],
no país (`weights`) e em cada UF. `same_generation` é true só quando todos são dos tiles desta geração (`build_id`).

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

from . import config, engine, municipalities, timezones
from .municipalities import MunicipalityFile
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


def municipalities_entry(files: Dict[str, MunicipalityFile], build_id: str) -> dict:
    """A chave `municipalities`: os arquivos de apoio por UF, a malha e a tabela de que saíram e, no país e em cada
    UF, com que gerações de tiles os pesos foram calculados."""
    country = municipalities.all_generations(files)
    country_states = municipalities.all_state_generations(files)
    states = {}
    for state, f in sorted(files.items()):
        states[state] = {"file": f.file, "bytes": f.size, "sha256": f.sha256, "bytes_unpacked": f.bytes_unpacked,
                         "municipalities": f.municipalities, "weights": f.weights,
                         "generations": [dict(g) for g in f.generations],
                         "same_generation": municipalities.same_generation(f.generations, build_id)}
        if f.state_weights:
            states[state]["state_weights"] = {**f.state_weights,
                                              "same_generation": f.state_weights["tiles_build_id"] == build_id}
    with_state_weights = {}
    if country_states:
        with_state_weights["state_weights"] = {
            "costing": config.MOTORCYCLE_WEIGHTS_COSTING, "use_tolls": config.MOTORCYCLE_WEIGHTS_USE_TOLLS,
            "sha256": config.MOTORCYCLE_STATE_WEIGHTS_SHA256, "count": sum(g["count"] for g in country_states),
            "generations": country_states,
            "same_generation": municipalities.same_generation(country_states, build_id)}
    return {
        "format_version": municipalities.FORMAT_VERSION,
        "mesh": {"url": config.IBGE_MUNICIPAL_MESH_URL, "date": config.IBGE_MUNICIPAL_MESH_COPY_DATE,
                 "sha256": config.IBGE_MUNICIPAL_MESH_COPY_SHA256,
                 "count": sum(f.municipalities for f in files.values())},
        "weights": {"costing": config.MOTORCYCLE_WEIGHTS_COSTING, "use_tolls": config.MOTORCYCLE_WEIGHTS_USE_TOLLS,
                    "sha256": config.MOTORCYCLE_WEIGHTS_SHA256, "count": sum(f.weights for f in files.values()),
                    "generations": country, "same_generation": municipalities.same_generation(country, build_id)},
        **with_state_weights,
        "states": states,
    }


def build_catalog(build_id: str, osm: Dict[str, object], packages: Dict[str, Package],
                  generator_commit: Optional[str] = None, generator_commit_full: Optional[str] = None,
                  municipality_files: Optional[Dict[str, MunicipalityFile]] = None) -> dict:
    """O catálogo de uma geração. `generator_commit` (curto) e `generator_commit_full` (40 dígitos) vêm do git quando
    não são dados; um SHA completo em `generator_commit` vale para os dois. `municipality_files`: os arquivos de apoio
    por UF; só entram os das UFs que têm pacote, e sem eles o catálogo sai sem a chave `municipalities`."""
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
    if municipality_files:
        cat["municipalities"] = municipalities_entry(
            {state: f for state, f in municipality_files.items() if state in cat["states"]}, build_id)
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
# Os arquivos de apoio por UF (andorinha/municipalities.py): um GeoJSON comprimido com zstd, sem tar.
MUNICIPALITIES_KIND = "auxiliary"
MUNICIPALITIES_FORMAT = "geojson"
MUNICIPALITIES_UNPACK = ["zstd"]
MUNICIPALITIES_CONTENT = "municipalities"
MUNICIPALITIES_ID = "municipios-{state}"

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
    municipal = cat.get("municipalities")
    if municipal:
        mesh, weights = municipal["mesh"], municipal["weights"]
        sources.append({"id": "ibge-malha-municipios",
                        "name": "IBGE, malha municipal (cópia versionada no repositório)", "url": mesh["url"],
                        "date": mesh["date"], "license": None, "checksums": {"sha256": mesh["sha256"]},
                        "note": "sha256 do conteúdo descompactado"})
        dates = [g["date"] for g in weights["generations"]]
        sources.append({"id": "pesos-moto-municipios",
                        "name": "Pesos do perfil de moto por município (tabela versionada no repositório)",
                        "url": None, "date": max(dates) if dates else None, "license": None,
                        "checksums": {"sha256": weights["sha256"]},
                        "note": "a geração dos tiles de cada peso está em municipalities.weights.generations"})
        by_state = municipal.get("state_weights")
        if by_state:
            dates = [g["date"] for g in by_state["generations"]]
            sources.append({"id": "pesos-moto-estados",
                            "name": "Pesos do perfil de moto por estado (tabela versionada no repositório)",
                            "url": None, "date": max(dates) if dates else None, "license": None,
                            "checksums": {"sha256": by_state["sha256"]},
                            "note": "a geração dos tiles de cada peso está em municipalities.state_weights.generations"})
    return sources


def _municipality_file(state: str, entry: dict, municipal: dict, cat: dict) -> dict:
    """O arquivo de apoio de uma UF no contrato: `auxiliary`, com a área da UF e sem nível de cobertura. As chaves
    próprias: `content`, `municipalities`, `weights` (quantos têm peso, o costing e de que gerações de tiles) e, quando
    a UF tem, `state_weights` (o peso do estado e a origem dele)."""
    weights = municipal["weights"]
    extra = {}
    if entry.get("state_weights"):
        extra["state_weights"] = {"costing": weights["costing"], "use_tolls": weights["use_tolls"],
                                  **entry["state_weights"]}
    return {
        "id": MUNICIPALITIES_ID.format(state=state), "kind": MUNICIPALITIES_KIND, "region": state,
        "url": cat["release_url"] + entry["file"], "sha256": entry["sha256"], "bytes": entry["bytes"],
        "bytes_unpacked": entry["bytes_unpacked"], "media_type": PACKAGE_MEDIA_TYPE,
        "unpack": list(MUNICIPALITIES_UNPACK), "format": MUNICIPALITIES_FORMAT,
        "bbox": list(cat["states"][state]["bbox"]), "coverage_level": None, "requires": [],
        "content": MUNICIPALITIES_CONTENT, "format_version": municipal["format_version"],
        "municipalities": entry["municipalities"],
        "weights": {"count": entry["weights"], "costing": weights["costing"], "use_tolls": weights["use_tolls"],
                    "generations": [dict(g) for g in entry["generations"]],
                    "same_generation": entry["same_generation"]},
        **extra,
    }


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
    municipal = cat.get("municipalities")
    if municipal:
        files += [_municipality_file(state, entry, municipal, cat)
                  for state, entry in sorted(municipal["states"].items()) if state in cat["states"]]
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
