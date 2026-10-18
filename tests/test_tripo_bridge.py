# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: scripts/tripo_bridge.py, driven by a fake Tripo Studio page against a stub workbench.
#
#   python -m unittest tests.test_tripo_bridge -v
#
# Nothing here talks to Tripo or to a real workbench: the "browser" is a raw socket on the loopback speaking the DCC
# Bridge protocol as Studio's page does (docs/GENERATE.md), and the workbench is a stub HTTP server that records what
# it is sent.
import base64, contextlib, io, json, os, shutil, socket, struct, sys, tempfile, threading, unittest, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from scripts import tripo_bridge  # noqa: E402

TOKEN = "bridge-test-token-SECRET"
STUDIO = "https://studio.tripo3d.ai"


class StubWorkbench(BaseHTTPRequestHandler):
    calls = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        StubWorkbench.calls.append((self.path, dict(self.headers), body))
        if self.headers.get("Authorization") != "Bearer " + TOKEN:
            return self.reply(401, {"error": "missing or wrong token"})
        if self.path.startswith("/api/v1/models"):
            name = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)["name"][0]
            return self.reply(201, {"model": {"name": name, "group": ""}, "existing": False})
        if self.path == "/api/v1/pipeline":
            return self.reply(202, {"id": 7, "state": "queued"})
        self.reply(404, {"error": "no route"})

    def reply(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class FakeStudio:
    """A browser tab on Tripo Studio, behaving as Studio's page does (protocol 1.1.0): it connects to
    ws://localhost:<port>, sends every message as a binary frame (a Blob), and sends each chunk only after the
    previous one's ack."""

    def __init__(self, port, origin=STUDIO, host=None, addr="127.0.0.1"):
        self.sock = socket.create_connection((addr, port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        lines = ["GET / HTTP/1.1", "Host: %s" % (host or "localhost:%d" % port), "Upgrade: websocket",
                 "Connection: Upgrade", "Sec-WebSocket-Key: " + key, "Sec-WebSocket-Version: 13"]
        if origin is not None: lines.append("Origin: " + origin)
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            more = self.sock.recv(4096)
            if not more: break
            buf += more
        head, _, self.buf = buf.partition(b"\r\n\r\n")   # the greeting may arrive in the same read as the 101
        self.response = head.decode("latin-1")
        self.status = int(self.response.split(" ", 2)[1]) if self.response else 0

    def send(self, opcode, data, fin=True):
        mask = os.urandom(4)
        n = len(data)
        head = bytes([(0x80 if fin else 0) | opcode])
        head += bytes([0x80 | n]) if n < 126 else (bytes([0xFE]) + struct.pack(">H", n) if n < 65536
                                                   else bytes([0xFF]) + struct.pack(">Q", n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def send_json(self, type_, payload=None, opcode=2):
        msg = {"type": type_} if payload is None else {"type": type_, "payload": payload}
        self.send(opcode, json.dumps(msg).encode())

    def recv_exact(self, n):
        while len(self.buf) < n:
            more = self.sock.recv(65536)
            if not more: raise ConnectionError("closed")
            self.buf += more
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self):
        b0, b1 = self.recv_exact(2)
        n = b1 & 0x7F
        if n == 126: n = struct.unpack(">H", self.recv_exact(2))[0]
        elif n == 127: n = struct.unpack(">Q", self.recv_exact(8))[0]
        data = self.recv_exact(n)
        return (b0 & 0x0F), data

    def recv_json(self):
        while True:
            op, data = self.recv()
            if op == 1: return json.loads(data)
            if op == 8: raise ConnectionError("closed by the bridge")

    def open(self):
        """What Studio's page does on connect: read the bridge's greeting, send its own handshake."""
        ack = self.recv_json()
        self.send_json("handshake", {"clientName": "Tripo Studio", "protocolVersion": "1.1.0"})
        return ack

    def send_file(self, fid, name, ftype, blob, chunk):
        """Send the chunks one by one, each after the ack for the one before, as Studio does. Returns the acks."""
        parts = [blob[i:i + chunk] for i in range(0, len(blob), chunk)]
        acks = []
        for i, part in enumerate(parts):
            head = {"payload": {"chunkIndex": i, "chunkSize": len(part), "chunkTotal": len(parts), "fileId": fid,
                                "fileName": name, "fileType": ftype}, "type": "file_transfer"}
            self.send(2, json.dumps(head).encode() + part)
            ack = self.recv_json()
            acks.append(ack)
            if ack["type"] != "file_transfer_ack" or ack["payload"].get("fileIndex") != i \
                    or ack["payload"].get("fileId") != fid or not ack["payload"].get("success"):
                break                               # Studio would stall here
        return acks

    def close(self):
        try: self.send(8, struct.pack(">H", 1000))
        except OSError: pass
        self.sock.close()


class TripoBridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wb = ThreadingHTTPServer(("127.0.0.1", 0), StubWorkbench)
        threading.Thread(target=cls.wb.serve_forever, daemon=True).start()
        cls.wb_url = "http://127.0.0.1:%d" % cls.wb.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.wb.shutdown()
        cls.wb.server_close()

    def setUp(self):
        StubWorkbench.calls = []
        self.tmp = tempfile.mkdtemp(prefix="tripo_bridge_")
        self.logs = []
        self.bridges = []

    def tearDown(self):
        for b in self.bridges:
            b.shutdown()
            b.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.assertFalse(any(TOKEN in line for line in self.logs), "the token was logged")

    def start(self, forward=True, rig=False, server=None):
        log = self.logs.append
        fw = tripo_bridge.Forwarder(server or self.wb_url, TOKEN, rig=rig, clips=["Idle", "Walk"], log=log) \
            if forward else None
        self.bridges = tripo_bridge.open_bridges(0, self.tmp, fw, log=log)
        for b in self.bridges:
            threading.Thread(target=b.serve_forever, daemon=True).start()
        self.bridge = self.bridges[0]
        return self.bridge.server_address[1]

    def connect(self, port, **kw):
        c = FakeStudio(port, **kw)
        self.addCleanup(c.close)
        return c

    def test_greeting_handshake_and_ping(self):
        c = self.connect(self.start(forward=False))
        self.assertEqual(c.status, 101)
        ack = c.open()                              # the bridge greets first, as the plug-ins do
        self.assertEqual(ack["type"], "handshake_ack")
        self.assertIs(ack["payload"]["success"], True)
        self.assertEqual(ack["payload"]["clientName"], "Godot")
        self.assertEqual(ack["payload"]["protocolVersion"], "1.0.0")
        c.send_json("ping")                         # Studio's heartbeat: a binary {"type": "ping"}
        self.assertEqual(c.recv_json(), {"type": "pong"})
        c.send_json("ping", opcode=1)               # and as text
        self.assertEqual(c.recv_json(), {"type": "pong"})
        c.send(9, b"hi")                            # a WebSocket-level ping gets a WebSocket pong
        self.assertEqual(c.recv(), (10, b"hi"))
        self.assertTrue(any(l.startswith("connection from 127.0.0.1:") for l in self.logs))
        self.assertTrue(any("Origin: " + STUDIO in l for l in self.logs))

    def test_localhost_over_ipv6(self):
        port = self.start(forward=False)
        if len(self.bridges) < 2: self.skipTest("no IPv6 loopback on this machine")
        c = self.connect(port, addr="::1")
        self.assertEqual(c.status, 101)
        self.assertEqual(c.open()["type"], "handshake_ack")

    def transfer(self, c, blob, name="Cow", ftype="glb", chunk=4096, fid="f1", greet=True):
        if greet: c.open()
        acks = c.send_file(fid, name, ftype, blob, chunk)
        total = -(-len(blob) // chunk)
        self.assertEqual([(a["type"], a["payload"]) for a in acks],
                         [("file_transfer_ack", {"success": True, "fileId": fid, "fileIndex": i, "code": 0})
                          for i in range(total)])
        msgs = [c.recv_json() for _ in range(3)]
        self.assertEqual([m["type"] for m in msgs], ["file_transfer_complete", "file_loaded", "import_complete"])
        self.assertEqual(msgs[0]["payload"]["status"], "importing")
        self.assertEqual(msgs[1]["payload"]["fileId"], fid)
        self.assertEqual(msgs[1]["payload"]["success"], msgs[2]["payload"]["success"])
        return msgs[1]["payload"]

    def test_chunked_file_is_reassembled_and_added(self):
        port = self.start()
        blob = os.urandom(3 * 4096 + 1000)          # four chunks, including one past the 4096-byte unmask cut-over
        done = self.transfer(self.connect(port), blob)
        self.assertIs(done["success"], True)
        with open(os.path.join(self.tmp, "Cow.glb"), "rb") as fh:
            self.assertEqual(fh.read(), blob)
        self.assertEqual(len(StubWorkbench.calls), 1)
        path, headers, body = StubWorkbench.calls[0]
        self.assertEqual(path, "/api/v1/models?filename=Cow.glb&name=Cow")
        self.assertEqual(headers["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertEqual(body, blob)

    def test_godot_zip_like_studio(self):
        # Studio sends Godot an FBX with its textures packed as a zip: fileType "zip", fileName the project name.
        port = self.start(forward=False)
        blob = os.urandom(5 * 1024 * 1024 + 12345)  # one full 5 MB chunk and a short last one
        self.assertIs(self.transfer(self.connect(port), blob, name="Barn", ftype="zip",
                                    chunk=5 * 1024 * 1024)["success"], True)
        with open(os.path.join(self.tmp, "Barn.zip"), "rb") as fh:
            self.assertEqual(fh.read(), blob)
        self.assertEqual(StubWorkbench.calls, [])

    def test_rig_sends_it_to_the_pipeline(self):
        port = self.start(rig=True)
        self.transfer(self.connect(port), b"glTF" + os.urandom(100), name="Wolf.glb")
        self.assertEqual([p for p, _, _ in StubWorkbench.calls], ["/api/v1/models?filename=Wolf.glb&name=Wolf",
                                                                   "/api/v1/pipeline"])
        self.assertEqual(json.loads(StubWorkbench.calls[1][2]),
                         {"model": "Wolf", "template": "auto", "clips": ["Idle", "Walk"]})
        self.assertEqual(self.bridge.received[0][1], {"model": "Wolf", "job": 7})

    def test_workbench_refusal_is_reported_and_the_file_kept(self):
        port = self.start()
        self.bridge.forwarder.token = "wrong"
        done = self.transfer(self.connect(port), b"glTF" + os.urandom(50))
        self.assertIs(done["success"], False)
        self.assertIn("401", done["errorMessage"])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "Cow.glb")))

    def test_names_are_made_safe_and_not_overwritten(self):
        port = self.start(forward=False)
        c = self.connect(port)
        self.transfer(c, b"one", name="../../evil", ftype="fbx", fid="a")
        self.transfer(c, b"two", name="../../evil", ftype="fbx", fid="b", greet=False)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["evil.fbx", "evil_2.fbx"])

    def test_other_origins_are_refused(self):
        port = self.start()
        for origin in ("https://evil.example", "http://studio.tripo3d.ai", "https://studio.tripo3d.ai.evil.example",
                       "https://studio.tripo3d.ai:8443", "null", None):
            c = self.connect(port, origin=origin)
            self.assertEqual(c.status, 403, origin)
            self.assertNotIn("101", c.response.split("\r\n")[0])
        self.assertTrue(any("refused a connection from https://evil.example" in l for l in self.logs))
        self.assertEqual(self.connect(port, origin="https://studio.tripo3d.com").status, 101)
        self.assertEqual(StubWorkbench.calls, [])

    def test_rebinding_host_is_refused(self):
        self.assertEqual(self.connect(self.start(), host="attacker.example:60650").status, 403)

    def test_bad_chunk_closes_the_connection(self):
        c = self.connect(self.start(forward=False))
        c.open()
        head = {"type": "file_transfer", "payload": {"fileId": "x", "fileName": "a.glb", "fileType": "glb",
                                                     "chunkIndex": 5, "chunkTotal": 2, "chunkSize": 3}}
        c.send(2, json.dumps(head).encode() + b"abc")
        op, data = c.recv()
        self.assertEqual(op, 8)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_header_split_counts_braces_outside_strings(self):
        head = {"type": "file_transfer", "payload": {"fileName": "we}ird{\"name"}}
        got, rest = tripo_bridge.split_header(json.dumps(head).encode() + b"{}\x00raw")
        self.assertEqual(got, head)
        self.assertEqual(rest, b"{}\x00raw")

    def test_token_only_over_https_or_loopback(self):
        with self.assertRaises(ValueError):
            tripo_bridge.Forwarder("http://autorig.cougarcave.dev", TOKEN)
        tripo_bridge.Forwarder("https://autorig.cougarcave.dev", TOKEN)
        tripo_bridge.Forwarder("http://localhost:8765", TOKEN)

    def test_main_never_prints_the_token(self):
        out = io.StringIO()
        os.environ["TRIPO_BRIDGE_TEST_TOKEN"] = TOKEN
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), self.assertRaises(SystemExit):
                tripo_bridge.main(["--server", "http://example.com", "--token-env", "TRIPO_BRIDGE_TEST_TOKEN"])
        finally:
            del os.environ["TRIPO_BRIDGE_TEST_TOKEN"]
        self.assertNotIn(TOKEN, out.getvalue())


if __name__ == "__main__":
    unittest.main()
