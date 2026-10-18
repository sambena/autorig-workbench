# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: receive models from Tripo Studio's DCC Bridge and hand them to the workbench (docs/GENERATE.md).
#
# Tripo Studio (https://studio.tripo3d.ai), open in the browser, pushes a model to a WebSocket server on the user's
# own machine. This script is that server, standing in for Tripo's Godot plug-in on Godot's port, so the real Blender
# (60600) and Unity (60610) bridges stay free. It saves each model, then adds it to the workbench (the local server or
# the hosted copy) and, with --rig, rigs and animates it straight away. Sending a model that's already made costs
# nothing; this script never asks Tripo to generate anything.
#
#   python scripts/tripo_bridge.py --save-only                         save to ./tripo_inbox, forward nothing
#   python scripts/tripo_bridge.py                                     add to the workbench on 127.0.0.1:8765
#   python scripts/tripo_bridge.py --server https://autorig.cougarcave.dev --rig --clips Idle,Walk
#
# The token comes from the environment (AUTORIG_TOKEN, or the variable --token-env names) and is never printed.
# Only a Tripo Studio page may connect: any other Origin gets 403, so another web page can't push files here.
#
# Written from the protocol notes in docs/GENERATE.md. Tripo's plug-in code is not copied (its zips carry no licence).
# Standard library only.
import argparse
import base64
import hashlib
import json
import os
import re
import socket
import socketserver
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PORT = 60650                                   # Tripo's Godot plug-in port
CLIENT_NAME = "Godot"
PROTOCOL_VERSION = "1.0.0"
DCC_VERSION = "4.4"
PLUGIN_VERSION = "1.0.1"
STUDIO_HOSTS = ("studio.tripo3d.ai", "studio.tripo3d.com")
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 16 * 1024 * 1024                 # one chunk is 5 MB plus its header
MAX_FILE = 1024 * 1024 * 1024
MAX_HEADER = 64 * 1024
MODEL_EXTS = (".glb", ".gltf", ".fbx", ".obj", ".zip")


def studio_origin(origin):
    """True only for an https Tripo Studio page."""
    try:
        u = urllib.parse.urlsplit((origin or "").strip())
    except ValueError:
        return False
    return u.scheme == "https" and (u.hostname or "") in STUDIO_HOSTS and u.port in (None, 443)


def local_host(host):
    """The Host header must name this machine, so a rebinding DNS name can't reach us from a page."""
    h = (host or "").strip().lower()
    h = h[1:h.index("]")] if h.startswith("[") and "]" in h else h.rsplit(":", 1)[0] if h.count(":") == 1 else h
    return h in ("127.0.0.1", "localhost", "::1")


def split_header(data):
    """A file_transfer frame is a JSON header followed straight away by the bytes. Find the end of the JSON by
    counting braces outside strings, as the plug-ins do, and return (header, rest)."""
    if not data[:1] == b"{":
        raise ValueError("a file_transfer frame starts with its JSON header")
    depth, in_str, esc = 0, False, False
    for i, c in enumerate(data[:MAX_HEADER]):
        if in_str:
            if esc: esc = False
            elif c == 0x5C: esc = True
            elif c == 0x22: in_str = False
        elif c == 0x22: in_str = True
        elif c == 0x7B: depth += 1
        elif c == 0x7D:
            depth -= 1
            if depth == 0:
                return json.loads(data[:i + 1].decode("utf-8")), data[i + 1:]
    raise ValueError("no complete JSON header in the first %d bytes" % MAX_HEADER)


def safe_name(s, default="tripo_model"):
    s = re.sub(r"[^A-Za-z0-9._ -]+", "_", os.path.basename(str(s or ""))).strip(" .")
    return s[:120] or default


class Forwarder:
    """Sends a finished file to the workbench API. The token lives only in this object and the request header."""

    def __init__(self, server, token, group="", rig=False, template="auto", clips=(), fmt="", log=print):
        self.server = server.rstrip("/")
        u = urllib.parse.urlsplit(self.server)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("--server is http(s)://host[:port]")
        if u.scheme == "http" and u.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("the token only goes over https, or plain http to this machine")
        self.token, self.group, self.rig = token, group, rig
        self.template, self.clips, self.fmt, self.log = template, list(clips), fmt, log

    def call(self, path, raw=None, body=None, ctype="application/json"):
        data = raw if raw is not None else json.dumps(body).encode()
        headers = {"Content-Type": ctype}
        if self.token: headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.server + path, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            msg = e.read(2000).decode("utf-8", "replace")
            try: msg = json.loads(msg).get("error") or msg
            except ValueError: pass
            raise RuntimeError("%s answered %d: %s" % (path.split("?")[0], e.code, msg)) from None

    def send(self, path, name):
        q = {"filename": os.path.basename(path), "name": name}
        if self.group: q["group"] = self.group
        with open(path, "rb") as fh:
            out = self.call("/api/v1/models?" + urllib.parse.urlencode(q), raw=fh.read(),
                            ctype="application/octet-stream")
        model = (out.get("model") or {}).get("name") or name
        self.log("added %s to %s%s" % (model, self.server, " (already there)" if out.get("existing") else ""))
        result = {"model": model}
        if self.rig:
            body = {"model": model, "template": self.template}
            if self.clips: body["clips"] = self.clips
            if self.fmt: body["format"] = self.fmt
            job = self.call("/api/v1/pipeline", body=body)
            result["job"] = job.get("id")
            self.log("pipeline job %s queued for %s" % (job.get("id"), model))
        return result


class Transfer:
    def __init__(self, head):
        self.name = safe_name(head.get("fileName"))
        self.type = str(head.get("fileType") or "").lower().lstrip(".")
        self.total = int(head.get("chunkTotal") or 0)
        if not 1 <= self.total <= MAX_FILE // 1024:
            raise ValueError("chunkTotal %r is out of range" % head.get("chunkTotal"))
        self.chunks, self.size = {}, 0

    def add(self, index, data):
        if not 0 <= index < self.total:
            raise ValueError("chunk %d of %d" % (index, self.total))
        self.size += len(data) - len(self.chunks.get(index, b""))
        if self.size > MAX_FILE:
            raise ValueError("file is over %d MB" % (MAX_FILE >> 20))
        self.chunks[index] = data
        return len(self.chunks) == self.total


class Bridge(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = sys.platform != "win32"   # on Windows it would let two servers share the port

    def __init__(self, host="127.0.0.1", port=PORT, out_dir="tripo_inbox", forwarder=None, log=print, received=None):
        self.out_dir, self.forwarder, self.log = out_dir, forwarder, log
        self.received = [] if received is None else received   # (path, forward result or error) per finished file
        self.address_family = socket.AF_INET6 if ":" in host else socket.AF_INET
        super().__init__((host, port), Handler)


def open_bridges(port=PORT, out_dir="tripo_inbox", forwarder=None, log=print):
    """Studio connects to ws://localhost:<port>, which the browser may try as ::1 or as 127.0.0.1, so listen on both
    loopback addresses (never on a LAN address). IPv6 is optional: without it, 127.0.0.1 alone."""
    received = []
    bridges = [Bridge("127.0.0.1", port, out_dir, forwarder, log, received)]
    try:
        bridges.append(Bridge("::1", bridges[0].server_address[1], out_dir, forwarder, log, received))
    except OSError as e:
        log("not listening on [::1]:%d (%s); 127.0.0.1 only" % (bridges[0].server_address[1], e))
    return bridges


class Closed(Exception):
    pass


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.lock = threading.Lock()
        self.transfers = {}
        self.sock = self.request
        self.sock.settimeout(120)
        self.peer = "%s:%s" % self.client_address[:2]
        self.server.log("connection from %s" % self.peer)   # before any check, so a failed try still shows up
        try:
            if not self.upgrade(): return
            self.server.log("Tripo Studio connected (%s)" % self.peer)
            self.greet()
            while True:
                op, data = self.read_message()
                if op == 1: self.on_message(json.loads(data.decode("utf-8")))
                elif op == 2: self.on_binary(data)
        except (Closed, ConnectionError, socket.timeout, OSError):
            self.server.log("%s disconnected" % self.peer)
        except Exception as e:                      # a protocol error: say why, then close
            self.server.log("connection closed: %s" % e)
            try: self.send_frame(8, struct.pack(">H", 1002) + str(e).encode()[:120])
            except OSError: pass

    # --- HTTP upgrade -------------------------------------------------------------------------------------------
    def upgrade(self):
        buf = b""
        while b"\r\n\r\n" not in buf:
            more = self.sock.recv(4096)
            if not more or len(buf) > 16384: return False
            buf += more
        lines = buf.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")
        hdr = {}
        for line in lines[1:]:
            k, _, v = line.partition(":")
            hdr[k.strip().lower()] = v.strip()
        origin = hdr.get("origin", "")
        self.server.log("  %s  Origin: %s  Host: %s" % (lines[0][:200], origin or "(none)", hdr.get("host", "(none)")))
        if not studio_origin(origin):
            self.server.log("refused a connection from %s" % (origin or "a page with no Origin"))
            return self.refuse(403, "only Tripo Studio may connect")
        if not local_host(hdr.get("host")):
            return self.refuse(403, "connect to 127.0.0.1")
        key = hdr.get("sec-websocket-key", "")
        if "websocket" not in hdr.get("upgrade", "").lower() or not key:
            return self.refuse(426, "this is a WebSocket endpoint")
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        self.sock.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           "Sec-WebSocket-Accept: %s\r\n\r\n" % accept).encode())
        return True

    def refuse(self, code, why):
        body = why.encode()
        self.sock.sendall(b"HTTP/1.1 %d %s\r\nContent-Type: text/plain\r\nContent-Length: %d\r\nConnection: close\r\n\r\n"
                          % (code, b"Forbidden" if code == 403 else b"Upgrade Required", len(body)) + body)
        return False

    # --- WebSocket frames ---------------------------------------------------------------------------------------
    def recv_exact(self, n):
        out = bytearray()
        while len(out) < n:
            more = self.sock.recv(min(n - len(out), 1 << 20))
            if not more: raise Closed()
            out += more
        return bytes(out)

    def read_message(self):
        """One whole data message (text or binary), joining fragments and answering control frames on the way."""
        op, parts, size = None, [], 0
        while True:
            b0, b1 = self.recv_exact(2)
            fin, opcode, masked, n = b0 & 0x80, b0 & 0x0F, b1 & 0x80, b1 & 0x7F
            if n == 126: n = struct.unpack(">H", self.recv_exact(2))[0]
            elif n == 127: n = struct.unpack(">Q", self.recv_exact(8))[0]
            if not masked: raise ValueError("browser frames are masked")
            if size + n > MAX_MESSAGE: raise ValueError("message over %d MB" % (MAX_MESSAGE >> 20))
            mask = self.recv_exact(4)
            data = self.recv_exact(n)
            data = bytes(b ^ mask[i & 3] for i, b in enumerate(data)) if n < 4096 else unmask(data, mask)
            if opcode == 8:
                try: self.send_frame(8, data[:2])
                except OSError: pass
                raise Closed()
            if opcode == 9: self.send_frame(10, data); continue
            if opcode == 10: continue
            if opcode in (1, 2):
                if op is not None: raise ValueError("new message inside a fragmented one")
                op = opcode
            elif opcode != 0 or op is None:
                raise ValueError("unexpected opcode %d" % opcode)
            parts.append(data)
            size += n
            if fin: return op, b"".join(parts)

    def send_frame(self, opcode, data):
        n = len(data)
        head = bytes([0x80 | opcode]) + (bytes([n]) if n < 126 else
                                         b"\x7e" + struct.pack(">H", n) if n < 65536 else b"\x7f" + struct.pack(">Q", n))
        with self.lock:
            self.sock.sendall(head + data)

    def send_json(self, type_, payload):
        self.send_frame(1, json.dumps({"type": type_, "payload": payload}).encode())

    # --- Tripo's messages ---------------------------------------------------------------------------------------
    # Studio sends every message as a binary frame (a Blob), the handshake and pings included; text frames are taken
    # too. Replies go as text frames.
    def greet(self):
        """Like the plug-ins, greet first: Studio waits for a handshake_ack with success before it sends a file."""
        self.send_json("handshake_ack", {"success": True, "clientName": CLIENT_NAME, "dccVersion": DCC_VERSION,
                                         "pluginVersion": PLUGIN_VERSION, "protocolVersion": PROTOCOL_VERSION})

    def on_message(self, msg):
        t, p = msg.get("type"), msg.get("payload") or {}
        if t == "handshake":
            self.server.log("handshake from %s (protocol %s)" % (p.get("clientName", "?"), p.get("protocolVersion", "?")))
        elif t == "ping":
            self.send_frame(1, b'{"type": "pong"}')
        else:
            self.server.log("ignored a %r message" % t)

    def on_binary(self, data):
        msg, chunk = split_header(data)
        if msg.get("type") != "file_transfer":
            return self.on_message(msg)
        p = msg.get("payload") or {}
        fid = str(p.get("fileId") or "")
        if not fid: raise ValueError("file_transfer without a fileId")
        index = int(p.get("chunkIndex", -1))
        if "chunkSize" in p and int(p["chunkSize"]) != len(chunk):
            raise ValueError("chunk %d says %s bytes but carries %d" % (index, p["chunkSize"], len(chunk)))
        tr = self.transfers.get(fid)
        if tr is None:
            tr = self.transfers[fid] = Transfer(p)
            self.server.log("receiving %s (fileType %s, %d chunk%s)" % (tr.name, tr.type or "none", tr.total,
                                                                         "" if tr.total == 1 else "s"))
        done = tr.add(index, chunk)
        # Studio sends the next chunk only on an ack whose fileId and fileIndex match the chunk it just sent.
        self.send_json("file_transfer_ack", {"success": True, "fileId": fid, "fileIndex": index, "code": 0})
        if done:
            del self.transfers[fid]
            path = self.save(tr)
            self.send_json("file_transfer_complete", {"fileId": fid, "status": "importing",
                                                      "message": "saved; adding it to the workbench"})
            threading.Thread(target=self.forward, args=(fid, tr, path), daemon=True).start()

    def save(self, tr):
        os.makedirs(self.server.out_dir, exist_ok=True)
        name = tr.name
        if tr.type and not name.lower().endswith("." + tr.type): name += "." + tr.type
        stem, ext = os.path.splitext(name)
        path, n = os.path.join(self.server.out_dir, name), 1
        while os.path.exists(path):
            n += 1
            path = os.path.join(self.server.out_dir, "%s_%d%s" % (stem, n, ext))
        with open(path + ".part", "wb") as fh:
            for i in range(tr.total): fh.write(tr.chunks[i])
        os.replace(path + ".part", path)
        self.server.log("saved %s (%s, %.1f MB)" % (path, ext.lstrip(".") or "no type", tr.size / 1048576))
        return path

    def forward(self, fid, tr, path):
        """Forward the file (off the read loop, so pings keep getting answers) and then report import_complete."""
        fw, result, status, err = self.server.forwarder, None, "success", ""
        if fw is not None:
            try:
                if not path.lower().endswith(MODEL_EXTS):
                    raise RuntimeError("the workbench takes %s, not %s" % (", ".join(MODEL_EXTS), os.path.basename(path)))
                result = fw.send(path, os.path.splitext(os.path.basename(path))[0])
            except Exception as e:
                status, err = "error", str(e)
                self.server.log("could not add %s to the workbench: %s (the file is still in %s)"
                                % (tr.name, err, self.server.out_dir))
        self.server.received.append((path, result if not err else {"error": err}))
        # Studio often closes the socket after the last ack, so these may find it gone. file_loaded is what Studio's
        # page reads (its protocol 1.1.0); import_complete is what the 1.0.0 plug-ins send.
        ok = status == "success"
        try:
            self.send_json("file_loaded", {"fileId": fid, "success": ok, "errorMessage": err or None})
            self.send_json("import_complete", {"fileId": fid, "success": ok, "message": err or "added to the workbench"})
        except OSError:
            pass


def unmask(data, mask):
    """XOR a large payload with its 4-byte mask, a machine word at a time."""
    n = len(data)
    key = int.from_bytes(mask * ((n + 3) // 4), "little")
    return (int.from_bytes(data, "little") ^ key).to_bytes(n + (-n % 4), "little")[:n] if n else b""


def main(argv=None):
    ap = argparse.ArgumentParser(description="Receive models from Tripo Studio's DCC Bridge (pick Godot) and add "
                                             "them to the Autorig Workbench.")
    ap.add_argument("--port", type=int, default=PORT, help="default %d, Tripo's Godot port" % PORT)
    ap.add_argument("--out", default="tripo_inbox", help="where received files are saved (default ./tripo_inbox)")
    ap.add_argument("--save-only", action="store_true", help="save the files and forward nothing")
    ap.add_argument("--server", default="http://127.0.0.1:8765",
                    help="the workbench: a local server or https://autorig.cougarcave.dev")
    ap.add_argument("--token-env", default="AUTORIG_TOKEN", help="environment variable holding its token")
    ap.add_argument("--group", default="", help="collection group for new models")
    ap.add_argument("--rig", action="store_true", help="rig and animate each model as it lands (POST /api/v1/pipeline)")
    ap.add_argument("--template", default="auto", help="pipeline template (default auto)")
    ap.add_argument("--clips", default="", help="pipeline clips, comma separated")
    ap.add_argument("--format", default="", help="pipeline export format, e.g. glb,fbx")
    a = ap.parse_args(argv)
    log = lambda m: print(time.strftime("%H:%M:%S ") + m, flush=True)
    fw = None
    if not a.save_only:
        token = os.environ.get(a.token_env, "")
        if not token: ap.error("set %s to the workbench token, or pass --save-only" % a.token_env)
        try:
            fw = Forwarder(a.server, token, a.group, a.rig, a.template,
                           [c.strip() for c in a.clips.split(",") if c.strip()], a.format, log)
        except ValueError as e:
            ap.error(str(e))
    try:
        bridges = open_bridges(a.port, a.out, fw, log)
    except OSError as e:
        sys.exit("can't listen on 127.0.0.1:%d (%s). Is Tripo's Godot plug-in or another bridge running?" % (a.port, e))
    addrs = " and ".join("[%s]" % b.server_address[0] if ":" in b.server_address[0] else b.server_address[0]
                         for b in bridges)
    log("Listening on ws://localhost:%d (%s). In Tripo Studio open DCC Bridge, pick Godot and connect. Files go to "
        "%s%s." % (a.port, addrs, os.path.abspath(a.out), "" if fw is None else ", then to " + fw.server))
    log("Every connection attempt is logged here. If nothing appears, Studio never reached this machine.")
    for b in bridges[1:]:
        threading.Thread(target=b.serve_forever, daemon=True).start()
    try:
        bridges[0].serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for b in bridges: b.server_close()


if __name__ == "__main__":
    main()
