"""O começo de um .osm.pbf de mentira: só o bloco OSMHeader, montado à mão em protobuf, com o horário dos dados.
Serve aos testes do cabeçalho e aos de download da Geofabrik (o conteúdo depois do cabeçalho é qualquer coisa)."""
from __future__ import annotations

import zlib
from datetime import datetime, timezone


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def field_varint(number: int, value: int) -> bytes:
    return varint(number << 3) + varint(value)


def field_bytes(number: int, value: bytes) -> bytes:
    return varint(number << 3 | 2) + varint(len(value)) + value


def epoch(timestamp: str) -> int:
    """`2026-11-07T21:21:36Z` -> segundos desde 1970."""
    return int(datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def header(timestamp: str = "2026-11-07T21:21:36Z", compressed: bool = False, with_timestamp: bool = True) -> bytes:
    """Os bytes do começo do arquivo: tamanho do BlobHeader, BlobHeader e Blob com o HeaderBlock."""
    block = field_bytes(4, b"OsmSchema-V0.6") + field_bytes(16, b"teste")
    if with_timestamp:
        block += field_varint(32, epoch(timestamp)) + field_varint(33, 4967)
    if compressed:
        blob = field_varint(2, len(block)) + field_bytes(3, zlib.compress(block))
    else:
        blob = field_bytes(1, block)
    blob_header = field_bytes(1, b"OSMHeader") + field_varint(3, len(blob))
    return len(blob_header).to_bytes(4, "big") + blob_header + blob


def pbf(timestamp: str = "2026-11-07T21:21:36Z", size: int = 60_000, **kwargs: object) -> bytes:
    """Um 'arquivo' inteiro: o cabeçalho e enchimento até `size` bytes."""
    head = header(timestamp, **kwargs)  # type: ignore[arg-type]
    return head + b"x" * (size - len(head))
