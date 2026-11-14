"""网页图片全能抓取器 - 本地桌面版核心服务（队列调度 + 可选 curl_cffi TLS 指纹 + 按域名分目录）"""

import base64
import ctypes
import gzip
import hashlib
import json
import os
import queue
import re
import ssl
import struct
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from curl_cffi import requests as _cffi_req
    HAS_CFFI = True
except Exception:
    _cffi_req = None
    HAS_CFFI = False

APP_NAME = "网页图片全能抓取器"
PORT = 17890
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

IMG_EXT = re.compile(
    r"\.(?:jpe?g|png|gif|webp|avif|bmp|ico|svg|apng|jxl|heic|heif|tiff?)(?:[?#]|$)",
    re.I,
)
URL_IN_TEXT = re.compile(
    r"(?:https?:)?//[^\s\"'<>(){}|\\^`\[\]]+?\.(?:jpe?g|png|gif|webp|avif|bmp|ico|svg|apng|jxl|heic|heif|tiff?)(?:\?[^\s\"'<>(){}|\\^`\[\]]*)?",
    re.I,
)
CSS_URL = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)", re.I)
DATA_URI = re.compile(r"data:image/[a-z0-9.+-]+;base64,[a-z0-9+/=]+", re.I)
TAG_RE = re.compile(r"<([a-zA-Z][\w:-]*)([^>]*?)/?>", re.S)
ATTR_RE = re.compile(
    r"([\w:.-]+)\s*=\\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", re.S
)
ATTR_RE = re.compile(
    r"([\w:.-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", re.S
)

MEDIA_TAGS = frozenset(
    {"img", "image", "source", "input", "video", "audio", "picture", "embed", "object"}
)
MEDIA_ATTRS = frozenset(
    {"src", "href", "xlink:href", "poster", "data", "content", "value"}
)
DROP_PARAMS = frozenset(
    {
        "w",
        "width",
        "h",
        "height",
        "size",
        "s",
        "q",
        "quality",
        "resize",
        "thumb",
        "x-oss-process",
        "imagemogr2",
        "imageview2",
        "spec",
    }
)

MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/svg+xml": ".svg",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "image/bmp": ".bmp",
    "image/x-ms-bmp": ".bmp",
    "image/apng": ".png",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "image/tiff": ".tiff",
    "image/jxl": ".jxl",
}


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(app_dir(), "config.json")
DEFAULT_DIR = os.path.join(app_dir(), "下载图片")


def load_config():
    c = {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            c = json.load(f)
    except Exception:
        c = {}
    c.setdefault("css", True)
    c.setdefault("upscale", True)
    c.setdefault("unknown", False)
    c.setdefault("ignore_ssl", False)
    c.setdefault("auto_subdir", True)
    c.setdefault("min_w", "0")
    c.setdefault("workers", "6")
    c.setdefault("timeout", "20")
    c.setdefault("cookie", "")
    c.setdefault("save_dir", DEFAULT_DIR)
    return c


def save_config(c):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def safe_name(s):
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(s or "image"))
    s = re.sub(r"\s+", "_", s).strip()
    return s[:70] or "image"


def base_name(u):
    s = str(u or "")
    low = s.lower()
    if low.startswith("data:") or low.startswith("b64:"):
        return "inline"
    try:
        path = urllib.parse.urlsplit(s).path.rstrip("/")
        last = path.split("/")[-1]
        if last:
            last = urllib.parse.unquote(last)
            if "." in last:
                last = last.rsplit(".", 1)[0]
            if last:
                return last
    except Exception:
        pass
    return "image"


def ext_of(mime, url):
    e = MIME_EXT.get(str(mime or "").lower().split(";")[0].strip())
    if e:
        return e
    m = re.search(r"\.([a-z0-9]{2,5})(?:[?#]|$)", str(url or ""), re.I)
    return ("." + m.group(1).lower()) if m else ".img"


def sniff(data):
    h = data[:24]
    if h[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if h[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if h[:3] == b"GIF":
        return "image/gif"
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "image/webp"
    if h[4:8] == b"ftyp" and h[8:12] in (b"avif", b"avis", b"heic", b"heix", b"mif1"):
        return "image/avif" if h[8:12] in (b"avif", b"avis") else "image/heic"
    if h[:2] == b"BM":
        return "image/bmp"
    low = h.lstrip().lower()
    if low.startswith(b"<?xml") or low.startswith(b"<svg"):
        return "image/svg+xml"
    return ""


def image_size(d):
    try:
        if len(d) < 10:
            return (0, 0)
        if d[:8] == b"\x89PNG\r\n\x1a\n" and d[12:16] == b"IHDR":
            return struct.unpack(">II", d[16:24])
        if d[:3] == b"GIF":
            return struct.unpack("<HH", d[6:10])
        if d[:4] == b"RIFF" and d[8:12] == b"WEBP":
            chunk = d[12:16]
            if chunk == b"VP8X" and len(d) >= 30:
                w = int.from_bytes(d[24:27], "little") + 1
                h = int.from_bytes(d[27:30], "little") + 1
                return (w, h)
            if chunk == b"VP8 " and len(d) >= 30 and d[23:26] == b"\x9d\x01*":
                w, h = struct.unpack("<HH", d[26:30])
                return (w & 16383, h & 16383)
            if chunk == b"VP8L" and len(d) >= 25:
                b = int.from_bytes(d[21:25], "little")
                return ((b & 16383) + 1, ((b >> 14) & 16383) + 1)
        if d[:2] == b"BM" and len(d) >= 26:
            w, h = struct.unpack("<ii", d[18:26])
            return (abs(w), abs(h))
        # AVIF / HEIC ISOBMFF 'ispe' box
        if d[4:8] == b"ftyp":
            idx = d.find(b"ispe", 0, min(len(d), 8192))
            if idx != -1 and idx + 16 <= len(d):
                w, h = struct.unpack(">II", d[idx + 8 : idx + 16])
                if 0 < w < 100000 and 0 < h < 100000:
                    return (w, h)
        # SVG width/height/viewBox
        head_low = d[:512].lstrip().lower()
        if head_low.startswith(b"<svg") or head_low.startswith(b"<?xml"):
            txt = d[:4096].decode("utf-8", "ignore")
            mw = re.search(r'\bwidth\s*=\s*["\'](\d+(?:\.\d+)?)(?:px)?["\']', txt, re.I)
            mh = re.search(r'\bheight\s*=\s*["\'](\d+(?:\.\d+)?)(?:px)?["\']', txt, re.I)
            if mw and mh:
                return (int(float(mw.group(1))), int(float(mh.group(1))))
            mvb = re.search(
                r'\bviewBox\s*=\s*["\']\s*[-\d.]+\s+[-\d.]+\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)',
                txt,
                re.I,
            )
            if mvb:
                return (int(float(mvb.group(1))), int(float(mvb.group(2))))
        if d[:2] == b"\xff\xd8":
            i = 2
            ln = len(d)
            while i + 4 < ln:
                if d[i] != 0xFF:
                    i += 1
                    continue
                while i < ln and d[i] == 0xFF:
                    i += 1
                if i >= ln:
                    break
                m = d[i]
                i += 1
                if m in (216, 217):
                    continue
                if i + 2 > ln:
                    break
                seg_len = struct.unpack(">H", d[i : i + 2])[0]
                if m in (192, 193, 194, 195, 197, 198, 199, 201, 202, 203, 205, 206, 207):
                    if i + 7 <= ln:
                        h, w = struct.unpack(">HH", d[i + 3 : i + 7])
                        return (w, h)
                    break
                i += seg_len
    except Exception:
        pass
    return (0, 0)


def normalize(raw, page=""):
    if raw is None:
        return None
    s = str(raw).strip().strip("\"'`")
    if not s or len(s) > 8000:
        return None
    s = s.replace("\\/", "/").replace("&amp;", "&")
    low = s.lower()
    if low.startswith("data:image/") or low.startswith("blob:"):
        return s
    if s.startswith("//"):
        scheme = "https"
        if page:
            try:
                ps = urllib.parse.urlsplit(page).scheme
                if ps in ("http", "https"):
                    scheme = ps
            except Exception:
                pass
        s = scheme + ":" + s
    elif page and not re.match(r"^[a-z][a-z0-9+.-]*:", low):
        s = urllib.parse.urljoin(page, s)
    try:
        p = urllib.parse.urlsplit(s)
        if p.scheme in ("http", "https") and p.netloc:
            return urllib.parse.urlunsplit(p)
    except Exception:
        pass
    return None


def looks_image(u):
    if not u:
        return False
    low = u.lower()
    if low.startswith("data:image/"):
        return True
    if IMG_EXT.search(u):
        return True
    if re.search(r"/(?:image|img|photo|pic|upload|media|thumb|file)s?/", u, re.I):
        return True
    if re.search(r"[?&](?:image|img|file|url|src|pic)=", u, re.I):
        return True
    return False


def upscale_candidates(u):
    res = []
    try:
        p = urllib.parse.urlsplit(u)
        if p.scheme not in ("http", "https"):
            return res
        m = re.search(r"\.[a-z0-9]{2,5}$", p.path, re.I)
        if m:
            ext = m.group(0)
            stem = p.path[: -len(ext)]
            cleaned = re.sub(r"[-_]\d{2,4}x\d{2,4}$", "", stem, flags=re.I)
            cleaned = re.sub(
                r"[-_@](?:thumb|thumbnail|small|mini|s\d{2,4}|m\d{2,4}|l\d{2,4}|q\d{2,3})$",
                "",
                cleaned,
                flags=re.I,
            )
            cleaned = re.sub(r"[-_]\d{2,4}w$", "", cleaned, flags=re.I).rstrip("-_")
            if cleaned and cleaned != stem:
                res.append(
                    urllib.parse.urlunsplit(
                        (p.scheme, p.netloc, cleaned + ext, p.query, p.fragment)
                    )
                )
        if p.query:
            q = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
            kept = [(k, v) for k, v in q if k.lower() not in DROP_PARAMS]
            if len(kept) != len(q):
                res.append(
                    urllib.parse.urlunsplit(
                        (p.scheme, p.netloc, p.path, urllib.parse.urlencode(kept), p.fragment)
                    )
                )
        seg = re.sub(
            r"/(?:thumb|thumbnail|small|resize|_\d+x\d+|s\d+)/",
            "/",
            p.path,
            flags=re.I,
        )
        if seg != p.path:
            res.append(
                urllib.parse.urlunsplit((p.scheme, p.netloc, seg, p.query, p.fragment))
            )
    except Exception:
        pass
    return res


def extract_images(html, page_url, o):
    out = []
    seen = set()

    def add(u, src, expand=True):
        n = normalize(u, page_url)
        if not n or n in seen:
            return
        seen.add(n)
        out.append((n, src))
        if expand and o.get("upscale", True):
            for c in upscale_candidates(n):
                add(c, "原图候选", False)

    for tag, attrstr in TAG_RE.findall(html):
        tl = tag.lower()
        for name, a, b, c in ATTR_RE.findall(attrstr):
            v = (a or b or c or "").strip()
            if not v or len(v) > 4000:
                continue
            low = name.lower()
            if "srcset" in low:
                for part in v.split(","):
                    u = part.strip().split()[0] if part.strip() else ""
                    if u:
                        add(u, "srcset")
            elif "url(" in v:
                for m in CSS_URL.finditer(v):
                    add(m.group(2), "style")
            elif v.startswith("data:image/"):
                add(v, "inline", False)
            elif (tl in MEDIA_TAGS and low in MEDIA_ATTRS) or looks_image(v):
                add(v, "元素" if tl in MEDIA_TAGS else "属性")

    clean = html.replace("\\/", "/").replace("&amp;", "&")
    for m in URL_IN_TEXT.finditer(clean):
        add(m.group(0), "源码")
    for m in DATA_URI.finditer(clean):
        add(m.group(0), "inline", False)
    if o.get("css", True):
        for m in CSS_URL.finditer(clean):
            add(m.group(2), "CSS背景")
    return out


def decode_html(data):
    m = re.search(rb"charset=[\"']?([\w-]+)", data[:4096], re.I)
    enc = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    try:
        return data.decode(enc, "replace")
    except Exception:
        return data.decode("utf-8", "replace")


def decode_data_uri(u):
    m = re.match(r"^data:([^;,]+)?(;base64)?,(.*)$", u, re.S | re.I)
    if not m:
        raise ValueError("无法解析内联图片")
    mime = (m.group(1) or "image/png").lower()
    if m.group(2):
        data = base64.b64decode(re.sub(r"\s", "", m.group(3)))
    else:
        data = urllib.parse.unquote_to_bytes(m.group(3))
    return data, mime


def fetch(url, referer="", cookie="", timeout=20, ignore_ssl=False, retries=2):
    headers = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
    }
    if referer:
        headers["Referer"] = referer
    if cookie:
        headers["Cookie"] = cookie

    if HAS_CFFI:
        last_err = None
        for attempt in range(retries + 1):
            try:
                r = _cffi_req.get(
                    url,
                    headers=headers,
                    timeout=timeout,
                    verify=not ignore_ssl,
                    impersonate="chrome",
                )
                if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code}")
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                return r.content[:31457280], ctype
            except Exception as e:
                last_err = e
                if attempt < retries:
                    time.sleep(0.4 * (2 ** attempt))
                    continue
                raise
        raise last_err

    ctx = None
    if ignore_ssl:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                raw = r.read(31457280)
                cenc = (r.headers.get("Content-Encoding") or "").lower().strip()
                if cenc == "gzip":
                    raw = gzip.decompress(raw)
                elif cenc == "deflate":
                    try:
                        raw = zlib.decompress(raw)
                    except zlib.error:
                        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                return raw, ctype
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(0.5 * (2 ** attempt))
                continue
            raise
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(0.4 * (2 ** attempt))
                continue
            raise
    raise last_err


def save_image(data, ctype, url, save_dir, idx, min_w, keep_unknown, seen_hashes=None, name_lock=None):
    if not data:
        raise ValueError("空文件")
    mime = ctype if str(ctype or "").startswith("image/") else sniff(data)
    if not mime and not keep_unknown:
        raise ValueError("返回内容不是图片")
    w, h = image_size(data)
    if min_w > 0 and w > 0 and w < min_w:
        raise ValueError("宽度 %d 小于设定值" % w)

    if seen_hashes is not None:
        digest = hashlib.sha256(data).hexdigest()
        with (name_lock or LOCK):
            if digest in seen_hashes:
                raise ValueError("与已保存图片内容重复，已跳过")
            seen_hashes.add(digest)

    ext = ext_of(mime, url)
    stem = "%03d_%s" % (idx, safe_name(base_name(url)))
    with (name_lock or LOCK):
        name = stem + ext
        path = os.path.join(save_dir, name)
        dup = 2
        while os.path.exists(path):
            name = "%s_%d%s" % (stem, dup, ext)
            path = os.path.join(save_dir, name)
            dup += 1
        with open(path, "wb") as f:
            f.write(data)
    return name, w, h


def resolve_save_dir(base_dir, page_url, auto_subdir):
    if not auto_subdir or not page_url:
        return base_dir
    try:
        host = urllib.parse.urlsplit(page_url).hostname or ""
        host = re.sub(r"^www\.", "", host, flags=re.I)
        if host:
            sub = safe_name(host + "_" + datetime.now().strftime("%Y%m%d"))
            return os.path.join(base_dir, sub)
    except Exception:
        pass
    return base_dir


def pick_folder_dialog():
    cb = STATE.get("folder_picker")
    if cb is not None:
        return cb()
    try:
        import tkinter as tk
        from tkinter import filedialog

        r = tk.Tk()
        r.withdraw()
        r.attributes("-topmost", True)
        d = filedialog.askdirectory(
            parent=r,
            title="选择图片保存文件夹",
            initialdir=STATE.get("save_dir") or DEFAULT_DIR,
        )
        r.destroy()
        return d or ""
    except Exception as e:
        raise RuntimeError("文件夹对话框调用失败：" + str(e))


LOCK = threading.Lock()
JOB_QUEUE = queue.Queue()
STATE = {
    "logs": [],
    "log_id": 0,
    "done": 0,
    "total": 0,
    "running": False,
    "pending_jobs": 0,
    "stop": threading.Event(),
    "save_dir": DEFAULT_DIR,
    "last_result": "",
    "folder_picker": None,
}


def log(msg, kind="info"):
    with LOCK:
        STATE["log_id"] += 1
        item = {
            "id": STATE["log_id"],
            "t": datetime.now().strftime("%H:%M:%S"),
            "msg": str(msg),
            "kind": kind,
        }
        STATE["logs"].append(item)
        if len(STATE["logs"]) > 1200:
            STATE["logs"] = STATE["logs"][-800:]
    try:
        print("[%s] %s" % (item["t"], item["msg"]), flush=True)
    except Exception:
        pass


def set_progress(done, total):
    with LOCK:
        STATE["done"] = done
        STATE["total"] = total


def num(v, default, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except Exception:
        return default


def norm_opts(o):
    o = o or {}
    return {
        "css": bool(o.get("css", True)),
        "upscale": bool(o.get("upscale", True)),
        "unknown": bool(o.get("unknown", False)),
        "ignore_ssl": bool(o.get("ignore_ssl", False)),
        "auto_subdir": bool(o.get("auto_subdir", True)),
        "min_w": num(o.get("min_w"), 0, 0, 100000),
        "workers": num(o.get("workers"), 6, 1, 20),
        "timeout": num(o.get("timeout"), 20, 3, 120),
        "cookie": str(o.get("cookie") or "").strip(),
    }


def busy():
    with LOCK:
        return STATE["running"]


def _worker_loop():
    while True:
        task = JOB_QUEUE.get()
        try:
            pages, images, save_dir, o, referer = task
            with LOCK:
                STATE["running"] = True
                STATE["stop"] = threading.Event()
                STATE["pending_jobs"] = max(0, STATE["pending_jobs"] - 1)
                STATE["done"] = 0
                STATE["total"] = 0
                STATE["last_result"] = ""
            run_job(pages, images, save_dir, o, referer)
        except Exception:
            log("队列执行异常：\n" + traceback.format_exc(), "err")
        finally:
            with LOCK:
                STATE["running"] = False
            JOB_QUEUE.task_done()


_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()


def ensure_worker():
    global _WORKER_STARTED
    with _WORKER_LOCK:
        if not _WORKER_STARTED:
            threading.Thread(target=_worker_loop, daemon=True).start()
            _WORKER_STARTED = True


def enqueue_job(pages, images, save_dir, o, referer=""):
    ensure_worker()
    target_dir = resolve_save_dir(
        save_dir, referer or (pages[0] if pages else ""), o.get("auto_subdir", True)
    )
    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception as e:
        log("无法创建保存文件夹：" + str(e), "err")
        return False, 0

    with LOCK:
        STATE["save_dir"] = save_dir
        STATE["pending_jobs"] += 1
        pending = STATE["pending_jobs"]
        is_running = STATE["running"]

    JOB_QUEUE.put((pages, images, target_dir, o, referer))
    if is_running:
        log("当前有任务执行中，已加入队列（排队位置 #%d）" % pending, "info")
    return True, pending


def stop_all_jobs():
    cleared = 0
    while True:
        try:
            JOB_QUEUE.get_nowait()
            JOB_QUEUE.task_done()
            cleared += 1
        except queue.Empty:
            break
    with LOCK:
        STATE["pending_jobs"] = 0
        STATE["stop"].set()
    log("已请求停止当前任务" + (f"并清空 {cleared} 个排队任务" if cleared else ""), "warn")


def run_job(pages, images, save_dir, o, referer):
    stop = STATE["stop"]
    try:
        candidates = []
        seen = set()
        b64map = {}

        for item in images or []:
            if isinstance(item, dict):
                u = item.get("url") or ""
                b64 = item.get("b64") or ""
                mime = item.get("mime") or ""
                if b64:
                    n = len(b64map) + 1
                    key = "b64:%d" % n
                    b64map[key] = (b64, mime)
                    candidates.append(key)
                elif u:
                    nu = normalize(u, referer)
                    if nu and nu not in seen:
                        seen.add(nu)
                        candidates.append(nu)
            else:
                nu = normalize(item, referer)
                if nu and nu not in seen:
                    seen.add(nu)
                    candidates.append(nu)

        for p in pages or []:
            if stop.is_set():
                break
            log("抓取页面：" + p)
            try:
                data, _ = fetch(
                    p,
                    referer=p,
                    cookie=o.get("cookie", ""),
                    timeout=o.get("timeout", 20),
                    ignore_ssl=o.get("ignore_ssl", False),
                )
                html = decode_html(data)
                found = extract_images(html, p, o)
                log("  源码 %d 字符，提取 %d 个候选地址" % (len(html), len(found)))
                for u, _s in found:
                    if u not in seen:
                        seen.add(u)
                        candidates.append(u)
            except Exception as e:
                log("  页面抓取失败：" + str(e)[:200], "err")

        total = len(candidates)
        set_progress(0, total)
        if not total:
            log("没有找到可下载的图片。需要登录或 JS 渲染的网页，请用浏览器扩展推送。", "warn")
            return

        log("去重后共 %d 张，保存到 %s" % (total, save_dir), "ok")
        ok = 0
        fail = 0
        seen_hashes = set()
        save_lock = threading.Lock()

        def job(pair):
            idx, u = pair
            if stop.is_set():
                return (False, "")
            try:
                if u.startswith("data:"):
                    data, ctype = decode_data_uri(u)
                elif u in b64map:
                    b, ctype = b64map[u]
                    data = base64.b64decode(re.sub(r"\s", "", b))
                else:
                    ref = referer or (pages[0] if pages else "")
                    data, ctype = fetch(
                        u,
                        referer=ref,
                        cookie=o.get("cookie", ""),
                        timeout=o.get("timeout", 20),
                        ignore_ssl=o.get("ignore_ssl", False),
                    )
                name, w, h = save_image(
                    data,
                    ctype,
                    u,
                    save_dir,
                    idx,
                    o.get("min_w", 0),
                    o.get("unknown", False),
                    seen_hashes=seen_hashes,
                    name_lock=save_lock,
                )
                return (True, "已保存 %s  (%dx%d)" % (name, w, h))
            except Exception as e:
                return (False, str(e)[:160])

        with ThreadPoolExecutor(max_workers=o.get("workers", 6)) as ex:
            for done_idx, (r, msg) in enumerate(ex.map(job, enumerate(candidates, 1)), 1):
                if r:
                    ok += 1
                    log(msg, "ok")
                elif msg:
                    fail += 1
                    log("跳过：" + msg, "err")
                set_progress(done_idx, total)

        summary = "完成：成功 %d 张，跳过 %d 张。文件位置：%s" % (ok, fail, save_dir)
        log(summary, "ok" if ok else "warn")
        with LOCK:
            STATE["last_result"] = summary
    except Exception:
        log("任务异常：\n" + traceback.format_exc(), "err")


INDEX_HTML = r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>网页图片全能抓取器</title>
<style>
:root{--bg:#16181d;--panel:#202329;--panel2:#272b32;--line:#343a43;--fg:#e8eaed;--mut:#9aa3af;--acc:#4c9aff;--ok:#4ec9a0;--err:#f2777a;--warn:#e5c07b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:22px 24px 60px}
header{display:flex;align-items:baseline;gap:14px;margin-bottom:16px}
h1{font-size:19px;font-weight:500;margin:0}.sub{color:var(--mut);font-size:12px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:12px}
h2{font-size:13px;font-weight:500;margin:0 0 10px}
.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:9px}.row:last-child{margin-bottom:0}
.lbl{color:var(--mut);font-size:12px;white-space:nowrap}
.hint{color:var(--mut);font-size:12px;line-height:1.55;margin-top:8px;border-left:2px solid var(--line);padding-left:10px}
button{background:var(--panel2);color:var(--fg);border:1px solid var(--line);border-radius:7px;padding:7px 13px;font-size:12px;cursor:pointer;font-family:inherit}
button:hover{border-color:var(--acc)}button:disabled{opacity:.45;cursor:not-allowed}
button.primary{background:#2c5f9e;border-color:#3b76c4;color:#fff}button.primary:hover{background:#356fb8}
input[type=text],input[type=number],textarea{background:#191c21;color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:7px 9px;font-size:12px;font-family:inherit;outline:none}
input:focus,textarea:focus{border-color:var(--acc)}textarea{width:100%;height:86px;resize:vertical;font-family:Consolas,monospace}
#saveDir{flex:1;min-width:320px;font-family:Consolas,monospace}
.chk{display:inline-flex;align-items:center;gap:5px;font-size:12px;cursor:pointer;white-space:nowrap}
input[type=checkbox]{accent-color:var(--acc)}
.bar{display:flex;align-items:center;gap:10px;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:11px 14px;margin-bottom:12px}
.prog{flex:1;height:8px;background:#111318;border-radius:5px;overflow:hidden}#progFill{height:100%;width:0;background:var(--acc);transition:width .2s}
#statTxt{font-size:12px;color:var(--mut);min-width:130px;text-align:right}
.log{background:#101216;border:1px solid var(--line);border-radius:8px;padding:10px 12px;height:280px;overflow:auto;font:11px/1.7 Consolas,"Microsoft YaHei",monospace;color:var(--mut)}
.log div{white-space:pre-wrap;word-break:break-all}.ok{color:var(--ok)}.err{color:var(--err)}.warn{color:var(--warn)}.info{color:#9fb2c9}
.badge{font-size:11px;padding:2px 8px;border-radius:10px;background:#27303b;color:var(--ok)}
</style></head><body><div class="wrap">
<header><h1>网页图片全能抓取器</h1><span class="sub">本地服务 · 队列调度 · 按域名分目录</span><span id="conn" class="badge">连接中…</span></header>
<section class="panel"><h2>网页地址（每行一个，可批量；留空则只接收浏览器扩展推送）</h2>
<textarea id="urls" placeholder="https://example.com/gallery&#10;https://example.com/page2"></textarea>
<div class="row" style="margin-top:8px"><span class="lbl">保存位置（真实绝对路径）</span><input type="text" id="saveDir"><button id="btnPick">浏览选择…</button><button id="btnOpen">打开该文件夹</button></div></section>
<section class="panel"><h2>抓取选项</h2>
<div class="row">
<label class="chk"><input type="checkbox" id="css" checked> CSS 背景与源码中的图片</label>
<label class="chk"><input type="checkbox" id="upscale" checked> 尝试还原原图</label>
<label class="chk"><input type="checkbox" id="auto_subdir" checked> 按域名+日期建子文件夹</label>
<label class="chk"><input type="checkbox" id="unknown"> 保留无法识别格式的资源</label>
<label class="chk"><input type="checkbox" id="ignore_ssl"> 忽略 HTTPS 证书错误</label></div>
<div class="row"><span class="lbl">最小宽度</span><input type="number" id="min_w" value="0" style="width:80px"><span class="lbl">px</span>
<span class="lbl">并发</span><input type="number" id="workers" value="6" style="width:60px">
<span class="lbl">超时(秒)</span><input type="number" id="timeout" value="20" style="width:60px">
<span class="lbl">Cookie（可选）</span><input type="text" id="cookie" style="width:230px" placeholder="需要登录的站点可填"></div></section>
<div class="bar"><button id="btnStart" class="primary">加入抓取队列</button><button id="btnStop">停止全部</button><button id="btnClear">清空日志</button><button id="btnExt">打开扩展文件夹</button>
<div class="prog"><div id="progFill"></div></div><span id="statTxt">就绪</span></div>
<div id="log" class="log"></div></div>
<script>
var lastId=0;function $(s){return document.querySelector(s)}
function api(p,body){return fetch(p,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{}).then(function(r){return r.json()})}
function opts(){return {css:$('#css').checked,upscale:$('#upscale').checked,auto_subdir:$('#auto_subdir').checked,unknown:$('#unknown').checked,ignore_ssl:$('#ignore_ssl').checked,min_w:$('#min_w').value,workers:$('#workers').value,timeout:$('#timeout').value,cookie:$('#cookie').value}}
function saveCfg(){api('/config',Object.assign({save_dir:$('#saveDir').value},opts()))}
function poll(){api('/status').then(function(s){
  var q=s.pending_jobs?('（排队 '+s.pending_jobs+'）'):'';
  $('#conn').textContent=(s.running?'抓取中':'服务已连接')+q;
  $('#progFill').style.width=s.total?Math.round(s.done/s.total*100)+'%':'0%';
  $('#statTxt').textContent=s.total?(s.done+' / '+s.total):(s.running?'准备中':'就绪');
  var box=$('#log');
  s.logs.forEach(function(l){if(l.id>lastId){var d=document.createElement('div');d.className=l.kind;d.textContent='['+l.t+'] '+l.msg;box.appendChild(d);lastId=l.id}});
  box.scrollTop=box.scrollHeight;
}).catch(function(){$('#conn').textContent='服务断开'});setTimeout(poll,600)}
$('#btnStart').onclick=function(){var urls=$('#urls').value.split('\n').map(function(x){return x.trim()}).filter(Boolean);api('/start',{urls:urls,save_dir:$('#saveDir').value,opts:opts()}).then(function(r){if(!r.ok)alert(r.error||'启动失败')});saveCfg()};
$('#btnStop').onclick=function(){api('/stop',{})};
$('#btnClear').onclick=function(){$('#log').innerHTML='';lastId=0;api('/clear',{})};
$('#btnPick').onclick=function(){api('/pickdir',{}).then(function(r){if(r.ok&&r.dir){$('#saveDir').value=r.dir;saveCfg()}})};
$('#btnOpen').onclick=function(){api('/openfolder',{dir:$('#saveDir').value})};
$('#btnExt').onclick=function(){api('/openext',{})};
api('/config').then(function(c){$('#saveDir').value=c.save_dir||'';['css','upscale','auto_subdir','unknown','ignore_ssl'].forEach(function(k){if(typeof c[k]==='boolean')$('#'+k).checked=c[k]});['min_w','workers','timeout','cookie'].forEach(function(k){if(c[k]!=null)$('#'+k).value=c[k]})});
poll();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ImageGrabber/1.2"

    def log_message(self, *args):
        return

    def _send(self, code, obj=None, raw=None, ctype="application/json; charset=utf-8"):
        body = (
            raw
            if raw is not None
            else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        )
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or "0")
            if n <= 0:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_OPTIONS(self):
        self._send(200, {"ok": True})

    def do_GET(self):
        p = urllib.parse.urlsplit(self.path).path
        if p in ("/", "/index.html"):
            self._send(200, raw=INDEX_HTML.encode("utf-8"), ctype="text/html; charset=utf-8")
        elif p == "/ping":
            with LOCK:
                running = STATE["running"]
                pending = STATE["pending_jobs"]
            self._send(
                200,
                {
                    "ok": True,
                    "app": APP_NAME,
                    "version": "1.2",
                    "running": running,
                    "pending_jobs": pending,
                    "cffi": HAS_CFFI,
                },
            )
        elif p == "/status":
            with LOCK:
                payload = {
                    "running": STATE["running"],
                    "pending_jobs": STATE["pending_jobs"],
                    "logs": STATE["logs"][-400:],
                    "done": STATE["done"],
                    "total": STATE["total"],
                    "last_result": STATE["last_result"],
                }
            self._send(200, payload)
        elif p == "/config":
            self._send(200, load_config())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        p = urllib.parse.urlsplit(self.path).path
        b = self._body()
        try:
            if p == "/push":
                items = b.get("urls") or []
                page = str(b.get("pageUrl") or "")
                cfg = load_config()
                d = str(b.get("save_dir") or STATE.get("save_dir") or cfg.get("save_dir") or DEFAULT_DIR).strip()
                log("收到浏览器推送：%d 张图片，来源 %s" % (len(items), page[:70] or "?"))
                ok, pending = enqueue_job([], items, d, norm_opts(cfg), referer=page)
                if ok:
                    self._send(200, {"ok": True, "queued": len(items), "pending_jobs": pending})
                else:
                    self._send(200, {"ok": False, "error": "创建保存目录失败"})
            elif p == "/start":
                urls = [str(x).strip() for x in (b.get("urls") or []) if str(x).strip()]
                d = str(b.get("save_dir") or DEFAULT_DIR).strip()
                o = norm_opts(b.get("opts"))
                if not urls:
                    log("未填网址，等待浏览器扩展推送…", "info")
                    self._send(200, {"ok": True, "waiting": True})
                    return
                ok, pending = enqueue_job(urls, [], d, o)
                if ok:
                    self._send(200, {"ok": True, "pending_jobs": pending})
                else:
                    self._send(200, {"ok": False, "error": "创建保存目录失败"})
            elif p == "/stop":
                stop_all_jobs()
                self._send(200, {"ok": True})
            elif p == "/clear":
                with LOCK:
                    STATE["logs"] = []
                self._send(200, {"ok": True})
            elif p == "/config":
                cfg = dict(load_config())
                for k, v in b.items():
                    cfg[k] = v
                save_config(cfg)
                if b.get("save_dir"):
                    with LOCK:
                        STATE["save_dir"] = str(b["save_dir"]).strip()
                self._send(200, {"ok": True})
            elif p == "/pickdir":
                d = pick_folder_dialog()
                if d:
                    with LOCK:
                        STATE["save_dir"] = d
                    cfg = load_config()
                    cfg["save_dir"] = d
                    save_config(cfg)
                self._send(200, {"ok": bool(d), "dir": d})
            elif p == "/openfolder":
                d = str(b.get("dir") or STATE.get("save_dir") or DEFAULT_DIR).strip()
                os.makedirs(d, exist_ok=True)
                if hasattr(os, "startfile"):
                    os.startfile(d)
                self._send(200, {"ok": True})
            elif p == "/openext":
                d = os.path.join(app_dir(), "browser-extension")
                if os.path.isdir(d) and hasattr(os, "startfile"):
                    os.startfile(d)
                    self._send(200, {"ok": True})
                else:
                    self._send(200, {"ok": False, "error": "未找到 browser-extension 文件夹"})
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:
            log("请求处理失败：" + str(e), "err")
            self._send(500, {"ok": False, "error": str(e)})


class Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    cfg = load_config()
    save_dir = cfg.get("save_dir") or DEFAULT_DIR
    STATE["save_dir"] = save_dir
    os.makedirs(save_dir, exist_ok=True)
    ensure_worker()

    url = "http://127.0.0.1:%d/" % PORT
    print("=" * 58)
    print(" ", APP_NAME, " v1.2")
    print("  TLS 引擎   :", "curl_cffi (Chrome 指纹)" if HAS_CFFI else "urllib 标准库")
    print("  默认保存   :", save_dir)
    print("  操作界面   :", url)
    print("  扩展接收   : http://127.0.0.1:%d/push" % PORT)
    print("-" * 58, flush=True)

    try:
        srv = Server(("127.0.0.1", PORT), Handler)
    except Exception as e:
        print("端口 %d 启动失败：%s" % (PORT, e))
        input("按回车退出…")
        return

    log("服务已启动，等待任务…", "ok")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        with open(os.path.join(app_dir(), "error.log"), "a", encoding="utf-8") as f:
            f.write(traceback.format_exc() + "\n")
        traceback.print_exc()
        input("按回车退出…")
