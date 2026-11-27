"""Drawing-only LAN transport. The chart API remains bound to loopback."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import select
import socket
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from . import drawing_store, vault

PORT = 48765
DISCOVER = b"KIS-DRAWINGS-1 DISCOVER"
OFFER = b"KIS-DRAWINGS-1 OFFER "
INVITE_SECONDS = 300
MAX_FRAME = 16 * 1024 * 1024
PRIVATE = tuple(map(ipaddress.ip_network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")))


def _private(address):
    try:
        ip = ipaddress.IPv4Address(address)
        return any(ip in subnet for subnet in PRIVATE)
    except ValueError:
        return False


def _local_address():
    addresses = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        try:
            # UDP connect selects the default interface without sending a packet.
            route.connect(("192.0.2.1", 9))
            addresses.append(route.getsockname()[0])
        except OSError:
            pass
    try:
        addresses.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return next((address for address in addresses if _private(address)), "")


def _json(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _send(sock, obj):
    raw = _json(obj)
    if len(raw) > MAX_FRAME:
        raise ValueError("동기화할 그림 데이터가 너무 큽니다.")
    sock.sendall(len(raw).to_bytes(4, "big") + raw)


def _read(sock, count):
    chunks = bytearray()
    while len(chunks) < count:
        chunk = sock.recv(min(65536, count - len(chunks)))
        if not chunk:
            raise OSError("연결이 종료되었습니다.")
        chunks.extend(chunk)
    return bytes(chunks)


def _recv(sock, limit=16384):
    n = int.from_bytes(_read(sock, 4), "big")
    if not 0 < n <= limit:
        raise ValueError("동기화 메시지 크기가 올바르지 않습니다.")
    value = json.loads(_read(sock, n))
    if not isinstance(value, dict):
        raise ValueError("동기화 메시지가 올바르지 않습니다.")
    return value


def _mac(code, purpose, fingerprint):
    return hmac.new(code.encode("ascii"), (purpose + fingerprint).encode("ascii"), "sha256").hexdigest()


def _matches(value, expected):
    return isinstance(value, str) and value.isascii() and hmac.compare_digest(value, expected)


def _save(path, value):
    if not vault.save(path, value):
        raise ValueError("기기 연결 정보를 암호화해 저장할 수 없습니다.")


def _host_identity(path):
    bundle = vault.load(path)
    if bundle is None:
        # Desktop only. Android uses its built-in SSL implementation as the TLS client.
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Drawing Sync")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3650))
                .sign(key, hashes.SHA256()))
        password = secrets.token_urlsafe(32)
        bundle = {
            "password": password,
            "key": key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.BestAvailableEncryption(password.encode())).decode(),
            "cert": cert.public_bytes(serialization.Encoding.PEM).decode(),
        }
        _save(path, bundle)

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # load_cert_chain requires a file. Only a passphrase-encrypted key ever touches it.
    with NamedTemporaryFile(dir=path.parent, delete=False) as f:
        pem_path = Path(f.name)
    try:
        pem_path.write_bytes((bundle["cert"] + bundle["key"]).encode())
        context.load_cert_chain(str(pem_path), password=bundle["password"])
    finally:
        pem_path.unlink(missing_ok=True)
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(bundle["cert"])).hexdigest()
    return context, fingerprint


def _fingerprints(snapshot):
    return {code: hashlib.sha256(_json(value)).hexdigest() for code, value in snapshot["symbols"].items()}


def _subset(snapshot, symbols):
    return {"version": 1, "symbols": {k: v for k, v in snapshot["symbols"].items() if k in symbols}}


class DrawingLan:
    def __init__(self, data_dir: Path, *, mobile: bool, wifi_available=None):
        self.dir = Path(data_dir)
        self.drawings = self.dir / "drawings"
        self.peer_file = self.dir / "drawing_peer.bin"
        self.mobile = mobile
        self.wifi = wifi_available or (lambda: True)
        self.lock = threading.RLock()
        self.peer = vault.load(self.peer_file)
        self.code = ""
        self.deadline = 0.0
        self.generation = 0
        self.last_sync = 0
        self.address = ""
        self.error = ""
        self.running = False
        self.identity = None
        self.worker = None
        self.end = threading.Event()
        self.wake = threading.Event()
        self.slots = threading.BoundedSemaphore(3)

    def start(self):
        with self.lock:
            if self.worker and self.worker.is_alive():
                return
            self.end.clear()
            self.worker = threading.Thread(target=self._run, name="drawing-sync", daemon=True)
            self.worker.start()

    def stop(self):
        self.end.set()
        self.wake.set()
        if self.worker:
            self.worker.join(timeout=7)

    def status(self):
        with self.lock:
            return {"mobile": self.mobile, "paired": bool(self.peer), "running": self.running,
                    "address": self.peer.get("address", "") if self.peer and self.mobile else self.address,
                    "port": PORT, "last_sync": self.last_sync, "generation": self.generation, "error": self.error}

    def invite(self):
        with self.lock:
            if self.mobile or self.peer:
                raise ValueError("먼저 기존 기기 연결을 해제하세요.")
            address = _local_address()
            if not address:
                raise ValueError("PC를 Wi-Fi 또는 유선 공유기에 연결하세요.")
            if not self.identity:
                self.identity = _host_identity(self.dir / "drawing_host.bin")
            self.address = address
            self.code = base64.b32encode(secrets.token_bytes(10)).decode()
            self.deadline = time.monotonic() + INVITE_SECONDS
            self.error = ""
            self.wake.set()
            return {"code": "-".join(self.code[i:i+4] for i in range(0, 16, 4)),
                    "address": address, "port": PORT, "expires_in": INVITE_SECONDS}

    def unlink(self):
        with self.lock:
            self.peer_file.unlink(missing_ok=True)
            self.peer = None
            self.code = ""
            self.last_sync = 0
            self.error = ""
            self.wake.set()
        return self.status()

    def pair(self, address, code):
        with self.lock:
            if not self.mobile or self.peer:
                raise ValueError("연결할 수 없는 상태입니다.")
        if not self.wifi():
            raise ValueError("휴대폰을 PC와 같은 Wi-Fi에 연결하세요.")
        code = code.replace("-", "").replace(" ", "").upper()
        if len(code) != 16 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in code):
            raise ValueError("PC에 표시된 16자리 연결 코드를 입력하세요.")
        address = address.strip()
        if address and not _private(address):
            raise ValueError("PC에 표시된 사설 IPv4 주소를 입력하세요.")
        addresses = [address] if address else self._discover()
        for host in addresses:
            try:
                with self._connect(host) as s:
                    pin = hashlib.sha256(s.getpeercert(binary_form=True)).hexdigest()
                    _send(s, {"op": "invite"})
                    if not _matches(_recv(s).get("proof"), _mac(code, "host:", pin)):
                        continue
                    token = secrets.token_hex(32)
                    proof = _mac(code, "peer:" + pin + ":", token)
                    with self.lock:
                        if self.peer:
                            raise ValueError("이미 다른 기기에 연결했습니다.")
                        # Persist after authenticating the PC and before consuming the invite.
                        # If the acknowledgement is lost the regular sync exchange finishes pairing.
                        self._save_peer({"pin": pin, "token": token, "address": host, "pending_proof": proof})
                        try:
                            _send(s, {"op": "pair", "token": token, "proof": proof})
                            if _recv(s).get("ok") is not True:
                                raise OSError("기기 등록 확인 대기 중")
                            self.error = ""
                        except OSError:
                            self.error = "PC의 기기 등록 확인 대기 중"
                        finally:
                            self.wake.set()
                        return self.status()
            except (OSError, ValueError):
                continue
        raise ValueError("연결하지 못했습니다. PC 주소, 연결 코드, 같은 Wi-Fi와 방화벽 설정을 확인하세요.")

    def _save_peer(self, peer):
        _save(self.peer_file, peer)
        self.peer = peer

    def _connect(self, host):
        if not _private(host):
            raise ValueError("사설 IPv4 주소만 연결할 수 있습니다.")
        # The peer certificate is authenticated by the out-of-band invite or the stored pin
        # before any private token or drawing is sent.
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        tcp = socket.create_connection((host, PORT), timeout=3)
        try:
            sock = ctx.wrap_socket(tcp)
            sock.settimeout(6)
            return sock
        except BaseException:
            tcp.close()
            raise

    def _discover(self, pin=""):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(DISCOVER, ("255.255.255.255", PORT))
            deadline = time.monotonic() + 0.7
            candidates = []
            while len(candidates) < 8 and time.monotonic() < deadline:
                sock.settimeout(max(0.01, deadline - time.monotonic()))
                try:
                    data, (address, _) = sock.recvfrom(128)
                except TimeoutError:
                    break
                if (_private(address) and data.startswith(OFFER)
                        and (not pin or data == OFFER + pin.encode()) and address not in candidates):
                    candidates.append(address)
            return candidates

    def _run(self):
        while not self.end.is_set():
            self.wake.clear()
            try:
                if self.mobile:
                    with self.lock:
                        peer = self.peer
                    if peer and self.wifi():
                        self._phone_cycle(peer)
                    elif peer:
                        self.error = "Wi-Fi 연결 대기 중"
                elif self._enabled():
                    self._listen()
            except (OSError, ValueError, RuntimeError, KeyError, RecursionError):
                self.error = ("PC 연결 대기 중" if self.mobile
                              else "그림 동기화 연결을 열지 못했습니다. 네트워크와 포트를 확인하세요.")
            self.wake.wait(5 if self.mobile else 1)

    def _enabled(self):
        with self.lock:
            return bool(self.peer or self.code and time.monotonic() < self.deadline)

    def _listen(self):
        address = _local_address()
        if not address:
            raise OSError("LAN 연결 대기 중")
        with self.lock:
            if not self.identity:
                self.identity = _host_identity(self.dir / "drawing_host.bin")
            self.address = address
        with socket.socket() as tcp, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            tcp.bind((address, PORT))
            tcp.listen(6)
            udp.bind(("", PORT))
            self.running = True
            self.error = ""
            next_check = time.monotonic() + 12
            try:
                while not self.end.is_set() and self._enabled():
                    if time.monotonic() >= next_check:
                        if _local_address() != address:
                            return
                        next_check = time.monotonic() + 12
                    for sock in select.select([tcp, udp], [], [], 0.4)[0]:
                        if sock is udp:
                            data, origin = udp.recvfrom(128)
                            if _private(origin[0]) and data == DISCOVER:
                                udp.sendto(OFFER + self.identity[1].encode(), origin)
                        else:
                            client, origin = tcp.accept()
                            if _private(origin[0]) and self.slots.acquire(blocking=False):
                                threading.Thread(target=self._serve, args=(client,), daemon=True).start()
                            else:
                                client.close()
            finally:
                self.running = False

    def _serve(self, client):
        try:
            with client:
                client.settimeout(5)
                with self.identity[0].wrap_socket(client, server_side=True) as sock:
                    request = _recv(sock)
                    if request.get("op") == "invite":
                        self._accept_pair(sock)
                    elif request.get("op") == "sync":
                        self._accept_sync(sock, request)
        except (OSError, ValueError, RecursionError):
            pass
        finally:
            self.slots.release()

    def _accept_pair(self, sock):
        with self.lock:
            code = self.code
            if self.peer or not code or time.monotonic() >= self.deadline:
                return
            pin = self.identity[1]
        _send(sock, {"proof": _mac(code, "host:", pin)})
        req = _recv(sock)
        with self.lock:
            if self.code != code or req.get("op") != "pair" or not self._claim_invite(req):
                return
            _send(sock, {"ok": True})

    def _claim_invite(self, req):
        token = req.get("token")
        if (self.peer or not self.code or time.monotonic() >= self.deadline
                or not isinstance(token, str) or len(token) != 64 or not token.isascii()):
            return False
        proof = _mac(self.code, "peer:" + self.identity[1] + ":", token)
        if not _matches(req.get("proof"), proof):
            return False
        self._save_peer({"token": token})
        self.code = ""
        return True

    def _accept_sync(self, sock, request):
        token = request.get("token")
        with self.lock:
            if not self.peer:
                self._claim_invite(request)
            if not self.peer or not _matches(token, self.peer["token"]):
                return
        _send(sock, {"ok": True})
        remote = _recv(sock, MAX_FRAME)
        local = drawing_store.export_sync(self.drawings)
        hashes = _fingerprints(local)
        different = {k for k in remote if remote[k] != hashes.get(k)}
        _send(sock, {"want": sorted(different)})
        incoming = _recv(sock, MAX_FRAME)
        with self.lock:
            if not self.peer or not _matches(token, self.peer["token"]):
                return
            changed = drawing_store.merge_sync(self.drawings, incoming)
            if changed:
                self.generation += 1
            latest = drawing_store.export_sync(self.drawings)
            delta = {k for k, v in _fingerprints(latest).items() if remote.get(k) != v}
            self.last_sync = int(time.time())
            self.error = ""
        _send(sock, _subset(latest, delta))

    def _phone_cycle(self, peer):
        try:
            self._exchange(peer, peer["address"])
            return
        except (OSError, ValueError):
            pass
        for host in self._discover(peer["pin"]):
            if host != peer["address"]:
                try:
                    self._exchange(peer, host)
                    return
                except (OSError, ValueError):
                    pass
        raise OSError("PC 연결 대기 중")

    def _exchange(self, peer, host):
        with self._connect(host) as sock:
            pin = hashlib.sha256(sock.getpeercert(binary_form=True)).hexdigest()
            if not _matches(peer["pin"], pin):
                raise ValueError("등록한 PC의 인증서가 다릅니다.")
            with self.lock:
                if self.peer != peer or self.end.is_set():
                    return
                _send(sock, {"op": "sync", "token": peer["token"], "proof": peer.get("pending_proof")})
            if _recv(sock).get("ok") is not True:
                raise ValueError("기기 인증 실패")
            snap = drawing_store.export_sync(self.drawings)
            _send(sock, _fingerprints(snap))
            want = _recv(sock, MAX_FRAME).get("want")
            if not isinstance(want, list) or not all(isinstance(k, str) for k in want):
                raise ValueError("그림 요청 형식 오류")
            with self.lock:
                if self.peer != peer or self.end.is_set():
                    return
                _send(sock, _subset(snap, set(want)))
            received = _recv(sock, MAX_FRAME)
            with self.lock:
                if self.peer != peer or self.end.is_set():
                    return
                if drawing_store.merge_sync(self.drawings, received):
                    self.generation += 1
                if peer["address"] != host or "pending_proof" in peer:
                    self._save_peer({"pin": pin, "token": peer["token"], "address": host})
                self.last_sync = int(time.time())
                self.error = ""
