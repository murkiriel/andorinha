"""Downloads que aguentam rede ruim: retomada de onde parou, retentativa só do que é passageiro.

- O arquivo vai para `<destino>.parcial` e só vira `<destino>` completo: nunca sobra um arquivo pela metade com o nome
  final. O tamanho recebido é conferido contra o Content-Length; conexão que cai antes do fim conta como falha.
- Numa nova tentativa, o `.parcial` é retomado com `Range: bytes=<tamanho>-`. Servidor que ignora o Range (responde
  200) faz recomeçar do zero.
- `identity` (ex.: o md5 publicado do arquivo) fica gravada ao lado do `.parcial`: uma sobra de OUTRA versão do
  arquivo é descartada em vez de emendada com a nova.
- Corpo comprimido (Content-Encoding gzip ou deflate) é descomprimido na hora de gravar. O urllib não pede
  compressão, mas há servidor que manda assim mesmo (o IBGE, para os executores do GitHub Actions).
- Erro permanente (4xx, exceto 408 e 429) falha na hora, sem esperar. Os demais (5xx, conexão, tempo esgotado) são
  retentados com espera crescente.
"""
from __future__ import annotations

import http.client
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Callable, Optional

from . import config
from .util import CHUNK

RETRYABLE_4XX = {408, 429}


class DownloadError(RuntimeError):
    """Todas as tentativas falharam."""


class PermanentError(DownloadError):
    """O servidor respondeu com um erro que não passa tentando de novo (ex.: 404)."""


def _open(url: str, timeout: float, headers: Optional[dict] = None):
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


def _decoder(url: str, r):
    """Descompressor do corpo, ou None se ele veio sem compressão."""
    encoding = (r.headers.get("Content-Encoding") or "identity").strip().lower()
    if encoding == "identity":
        return None
    if encoding in ("gzip", "x-gzip"):
        return zlib.decompressobj(16 + zlib.MAX_WBITS)
    if encoding == "deflate":
        return zlib.decompressobj()
    raise PermanentError(f"{url}: Content-Encoding {encoding} não suportado")


def _is_permanent(e: urllib.error.HTTPError) -> bool:
    return 400 <= e.code < 500 and e.code not in RETRYABLE_4XX


def _origin_path(partial: Path) -> Path:
    return partial.with_name(partial.name + ".origem")


def mark_origin(partial: Path, identity: str) -> None:
    """Grava de qual versão do arquivo o `.parcial` é (usado também pelos testes)."""
    _origin_path(partial).write_text(identity, encoding="utf-8")


def fetch_text(url: str, attempts: int = 3, delay: float = 5, timeout: float = 60) -> str:
    """O corpo da resposta como texto UTF-8."""
    for attempt in range(1, attempts + 1):
        try:
            with _open(url, timeout=timeout) as r:
                body = r.read()
                decoder = _decoder(url, r)
            if decoder is not None:
                body = decoder.decompress(body) + decoder.flush()
            return body.decode("utf-8")
        except urllib.error.HTTPError as e:
            if _is_permanent(e):
                raise PermanentError(f"{url}: HTTP {e.code}") from e
            error: Exception = e
        except (OSError, http.client.HTTPException) as e:
            error = e
        if attempt < attempts:
            time.sleep(delay * attempt)
    raise DownloadError(f"{url}: {attempts} tentativas falharam ({error})")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None  # o urllib devolve o 3xx como HTTPError, com o Location nos cabeçalhos


def _next_hop(opener: urllib.request.OpenerDirector, url: str, attempts: int, delay: float) -> Optional[str]:
    """O Location (absoluto) de `url`, ou None se ela responde sem redirecionar. Erro permanente (4xx) falha na hora;
    o resto (5xx, conexão, tempo esgotado) é retentado com espera crescente."""
    error: Exception = DownloadError("nenhuma tentativa")
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
        try:
            with opener.open(req, timeout=60):
                return None  # 2xx: fecha sem ler o corpo
        except urllib.error.HTTPError as e:
            location = e.headers.get("Location") if 300 <= e.code < 400 else None
            if location:
                return urllib.parse.urljoin(url, location)
            if 300 <= e.code < 400 or _is_permanent(e):
                raise PermanentError(f"{url}: HTTP {e.code}") from e
            error = e
        except (OSError, http.client.HTTPException) as e:
            error = e
        if attempt < attempts:
            time.sleep(delay * attempt)
    raise DownloadError(f"{url}: {attempts} tentativas falharam ({error})")


def redirect_target(url: str, attempts: int = 3, delay: float = 5, max_hops: int = 3,
                    stop: Optional[Callable[[str], bool]] = None) -> str:
    """Onde termina a corrente de redirecionamentos de `url` (o último endereço, absoluto). Cada salto é um pedido
    à parte, lido sem seguir o redirecionamento e sem baixar corpo. Uma resposta sem redirecionar logo no primeiro salto é erro permanente: quem chama conta com o
    redirecionamento. Mais de `max_hops` saltos também é erro (corrente sem fim: em 2026-09, numa rede doméstica, o
    `-latest` da Geofabrik entrou em laço).

    `stop`: se devolve True para um endereço da corrente, ele é o fim e nem é pedido. A Geofabrik precisa disso: o
    `-latest` dá dois saltos (301 e 307) até o arquivo datado, e pedir o datado do dia, que às vezes não responde, faria
    falhar a leitura de um endereço que já se sabe certo."""
    opener = urllib.request.build_opener(_NoRedirect)
    current = url
    for hop in range(max_hops + 1):
        target = _next_hop(opener, current, attempts, delay)
        if target is None:
            if hop == 0:
                raise PermanentError(f"{url}: respondeu sem redirecionar")
            return current
        if hop == max_hops:
            raise PermanentError(f"{url}: mais de {max_hops} redirecionamentos (parou em {target})")
        current = target
        if stop is not None and stop(current):
            return current
    raise AssertionError("inalcançável")  # o laço sempre retorna ou levanta


def download(url: str, dest: Path, identity: Optional[str] = None, attempts: int = 5, delay: float = 10,
             timeout: float = 120) -> None:
    """Baixa `url` para `dest`, retomando de onde parou entre tentativas."""
    partial = dest.with_name(dest.name + ".parcial")
    origin = _origin_path(partial)
    if partial.exists() and (identity is None or not origin.exists()
                             or origin.read_text(encoding="utf-8") != identity):
        partial.unlink()  # sobra de outra versão (ou de origem desconhecida): não dá para emendar
    if identity is not None:
        mark_origin(partial, identity)

    error: Exception = DownloadError("nenhuma tentativa")
    for attempt in range(1, attempts + 1):
        start = partial.stat().st_size if partial.exists() else 0
        headers = {"Range": f"bytes={start}-"} if start else {}
        try:
            with _open(url, timeout, headers) as r:
                decoder = _decoder(url, r)
                if decoder is not None and r.status == 206:
                    # O Range vale sobre os bytes comprimidos; o .parcial guarda os descomprimidos. Recomeça sem Range.
                    partial.unlink()
                    raise DownloadError("resposta parcial comprimida")
                if start and r.status == 206:
                    mode, already_had = "ab", start
                else:  # 200: começa do zero (inclusive quando o servidor ignorou o Range)
                    mode, already_had = "wb", 0
                expected = r.headers.get("Content-Length")
                received = 0
                with open(partial, mode) as f:
                    while chunk := r.read(CHUNK):
                        f.write(decoder.decompress(chunk) if decoder is not None else chunk)
                        received += len(chunk)
                    if decoder is not None:
                        f.write(decoder.flush())
                if expected is not None and received != int(expected):
                    raise DownloadError(f"conexão caiu: {already_had + received} bytes de "
                                        f"{already_had + int(expected)}")
                if decoder is not None and not decoder.eof:
                    raise DownloadError("conexão caiu: corpo comprimido incompleto")
            partial.replace(dest)
            if origin.exists():
                origin.unlink()
            return
        except PermanentError:
            raise
        except urllib.error.HTTPError as e:
            if e.code == 416:  # o .parcial já passou do tamanho do arquivo: recomeça
                partial.unlink(missing_ok=True)
                error = e
            elif _is_permanent(e):
                raise PermanentError(f"{url}: HTTP {e.code}") from e
            else:
                error = e
        except (OSError, http.client.HTTPException, DownloadError) as e:
            error = e
        if attempt < attempts:
            time.sleep(delay * attempt)
    raise DownloadError(f"{url}: {attempts} tentativas falharam ({error})")
