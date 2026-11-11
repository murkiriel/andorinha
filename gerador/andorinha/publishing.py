"""As travas e a subida da publicação: o que precisa estar certo antes de uma geração ir para a release e para o
catálogo, e a própria release.

A linha de comando fica em scripts/publish.py; aqui só a lógica, testável sem o GitHub: o `gh` entra como uma função
`gh(args) -> (código, stdout, stderr)`, e os testes passam uma falsa.

A release é criada como RASCUNHO, sem arquivos; cada arquivo (os pacotes, o LICENCA-DADOS.txt e o catalogo.json da
geração) que ainda não está lá (ou está com outro tamanho) sobe; só com todos presentes e do tamanho certo ela é
publicada. Uma queda no meio deixa um rascunho que ninguém vê, e
rodar de novo retoma do que falta. Release já publicada com o mesmo tag não é mexida.

Depois que o catálogo novo está publicado, as releases antigas são apagadas (`prune_releases`): ficam a do catálogo
e as anteriores mais recentes.
"""
from __future__ import annotations

import json
import re
import subprocess
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import config
from .util import file_hash

Gh = Callable[[Sequence[str]], Tuple[int, str, str]]

ANONYMOUS_SUFFIX = "@users.noreply.github.com"

# O catálogo da geração também sobe na release: o da raiz do repositório sempre aponta para a mais recente, e o de uma
# geração anterior só ficaria no histórico do git.
CATALOG_FILE = "catalogo.json"


class GhError(RuntimeError):
    """Um comando do gh falhou; a mensagem traz o comando e o que o gh disse."""


class ReleaseAlreadyPublished(RuntimeError):
    """Já existe uma release publicada (não rascunho) com este tag."""


def gh_real(args: Sequence[str]) -> Tuple[int, str, str]:
    """O `gh` de verdade, no repositório do projeto."""
    r = subprocess.run(["gh", *args, "--repo", config.GITHUB_REPO], capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def _run(gh: Gh, args: Sequence[str]) -> str:
    code, output, error = gh(list(args))
    if code != 0:
        raise GhError(f"gh {' '.join(args)} falhou (código {code}): {error.strip() or output.strip()}")
    return output


def release_state(gh: Gh, tag: str) -> Tuple[str, Dict[str, int]]:
    """('inexistente' | 'rascunho' | 'publicada', {arquivo: tamanho}). Só 'release not found' conta como inexistente:
    sem rede ou sem login é erro, para nunca tentar criar uma release por engano."""
    args = ["release", "view", tag, "--json", "isDraft,assets"]
    code, output, error = gh(args)
    if code != 0:
        if "not found" in error.lower():
            return "inexistente", {}
        raise GhError(f"gh {' '.join(args)} falhou (código {code}): {error.strip() or output.strip()}")
    data = json.loads(output)
    assets = {a["name"]: int(a["size"]) for a in data.get("assets", [])}
    return ("rascunho" if data.get("isDraft") else "publicada"), assets


def publish_release(gh: Gh, tag: str, files: List[Path], title: str, notes: str) -> None:
    """Cria (ou retoma) o rascunho, sobe o que falta e publica. Levanta ReleaseJaPublicada ou ErroGh."""
    state, assets = release_state(gh, tag)
    if state == "publicada":
        raise ReleaseAlreadyPublished(f"a release {tag} já está publicada; gere de novo (outro build_id) "
                                      "ou apague-a antes")
    if state == "inexistente":
        _run(gh, ["release", "create", tag, "--draft", "--title", title, "--notes", notes])
        assets = {}
    for file in files:
        size = file.stat().st_size
        # Já subiu inteiro numa tentativa anterior. O catálogo sobe sempre de novo: uma geração refeita com o mesmo
        # build_id (mesmo extrato) dá pacotes idênticos, mas outro built_at, com o mesmo tamanho.
        if assets.get(file.name) == size and file.name != CATALOG_FILE:
            continue
        extra = ["--clobber"] if file.name in assets else []
        print(f"[release] subindo {file.name} ({size / 1e6:.1f} MB)...")
        _run(gh, ["release", "upload", tag, str(file), *extra])
    _, assets = release_state(gh, tag)
    missing = [a.name for a in files if assets.get(a.name) != a.stat().st_size]
    if missing:
        raise GhError(f"a release {tag} continua sem {', '.join(missing)} (ou com outro tamanho); rode de novo")
    _run(gh, ["release", "edit", tag, "--draft=false"])


class PruneRefused(RuntimeError):
    """A limpeza não apaga nada: a release do catálogo publicado não está no ar."""


# Só releases com tag no formato do build_id (data + 8 primeiros do md5 do extrato) são desta automação.
BUILD_ID_TAG = re.compile(r"^\d{4}-\d{2}-\d{2}-[0-9a-f]{8}$")


def _age_key(release: dict) -> Tuple[str, str]:
    """Ordem das gerações: a data do build_id e, no mesmo dia, a hora da publicação."""
    return release["tagName"][:10], release.get("publishedAt") or ""


def releases_to_delete(releases: List[dict], current_tag: str, keep: int) -> List[str]:
    """Tags a apagar, da mais antiga para a mais nova. Ficam a release do catálogo publicado (`current_tag`) e as
    `keep - 1` gerações publicadas mais recentes além dela; saem as demais e os rascunhos órfãos (uma subida que caiu
    deixa um rascunho que a geração seguinte, com outro build_id, nunca retoma). Release com tag fora do formato do
    build_id nunca entra. `keep` 0 (ou menos) desliga a limpeza."""
    if keep <= 0:
        return []
    ours = [r for r in releases if BUILD_ID_TAG.match(r["tagName"]) and r["tagName"] != current_tag]
    published = sorted((r for r in ours if not r.get("isDraft")), key=_age_key, reverse=True)
    doomed = published[keep - 1:] + [r for r in ours if r.get("isDraft")]
    return [r["tagName"] for r in sorted(doomed, key=_age_key)]


def prune_releases(gh: Gh, current_tag: str, keep: int, dry_run: bool = False) -> List[str]:
    """Apaga as releases antigas (e as tags delas) e devolve as tags apagadas; com `dry_run`, só devolve o que
    apagaria. Levanta PruneRefused, sem apagar nada, se a release do catálogo não estiver publicada: é o sinal de
    que o catálogo e as releases não batem."""
    if keep <= 0:
        return []
    releases = json.loads(_run(gh, ["release", "list", "--limit", "1000", "--json", "tagName,isDraft,publishedAt"]))
    current = next((r for r in releases if r["tagName"] == current_tag), None)
    if current is None or current.get("isDraft"):
        raise PruneRefused(f"a release {current_tag} do catálogo não está publicada; nenhuma release foi apagada")
    drafts = {r["tagName"] for r in releases if r.get("isDraft")}
    doomed = releases_to_delete(releases, current_tag, keep)
    if not dry_run:
        for tag in doomed:
            print(f"[release] apagando a release antiga {tag}...")
            # um rascunho ainda não tem tag no git
            _run(gh, ["release", "delete", tag, "--yes"] + ([] if tag in drafts else ["--cleanup-tag"]))
    return doomed


def prune_after_publish(gh: Gh, current_tag: str, keep: int) -> Tuple[List[str], Optional[str]]:
    """A limpeza no fim de uma publicação: (tags apagadas, aviso). Nunca levanta: a geração já está publicada, e o
    que falhar aqui a próxima publicação tenta de novo."""
    try:
        return prune_releases(gh, current_tag, keep), None
    except (GhError, PruneRefused, ValueError, KeyError) as e:
        return [], f"releases antigas não apagadas: {e}"


def identity_problem(email: str) -> Optional[str]:
    """Commits do projeto só com o e-mail anônimo do GitHub."""
    if not email.strip().endswith(ANONYMOUS_SUFFIX):
        return f"o git deste repositório está com o e-mail '{email.strip()}', não o anônimo (*{ANONYMOUS_SUFFIX})"
    return None


def older_generation(published: Optional[dict], updated: dict) -> List[str]:
    """Barra publicar por cima uma geração feita de um extrato do OpenStreetMap mais antigo que o publicado."""
    if published and updated["osm"]["timestamp"] < published["osm"]["timestamp"]:
        return [f"a geração {updated['build_id']} usa o OpenStreetMap de {updated['osm']['timestamp']}, "
                f"mais antigo que o da publicada ({published['build_id']}, {published['osm']['timestamp']})"]
    return []


START_MARKER = "<!-- downloads:start -->"
END_MARKER = "<!-- downloads:end -->"


def _strip_accents(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _mb(n_bytes: int) -> str:
    return f"{n_bytes / 1e6:.1f}".replace(".", ",") + " MB"


def downloads_table(cat: dict) -> str:
    """A tabela "Baixar por estado" do README: a base primeiro, depois as UFs pelo nome, cada uma com o link direto
    para o arquivo na release desta geração."""
    url = cat["release_url"]
    lines = [
        f"Geração **{cat['build_id']}**: OpenStreetMap de {cat['osm']['timestamp'][:10]}, Valhalla "
        f"{cat['valhalla_version']}. Baixe sempre a base e os estados por onde vai rodar; confira o sha256 pelo "
        f"[`catalogo.json`](catalogo.json).",
        "",
        "| Pacote | Arquivo | Tamanho |",
        "|---|---|---:|",
        f"| **Base** (rodovias do Brasil, sempre necessária) | [{cat['base']['file']}]({url}{cat['base']['file']}) "
        f"| {_mb(cat['base']['bytes'])} |",
    ]
    for state, e in sorted(cat["states"].items(), key=lambda item: _strip_accents(item[1]["name"])):
        lines.append(f"| {e['name']} ({state}) | [{e['file']}]({url}{e['file']}) | {_mb(e['bytes'])} |")
    return "\n".join(lines)


def update_readme(text: str, table: str) -> str:
    """Troca só o que está entre os marcadores de downloads do README."""
    start, end = text.find(START_MARKER), text.find(END_MARKER)
    if start < 0 or end < start:
        raise ValueError(f"o README não tem os marcadores {START_MARKER} e {END_MARKER}")
    return text[:start + len(START_MARKER)] + "\n" + table + "\n" + text[end:]


def flags_problem(release: bool, commit: bool, push: bool, prune: bool = False,
                  dry_run: bool = False) -> Optional[str]:
    if prune and (release or commit or push):
        return "--prune roda sozinho: a publicação (--release --commit --push) já faz a limpeza no fim"
    if dry_run and not prune:
        return "--dry-run só faz sentido com --prune"
    if commit and not release:
        return "--commit só faz sentido com --release (o catálogo só muda depois que a release sobe)"
    if push and not commit:
        return "--push só faz sentido com --commit"
    return None


# Queda máxima de tiles de um pacote em relação ao catálogo publicado. Uma fonte quebrada (extrato truncado, malha
# errada) aparece assim; o OSM de um mês para o outro varia bem menos.
MAX_DROP = 0.05
# Com o extrato de outra fonte que o da publicada (openstreetmap.fr <-> Geofabrik), a folga é maior: os recortes diferem
# na fronteira. Em 2026-11-08, a Geofabrik deu 11 tiles a menos no AC (172 -> 161, -6,4%), só de vias do lado peruano e
# boliviano dentro da margem de 5 km; RS, AM, MS e RO perderam menos de 1%. Um extrato quebrado perde muito mais.
MAX_DROP_SOURCE_CHANGE = 0.10


def _extract_host(cat: Optional[dict]) -> Optional[str]:
    url = ((cat or {}).get("osm") or {}).get("url")
    return urllib.parse.urlparse(url).netloc or None if isinstance(url, str) else None


def allowed_drop(published: Optional[dict], updated: dict) -> float:
    """A queda tolerada: MAX_DROP_SOURCE_CHANGE quando o extrato vem de outra fonte que o da publicada (o servidor da
    URL; outro arquivo datado da mesma fonte é a mesma fonte), MAX_DROP nos demais casos, inclusive sem a URL."""
    before, after = _extract_host(published), _extract_host(updated)
    return MAX_DROP_SOURCE_CHANGE if before and after and before != after else MAX_DROP


def package_files(cat: dict) -> List[str]:
    """Os pacotes que sobem na release: o `file` de cada um (hoje o .tar.zst) e, numa geração que trazia os dois
    formatos (a chave `zstd`, só em 2026-10-05), também o .tar.zst ao lado do .tar.gz."""
    out = []
    for e in entries(cat).values():
        out.append(e["file"])
        if e.get("zstd"):
            out.append(e["zstd"]["file"])
    return out


LICENSE_FILE = "LICENCA-DADOS.txt"


def license_text(cat: dict) -> str:
    """A licença e a atribuição de uma geração, para ir na release ao lado dos pacotes: quem baixa só os arquivos
    recebe o aviso. O texto da ODbL vai inteiro (o LICENSE da raiz do repositório)."""
    odbl = (config.REPO / "LICENSE").read_text(encoding="utf-8").strip()
    osm = cat["osm"]
    return (f"Andorinha, geração {cat['build_id']}: tiles de roteamento do Valhalla {cat['valhalla_version']} para o "
            f"Brasil.\n\n"
            f"{config.ATTRIBUTION}\n\n"
            "Os pacotes desta release são uma base de dados derivada do OpenStreetMap, distribuída sob a Open "
            "Database License (ODbL) 1.0, cujo texto inteiro segue abaixo. Cite \"© colaboradores do "
            "OpenStreetMap\" (https://www.openstreetmap.org/copyright). Quem publicar uma base derivada destes "
            "pacotes precisa mantê-la sob a ODbL.\n\n"
            f"Dados de origem:\n"
            f"- OpenStreetMap, extrato de {osm['timestamp']}: {osm['url']}\n"
            f"- Limites das UFs (que pacote leva cada tile): malha de UFs do IBGE, "
            f"https://servicodados.ibge.gov.br/api/docs/malhas\n\n"
            f"Gerador (código sob MIT): https://github.com/{config.GITHUB_REPO}\n\n"
            f"{'=' * 78}\n\n{odbl}\n")


def release_files(cat: dict, dist: Path) -> List[Path]:
    """Os arquivos da release, na ordem de subida: os pacotes, o LICENCA-DADOS.txt (gravado em `dist`) e, por último,
    o catalogo.json da geração (o de `dist`, o mesmo que vai para a raiz do repositório). Os dois ficam fora dos
    pacotes: quem lê os .tar.zst recusa um caminho que não seja de tile. Levanta FileNotFoundError sem o catálogo."""
    catalog_path = dist / CATALOG_FILE
    if not catalog_path.is_file():
        raise FileNotFoundError(f"falta {catalog_path}: o catálogo da geração sobe na release")
    license_path = dist / LICENSE_FILE
    license_path.write_text(license_text(cat), encoding="utf-8", newline="\n")
    return [dist / name for name in package_files(cat)] + [license_path, catalog_path]


def entries(cat: dict) -> dict:
    """{'base': {...}, 'GO': {...}, ...}: todos os pacotes do catálogo."""
    return {"base": cat["base"], **cat["states"]}


def drops(published: dict, updated: dict, max_drop: float = MAX_DROP) -> List[str]:
    """Pacotes do catálogo publicado que sumiram do novo ou perderam mais que max_queda dos tiles."""
    out = []
    updated_entries = entries(updated)
    for name, old in sorted(entries(published).items()):
        current = updated_entries.get(name)
        if current is None:
            out.append(f"{name}: sumiu do catálogo")
        elif current["tiles"] < old["tiles"] * (1 - max_drop):
            out.append(f"{name}: {old['tiles']} -> {current['tiles']} tiles")
    return out


def check(cat: dict, published: Optional[dict], accept_drop: bool, accept_older: bool = False) -> List[str]:
    """Tudo o que impede publicar a geração `cat` (vazio = pode publicar)."""
    problems = []
    if (config.DIST / config.IN_PROGRESS_MARKER).exists():
        problems.append("a geração ainda roda ou caiu no meio: rode python generate.py de novo")
    if cat["valhalla_version"] != config.VALHALLA_VERSION:
        problems.append(f"catálogo gerado com o Valhalla {cat['valhalla_version']}, "
                        f"gerador em {config.VALHALLA_VERSION}")
    for name, e in entries(cat).items():
        for item in [e] + ([e["zstd"]] if e.get("zstd") else []):
            file = config.DIST / item["file"]
            if not file.exists():
                problems.append(f"{name}: falta {item['file']}")
            elif file.stat().st_size >= config.MAX_ASSET_BYTES:
                problems.append(f"{name}: {file.stat().st_size / 1e9:.2f} GB, acima do limite do GitHub Releases")
            elif file_hash(file) != item["sha256"]:
                problems.append(f"{name}: sha256 de {item['file']} não bate com o catálogo")
    if published and not accept_drop:
        problems += drops(published, cat, allowed_drop(published, cat))
    if not accept_older:
        problems += older_generation(published, cat)
    return problems
