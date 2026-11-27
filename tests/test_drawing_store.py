import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from server.drawing_store import read_drawings, write_drawings


class DrawingStoreTests(unittest.TestCase):
    def test_existing_file_and_idempotent_ack(self):
        with TemporaryDirectory() as temp:
            target = Path(temp) / "A.json"
            original = {"code": "A", "drawings": [{"id": "old"}]}
            target.write_text(json.dumps(original), encoding="utf-8")
            prior = read_drawings(target, "A")
            current, ok = write_drawings(target, "A", [{"id": "new"}], prior["revision"])
            self.assertTrue(ok)
            self.assertNotEqual(current["revision"], prior["revision"])
            self.assertEqual(write_drawings(target, "A", [{"id": "new"}], prior["revision"]), (current, True))
            self.assertEqual(write_drawings(target, "A", [], prior["revision"]), (current, False))
            # Older applications used unconditional PUT.
            self.assertTrue(write_drawings(target, "A", [], None)[1])

    def test_concurrent_versions_and_unreadable_file(self):
        with TemporaryDirectory() as temp:
            target = Path(temp) / "A.json"
            start = Barrier(2)

            def writer(drawing):
                start.wait()
                return write_drawings(target, "A", [{"id": drawing}], "0")[1]

            with ThreadPoolExecutor(2) as pool:
                self.assertEqual(sorted(pool.map(writer, ["first", "second"])), [False, True])
            target.write_text("incomplete legacy file", encoding="utf-8")
            with self.assertRaises(ValueError):
                write_drawings(target, "A", [], None)
            self.assertEqual(target.read_text(encoding="utf-8"), "incomplete legacy file")


if __name__ == "__main__":
    unittest.main()
