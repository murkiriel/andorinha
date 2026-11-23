"""andorinha/pbf_header.py: o horário dos dados lido do cabeçalho de um .osm.pbf, com cabeçalhos montados à mão."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from andorinha import pbf_header  # noqa: E402
from tests import fake_pbf  # noqa: E402


class ReplicationTimestampTest(unittest.TestCase):
    def test_raw_block(self):
        head = fake_pbf.header("2026-11-21T20:55:03Z")
        self.assertEqual(fake_pbf.epoch("2026-11-21T20:55:03Z"), pbf_header.replication_timestamp(head))

    def test_zlib_block(self):
        head = fake_pbf.header("2026-11-21T20:55:03Z", compressed=True)
        self.assertEqual(fake_pbf.epoch("2026-11-21T20:55:03Z"), pbf_header.replication_timestamp(head))

    def test_header_inside_the_first_64_kb_of_a_big_file(self):
        data = fake_pbf.pbf("2026-11-21T20:55:03Z", size=200_000)
        self.assertEqual(fake_pbf.epoch("2026-11-21T20:55:03Z"),
                         pbf_header.replication_timestamp(data[:pbf_header.READ_BYTES]))

    def test_format(self):
        self.assertEqual("2026-11-21T20:55:03Z", pbf_header.format_timestamp(fake_pbf.epoch("2026-11-21T20:55:03Z")))

    def test_header_without_the_timestamp_field(self):
        with self.assertRaises(pbf_header.PbfHeaderError) as ctx:
            pbf_header.replication_timestamp(fake_pbf.header(with_timestamp=False))
        self.assertIn("osmosis_replication_timestamp", str(ctx.exception))

    def test_not_a_pbf(self):
        for junk in (b"", b"abc", b"<html>erro</html>" * 100, b"\x00" * 100, b"\xff" * 100):
            with self.assertRaises(pbf_header.PbfHeaderError, msg=junk[:10]):
                pbf_header.replication_timestamp(junk)

    def test_truncated_header(self):
        head = fake_pbf.header()
        for cut in (3, 10, len(head) - 1):
            with self.assertRaises(pbf_header.PbfHeaderError, msg=cut):
                pbf_header.replication_timestamp(head[:cut])

    def test_corrupted_zlib_block(self):
        head = bytearray(fake_pbf.header(compressed=True))
        head[-5:] = b"\x00\x01\x02\x03\x04"
        with self.assertRaises(pbf_header.PbfHeaderError):
            pbf_header.replication_timestamp(bytes(head))

    def test_other_block_type(self):
        head = fake_pbf.header().replace(b"OSMHeader", b"OSMDataxxx")
        with self.assertRaises(pbf_header.PbfHeaderError):
            pbf_header.replication_timestamp(head)


class FileTimestampTest(unittest.TestCase):
    def test_reads_the_start_of_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.osm.pbf"
            path.write_bytes(fake_pbf.pbf("2026-11-21T20:55:03Z", size=300_000))
            self.assertEqual("2026-11-21T20:55:03Z", pbf_header.file_timestamp(path))


if __name__ == "__main__":
    unittest.main()
