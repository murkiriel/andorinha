"""O horário dos dados de um extrato .osm.pbf, lido do cabeçalho do arquivo (só a stdlib, sem o pyosmium).

O arquivo começa com o bloco `OSMHeader`: 4 bytes (big-endian) com o tamanho do `BlobHeader`, o `BlobHeader`
(protobuf: `type`, `datasize`), e o `Blob` (protobuf: `raw` ou `zlib_data`), que guarda o `HeaderBlock`. Dele só
interessa o campo 32, `osmosis_replication_timestamp` (segundos desde 1970): até quando o arquivo tem dados. É o que o
catálogo grava como `osm.timestamp`, e não o horário em que alguém olhou o `state.txt` das atualizações da fonte.
"""
from __future__ import annotations

import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional, Tuple, Union

# O cabeçalho cabe em poucos KB; ler mais que isto de um arquivo de GB seria à toa.
READ_BYTES = 64 * 1024
MAX_HEADER_BLOCK = 4 * 1024 * 1024  # trava contra um arquivo que não é PBF e traz um tamanho absurdo

Value = Union[int, bytes]


class PbfHeaderError(ValueError):
    """O começo do arquivo não é um cabeçalho de .osm.pbf legível."""


def _varint(data: bytes, pos: int) -> Tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(data) or shift > 63:
            raise PbfHeaderError("número truncado ou grande demais")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def _fields(data: bytes) -> Iterator[Tuple[int, Value]]:
    """(número do campo, valor) de uma mensagem protobuf; só varint e bytes (os demais tipos não são usados aqui)."""
    pos = 0
    while pos < len(data):
        key, pos = _varint(data, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(data, pos)
            yield number, value
        elif wire == 2:
            size, pos = _varint(data, pos)
            if pos + size > len(data):
                raise PbfHeaderError("campo truncado")
            yield number, data[pos:pos + size]
            pos += size
        elif wire == 1:
            pos += 8
        elif wire == 5:
            pos += 4
        else:
            raise PbfHeaderError(f"tipo de campo {wire} desconhecido")


def replication_timestamp(head: bytes) -> int:
    """O `osmosis_replication_timestamp` (segundos desde 1970) do começo de um .osm.pbf. Levanta PbfHeaderError se o
    arquivo não é um PBF, o cabeçalho não cabe em `head` ou o campo não existe."""
    if len(head) < 4:
        raise PbfHeaderError("arquivo curto demais")
    header_size = int.from_bytes(head[:4], "big")
    if not 0 < header_size < READ_BYTES or 4 + header_size > len(head):
        raise PbfHeaderError("tamanho do BlobHeader inválido")
    kind, blob_size = None, 0
    for number, value in _fields(head[4:4 + header_size]):
        if number == 1 and isinstance(value, bytes):
            kind = value
        elif number == 3 and isinstance(value, int):
            blob_size = value
    if kind != b"OSMHeader":
        raise PbfHeaderError("não começa com o bloco OSMHeader")
    start = 4 + header_size
    if not 0 < blob_size < MAX_HEADER_BLOCK or start + blob_size > len(head):
        raise PbfHeaderError("o bloco do cabeçalho não cabe no começo do arquivo")
    block: Optional[bytes] = None
    for number, value in _fields(head[start:start + blob_size]):
        if number == 1 and isinstance(value, bytes):
            block = value
        elif number == 3 and isinstance(value, bytes):
            try:
                block = zlib.decompress(value)
            except zlib.error as e:
                raise PbfHeaderError(f"bloco comprimido inválido ({e})") from e
    if block is None:
        raise PbfHeaderError("bloco sem dados raw nem zlib")
    for number, value in _fields(block):
        if number == 32 and isinstance(value, int):
            return value
    raise PbfHeaderError("o cabeçalho não tem osmosis_replication_timestamp")


def format_timestamp(seconds: int) -> str:
    """No formato do catálogo (UTC, `AAAA-MM-DDTHH:MM:SSZ`)."""
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_timestamp(path: Path) -> str:
    """O horário dos dados do arquivo `path` no formato do catálogo. Levanta PbfHeaderError se não der para ler."""
    with open(path, "rb") as f:
        head = f.read(READ_BYTES)
    return format_timestamp(replication_timestamp(head))
