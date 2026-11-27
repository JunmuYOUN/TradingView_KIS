"""Loopback-only transport integration, using an in-memory drawing store and credential vault."""
import contextlib
import copy
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from server import drawing_lan as lan
from server.drawing_store import export_sync, merge_sync, read_drawings, write_drawings


class LanPairTests(unittest.TestCase):
    def setUp(self):
        import importlib.util
        if not importlib.util.find_spec("cryptography"):
            self.skipTest("desktop certificate generation requires cryptography")
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.path = Path(tmp)
        self.secrets = {}
        self.documents = {}
        self.stack.enter_context(patch.object(lan.vault, "load", lambda path: self.secrets.get(path)))
        self.stack.enter_context(patch.object(lan.vault, "save", self.save))
        self.stack.enter_context(patch.object(lan.drawing_store, "export_sync", self.snapshot, create=True))
        self.stack.enter_context(patch.object(lan.drawing_store, "merge_sync", self.merge, create=True))
        self.stack.enter_context(patch.object(lan, "_local_address", lambda: "127.0.0.1"))
        self.stack.enter_context(patch.object(lan, "_private", lambda ip: ip == "127.0.0.1"))
        # The listener needs the same port number in *both* TCP and UDP namespaces.
        # A free TCP ephemeral port can already belong to another UDP application.
        for _ in range(32):
            with socket.socket() as tcp, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                tcp.bind(("127.0.0.1", 0))
                port = tcp.getsockname()[1]
                try:
                    udp.bind(("", port))
                except OSError:
                    continue
                self.stack.enter_context(patch.object(lan, "PORT", port))
                break
        else:
            self.fail("could not reserve a loopback TCP and discovery UDP port")
        self.pc = lan.DrawingLan(self.path / "pc", mobile=False)
        self.phone = lan.DrawingLan(self.path / "phone", mobile=True)
        self.pc.start()
        self.addCleanup(self.pc.stop)
        self.invite = self.pc.invite()
        deadline = time.monotonic() + 4
        while not self.pc.running and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(self.pc.running, self.pc.status()["error"])

    def save(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.secrets[path] = value.copy()
        return True

    def snapshot(self, directory):
        return {"version": 1, "symbols": copy.deepcopy(self.documents.get(directory, {}))}

    def merge(self, directory, payload):
        before = self.snapshot(directory)
        self.documents.setdefault(directory, {}).update(payload["symbols"])
        return before != self.snapshot(directory)

    def test_pair_offline_changes_restart_and_sync(self):
        self.phone.pair(self.invite["address"], self.invite["code"])
        self.assertTrue(self.pc.status()["paired"])
        self.assertTrue(self.phone.status()["paired"])
        phone = lan.DrawingLan(self.path / "phone", mobile=True)
        self.documents[self.pc.drawings] = {"AAPL": {"line": "desktop"}}
        self.documents[phone.drawings] = {"005930": {"line": "mobile"}}
        phone._phone_cycle(phone.peer)
        self.assertEqual(self.snapshot(phone.drawings), self.snapshot(self.pc.drawings))
        self.assertEqual(len(self.snapshot(phone.drawings)["symbols"]), 2)
        self.assertEqual(phone.status()["generation"], 1)
        self.assertEqual(self.pc.status()["generation"], 1)
        phone._phone_cycle(phone.peer)
        self.assertEqual(phone.status()["generation"], 1)
        self.assertEqual(self.pc.status()["generation"], 1)

    def test_wrong_code_and_untrusted_pc(self):
        with self.assertRaises(ValueError):
            self.phone.pair(self.invite["address"], "A" * 16)
        self.assertFalse(self.pc.status()["paired"])
        self.phone.pair(self.invite["address"], self.invite["code"])
        stale = dict(self.phone.peer, pin="00" * 32)
        with self.assertRaises(ValueError):
            self.phone._exchange(stale, self.invite["address"])
        self.assertEqual(self.pc.status()["last_sync"], 0)
        self.pc.unlink()
        with self.assertRaises(OSError):
            self.phone._exchange(self.phone.peer, self.invite["address"])

    def test_lost_pair_ack_recovers_and_merges_real_offline_files(self):
        def drop_ack(sock, obj):
            if obj == {"ok": True}:
                raise OSError("dropped pair acknowledgement")
            send(sock, obj)

        send = lan._send
        with patch.object(lan, "_send", drop_ack):
            self.phone.pair(self.invite["address"], self.invite["code"])
        self.assertTrue(self.phone.peer)
        self.assertTrue(self.pc.peer)
        code = "005930"
        pc_file = self.pc.drawings / f"{code}.json"
        phone_file = self.phone.drawings / f"{code}.json"
        with (patch.object(lan.drawing_store, "export_sync", export_sync),
              patch.object(lan.drawing_store, "merge_sync", merge_sync)):
            write_drawings(pc_file, code, [{"id": "pc"}], None)
            write_drawings(phone_file, code, [{"id": "phone"}], None)
            self.phone._phone_cycle(self.phone.peer)
            for file in (pc_file, phone_file):
                self.assertEqual({d["id"] for d in read_drawings(file, code)["drawings"]}, {"pc", "phone"})
            # The phone went offline and removed its own line before the next exchange.
            write_drawings(phone_file, code, [{"id": "pc"}], None)
            self.phone._phone_cycle(self.phone.peer)
            self.assertEqual(read_drawings(pc_file, code)["drawings"], [{"id": "pc"}])

    def test_unpaired_no_wire_and_wifi_required(self):
        self.phone.wifi = lambda: False
        with self.assertRaisesRegex(ValueError, "Wi-Fi"):
            self.phone.pair(self.invite["address"], self.invite["code"])
        self.assertFalse(self.pc.status()["paired"])


if __name__ == "__main__":
    unittest.main()
