# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: what the three generator adapters (tripo.py, meshy.py, rodin.py) share. Plain urllib, nothing to
# install.
#
#   call(method, url, key, json_body=None, form=None)  -> the reply's JSON (GeneratorError with the provider's reason)
#   images(list)                                        -> [Image]: data: URIs or https:// links, checked
#   download(url, dest, base)                           -> dest; https only (http only from a stub on this machine)
#
# A key goes only to the provider's own API host, never to the CDN a result is downloaded from, and never into a log
# or an error message.
import base64
import binascii
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

_core = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _core not in sys.path: sys.path.insert(0, _core)
import studio_generation  # noqa: E402  (check_output: the file names and links a generator may hand back)

MAX_DOWNLOAD = 1 << 30                       # 1 GB, as POST /api/v1/models
MAX_IMAGE = 20 << 20                         # 20 MB: all three providers' limit for one picture
UA = "AutorigWorkbench/1 (+https://github.com/sambena/autorig-workbench)"
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/webp": ".webp"}


class GeneratorError(Exception):
    """A provider refused or failed. status: its HTTP status, when there was one."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def base_url(env, default):
    """The provider's API root. The env override exists for the tests' stub server."""
    return (os.environ.get(env) or default).rstrip("/")


def is_local(url):
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


def _scrub(text, key):
    text = str(text or "")
    return text.replace(key, "***") if key else text


def call(method, url, key, json_body=None, form=None, timeout=120):
    """One API request. form: [(field, value)] where value is a str or (filename, bytes, content type)."""
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if key: headers["Authorization"] = "Bearer " + key
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data, headers["Content-Type"] = multipart(form)
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            body = json.loads(raw)
            why = body.get("message") or body.get("error") or body.get("detail") or raw[:300]
            if isinstance(body.get("suggestion"), str): why = "%s (%s)" % (why, body["suggestion"])
        except (ValueError, AttributeError):
            why = raw[:300].decode("utf-8", "replace")
        raise GeneratorError("%s answered %d: %s" % (urllib.parse.urlparse(url).netloc, e.code, _scrub(why, key)), e.code)
    except (urllib.error.URLError, OSError) as e:
        raise GeneratorError("could not reach %s: %s" % (urllib.parse.urlparse(url).netloc, _scrub(e, key)))
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        raise GeneratorError("%s sent something that is not JSON" % urllib.parse.urlparse(url).netloc)


def multipart(form):
    boundary = "autorig" + uuid.uuid4().hex
    out = bytearray()
    for name, value in form:
        out += ("--%s\r\n" % boundary).encode()
        if isinstance(value, tuple):
            filename, data, ctype = value
            out += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: %s\r\n\r\n'
                    % (name, filename, ctype)).encode()
            out += data
        else:
            out += ('Content-Disposition: form-data; name="%s"\r\n\r\n' % name).encode() + str(value).encode("utf-8")
        out += b"\r\n"
    out += ("--%s--\r\n" % boundary).encode()
    return bytes(out), "multipart/form-data; boundary=" + boundary


class Image:
    """A reference picture: a data: URI (bytes here) or an https:// link (fetched only when a provider needs bytes)."""

    def __init__(self, url=None, data=None, ctype=None, view=None):
        self.url, self.data, self.ctype, self.view = url, data, ctype, view

    @property
    def filename(self):
        return "%s%s" % (self.view or "image", IMAGE_TYPES.get(self.ctype or "", ".png"))

    def data_uri(self):
        if self.url: return self.url
        return "data:%s;base64,%s" % (self.ctype, base64.b64encode(self.data).decode("ascii"))

    def bytes(self):
        """The picture's bytes, downloading a link (free; the link is the caller's own)."""
        if self.data is None:
            req = urllib.request.Request(self.url, headers={"User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    self.data = r.read(MAX_IMAGE + 1)
                    self.ctype = (r.headers.get("Content-Type") or "image/png").split(";")[0].strip().lower()
            except (urllib.error.URLError, OSError) as e:
                raise GeneratorError("could not fetch the picture %s: %s" % (self.url, e))
            if len(self.data) > MAX_IMAGE: raise ValueError("a picture is over 20 MB")
            if self.ctype not in IMAGE_TYPES: raise ValueError("a picture is PNG, JPEG or WebP")
        return self.data


VIEWS = ("front", "left", "back", "right")
DATA_URI = re.compile(r"^data:(image/[a-z+.-]+);base64,(.*)$", re.S | re.I)


def images(items):
    """[Image] from the request's images: each a data: URI, an https:// link, or {view: one of those}."""
    if items in (None, "", []): return []
    if not isinstance(items, list): items = [items]
    if len(items) > 5: raise ValueError("at most 5 pictures")
    out = []
    for it in items:
        view = None
        if isinstance(it, dict):
            if len(it) != 1: raise ValueError("a picture is a link, a data: URI, or {front|left|back|right: one of those}")
            view, it = next(iter(it.items()))
            if view not in VIEWS: raise ValueError("a view is one of %s" % ", ".join(VIEWS))
        if not isinstance(it, str): raise ValueError("a picture is a link or a data: URI")
        m = DATA_URI.match(it.strip())
        if m:
            ctype = m.group(1).lower()
            if ctype not in IMAGE_TYPES: raise ValueError("a picture is PNG, JPEG or WebP")
            try:
                data = base64.b64decode(m.group(2), validate=False)
            except (binascii.Error, ValueError):
                raise ValueError("a data: URI that is not base64")
            if not data: raise ValueError("an empty picture")
            if len(data) > MAX_IMAGE: raise ValueError("a picture is over 20 MB")
            out.append(Image(data=data, ctype="image/jpeg" if ctype == "image/jpg" else ctype, view=view))
        elif it.startswith("https://"):
            out.append(Image(url=it, view=view))
        else:
            raise ValueError("a picture is an https:// link or a data: URI")
    return out


def download(url, dest, base, name=None):
    """Fetch a finished model (free: it is already paid for). https only; http only when the provider's API root is
    itself on this machine (the tests' stub). No key is sent."""
    studio_generation.check_output(name or os.path.basename(dest), url, allow_http=is_local(base) and is_local(url))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    got = 0
    tmp = dest + ".part"
    try:
        with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as fh:
            while True:
                chunk = r.read(1 << 20)
                if not chunk: break
                got += len(chunk)
                if got > MAX_DOWNLOAD: raise GeneratorError("the download is over 1 GB")
                fh.write(chunk)
        os.replace(tmp, dest)
    except GeneratorError:
        raise
    except (urllib.error.URLError, OSError) as e:
        raise GeneratorError("download failed: %s" % e)
    finally:
        if os.path.exists(tmp): os.remove(tmp)
    return dest


def pick(options, allowed, name):
    """Only options this adapter knows how to price: anything else is refused, so the estimate can't be wrong."""
    options = options or {}
    if not isinstance(options, dict): raise ValueError("options is an object")
    bad = sorted(set(options) - set(allowed))
    if bad:
        raise ValueError("%s does not take %s here (it takes %s)" % (name, ", ".join(bad), ", ".join(sorted(allowed))))
    return dict(options)


def one_of(value, choices, what):
    if value not in choices: raise ValueError("%s is one of %s" % (what, ", ".join(str(c) for c in choices)))
    return value


def boolean(value, what):
    if not isinstance(value, bool): raise ValueError("%s is true or false" % what)
    return value
