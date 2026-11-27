import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from server.drawing_store import export_sync, merge_sync, read_drawings, write_drawings


class DrawingSyncTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.desktop = Path(temp.name) / "desktop"
        self.phone = Path(temp.name) / "phone"

    def read(self, folder):
        return read_drawings(folder / "NAS_AAPL.json", "NAS:AAPL")["drawings"]

    def put(self, folder, drawings):
        return write_drawings(folder / "NAS_AAPL.json", "NAS:AAPL", drawings, None)[0]

    def sync(self):
        merge_sync(self.desktop, export_sync(self.phone))
        merge_sync(self.phone, export_sync(self.desktop))
        self.assertEqual(export_sync(self.desktop), export_sync(self.phone))
        self.assertFalse(merge_sync(self.desktop, export_sync(self.phone)))

    def test_offline_additions_updates_deletion_and_replay(self):
        a, b = {"id": "trend", "price": 20}, {"id": "hline", "price": 30}
        self.put(self.desktop, [a, b])
        self.sync()
        old = export_sync(self.phone)
        self.put(self.phone, [{**a, "price": 50}, b, {"id": "mobile"}])
        self.put(self.desktop, [a, {"id": "pc"}])
        self.sync()
        self.assertEqual({d["id"] for d in self.read(self.phone)}, {"trend", "mobile", "pc"})
        self.assertFalse(merge_sync(self.desktop, old))
        self.assertIn({**a, "price": 50}, self.read(self.desktop))
        self.put(self.phone, [])
        self.sync()
        self.assertFalse(merge_sync(self.desktop, old))
        self.assertEqual(self.read(self.desktop), [])

    def test_conflicting_changes_preserve_editable_versions(self):
        self.put(self.desktop, [{"id": "trend", "price": 1}])
        self.sync()
        self.put(self.desktop, [{"id": "trend", "price": 2}])
        self.put(self.phone, [{"id": "trend", "price": 3}])
        self.sync()
        drawings = self.read(self.phone)
        self.assertEqual({d["price"] for d in drawings}, {2, 3})
        self.assertEqual(len({d["id"] for d in drawings}), 2)
        keep = next(d for d in drawings if d["id"] != "trend")
        self.put(self.phone, [keep])
        self.sync()
        self.assertEqual(self.read(self.desktop), [keep])

    def test_delete_and_concurrent_edit_preserves_edit(self):
        self.put(self.desktop, [{"id": "trend", "price": 1}])
        self.sync()
        self.put(self.phone, [{"id": "trend", "price": 2}])
        self.put(self.desktop, [])
        self.sync()
        self.assertEqual(self.read(self.desktop), [{"id": "trend", "price": 2}])

    def test_shared_legacy_file_and_missing_ids(self):
        original = {"code": "NAS:AAPL", "drawings": [{"type": "trend"}, {"id": "old"}, {"id": "old"}]}
        for folder in (self.phone, self.desktop):
            folder.mkdir()
            (folder / "NAS_AAPL.json").write_text(json.dumps(original), encoding="utf-8")
        prior = self.read(self.desktop)
        self.assertEqual(len({d["id"] for d in prior}), 3)
        self.sync()
        self.assertEqual(prior, self.read(self.phone))
        self.put(self.phone, [*prior, {"id": "new"}])
        self.sync()
        self.assertEqual(self.read(self.desktop), [*prior, {"id": "new"}])

    def test_invalid_packets_do_not_mutate_files(self):
        self.put(self.desktop, [{"id": "trend"}])
        state = export_sync(self.desktop)
        for malformed in ({"../secret": {}}, {"NAS:AAPL": {"clock": {}, "entries": [["x", 1, 0, {}]]}}):
            with self.assertRaises(ValueError):
                merge_sync(self.desktop, {"version": 1, "symbols": malformed})
            self.assertEqual(export_sync(self.desktop), state)


if __name__ == "__main__":
    unittest.main()
