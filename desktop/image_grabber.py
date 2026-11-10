# -*- coding: utf-8 -*-
"""网页图片全能抓取器 - 本地桌面版（EXE + 本地服务 + 浏览器界面，纯标准库）"""
import os
import sys
import re
import json
import ssl
import base64
import struct
import ctypes
import threading
import traceback
import webbrowser
import urllib.request
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

APP_NAME = "网页图片全能抓取器"
PORT = 17890
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

IMG_EXT = re.compile(r'\.(?:jpe?g|png|gif|webp|avif|bmp|ico|svg|apng|jxl|heic|heif|tiff?)(?:[?#]|$)', re.I)
URL_IN_TEXT = re.compile(
    r'(?:https?:)?//[^\s"\'<>(){}|\\^`\[\]]+?\.(?:jpe?g|png|gif|webp|avif|bmp|ico|svg|apng|jxl|heic|heif|tiff?)'
    r'(?:\?[^\s"\'<>(){}|\\^`\[\]]*)?', re.I)
CSS_URL = re.compile(r'url\(\s*([\'"]?)([^\'")]+)\1\s*\)', re.I)
DATA_URI = re.compile(r'data:image/[a-z0-9.+-]+;base64,[a-z0-9+/=]+', re.I)
TAG_RE = re.compile(r'<([a-zA-Z][\w:-]*)([^>]*?)/?>', re.S)
ATTR_RE = re.compile(r'([\w:.-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))', re.S)
MEDIA_TAGS = {'img', 'image', 'source', 'input', 'video', 'audio', 'embed', 'object', 'picture'}
MEDIA_ATTRS = {'src', 'href', 'poster', 'data', 'content', 'value', 'xlink:href'}
DROP_PARAMS = {'w', 'width', 'h', 'height', 'size', 's', 'q', 'quality', 'resize',
               'thumb', 'x-oss-process', 'imagemogr2', 'imageview2', 'spec'}

MIME_EXT = {
    'image/jpeg': '.jpg', 'image/jpg': '.jpg', 'image/png': '.png', 'image/gif': '.gif',
    'image/webp': '.webp', 'image/avif': '.avif', 'image/svg+xml': '.svg',
    'image/x-icon': '.ico', 'image/vnd.microsoft.icon': '.ico', 'image/bmp': '.bmp',
    'image/x-ms-bmp': '.bmp', 'image/apng': '.png', 'image/heic': '.heic',
    'image/heif': '.heif', 'image/tiff': '.tiff', 'image/jxl': '.jxl'
}


def app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(app_dir(), 'config.json')
DEFAULT_DIR = os.path.join(app_dir(), '下载图片')


def load_config():
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            c = json.load(f)
        c.setdefault('save_dir', DEFAULT_DIR)
        return c
    except Exception:
        return {'save_dir': DEFAULT_DIR, 'css': True, 'upscale': True,
                'unknown': False, 'ignore_ssl': False, 'min_w': '0',
                'workers': '6', 'timeout': '20', 'cookie': ''}


def save_config(c):
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def safe_name(s):
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', str(s or 'image'))
    s = re.sub(r'\s+', '_', s).strip('_')
    return (s[:70] or 'image')


def base_name(u):
    if str(u).lower().startswith('data:') or str(u).startswith('b64:'):
        return 'inline'
    try:
        last = urllib.parse.urlsplit(u).path.rstrip('/').split('/')[-1]
        return urllib.parse.unquote(last).rsplit('.', 1)[0] if '.' in last else (last or 'image')
    except Exception:
        return 'image'


def ext_of(mime, url):
    if mime:
        e = MIME_EXT.get(str(mime).lower().split(';')[0].strip())
        if e:
            return e
    m = re.search(r'\.([a-z0-9]{2,5})(?:[?#]|$)', str(url or ''), re.I)
    return ('.' + m.group(1).lower()) if m else '.img'


def sniff(data):
    h = data[:24]
    if h[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if h[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if h[:3] == b'GIF':
        return 'image/gif'
    if h[:4] == b'RIFF' and h[8:12] == b'WEBP':
        return 'image/webp'
    if h[4:8] == b'ftyp' and h[8:12] in (b'avif', b'avis'):
        return 'image/avif'
    if h[:2] == b'BM':
        return 'image/bmp'
    if h[:5].lower().startswith(b'<?xml') or b'<svg' in h.lower():
        return 'image/svg+xml'
    return ''


def image_size(d):
    try:
        if len(d) < 10:
            return 0, 0
        if d[:8] == b'\x89PNG\r\n\x1a\n' and d[12:16] == b'IHDR':
            return struct.unpack('>I', d[16:20])[0], struct.unpack('>I', d[20:24])[0]
        if d[:2] == b'\xff\xd8':
            i = 2
            while i < len(d) - 9:
                if d[i] != 0xFF:
                    i += 1
                    continue
                m = d[i + 1]
                if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    return struct.unpack('>H', d[i + 7:i + 9])[0], struct.unpack('>H', d[i + 5:i + 7])[0]
                if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
                    i += 2
                    continue
                ln = struct.unpack('>H', d[i + 2:i + 4])[0]
                if ln < 2:
                    break
                i += 2 + ln
        if d[:3] == b'GIF':
            return struct.unpack('<H', d[6:8])[0], struct.unpack('<H', d[8:10])[0]
        if d[:4] == b'RIFF' and d[8:12] == b'WEBP':
            f = d[12:16]
            if f == b'VP8X':
                return int.from_bytes(d[24:27], 'little') + 1, int.from_bytes(d[27:30], 'little') + 1
            if f == b'VP8 ':
                p = d.find(b'\x9d\x01\x2a')
                if p > 0:
                    return int.from_bytes(d[p + 3:p + 5], 'little') & 0x3fff, int.from_bytes(d[p + 5:p + 7], 'little') & 0x3fff
            if f == b'VP8L':
                b = int.from_bytes(d[21:25], 'little')
                return (b & 0x3fff) + 1, ((b >> 14) & 0x3fff) + 1
        if d[:2] == b'BM':
            return struct.unpack('<i', d[18:22])[0], abs(struct.unpack('<i', d[22:26])[0])
    except Exception:
        pass
    return 0, 0


def normalize(raw, page):
    if not raw:
        return None
    s = str(raw).strip().strip('"\'`').strip()
    if not s or len(s) > 8000:
        return None
    s = s.replace('&amp;', '&')
    if s.lower().startswith('data:image/'):
        return s
    if s.lower().startswith('blob:'):
        return None
    s = s.replace('\\/', '/')
    if s.startswith('//'):
        s = (urllib.parse.urlsplit(page).scheme or 'https') + ':' + s
    try:
        p = urllib.parse.urlsplit(s)
        if p.scheme and p.scheme not in ('http', 'https'):
            return None
        return urllib.parse.urljoin(page, s)
    except Exception:
        return None


def looks_image(u):
    if not u:
        return False
    if u.lower().startswith('data:image/') or IMG_EXT.search(u):
        return True
    if re.search(r'/(?:image|img|photo|pic|upload|media|thumb|file)s?/', u, re.I):
        return True
    if re.search(r'[?&](?:image|img|file|url|src|pic)=', u, re.I):
        return True
    return False


def upscale_candidates(u):
    res = []
    try:
        p = urllib.parse.urlsplit(u)
        if p.scheme not in ('http', 'https'):
            return res
        m = re.search(r'\.[a-z0-9]{2,5}$', p.path, re.I)
        if m:
            ext, stem = m.group(0), p.path[:-len(m.group(0))]
            cleaned = re.sub(r'[-_]?\d{2,5}x\d{2,5}$', '', stem, flags=re.I)
            cleaned = re.sub(r'[-_@](?:thumb|thumbnail|small|mini|s\d{2,4}|m\d{2,4}|l\d{2,4}|q\d{2,3})$', '', cleaned, flags=re.I)
            cleaned = re.sub(r'[-_]\d+w$', '', cleaned, flags=re.I).rstrip('-_')
            if cleaned and cleaned != stem:
                res.append(urllib.parse.urlunsplit((p.scheme, p.netloc, cleaned + ext, p.query, p.fragment)))
        if p.query:
            q = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
            kept = [(k, v) for k, v in q if k.lower() not in DROP_PARAMS]
            if len(kept) != len(q):
                res.append(urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(kept), p.fragment)))
        seg = re.sub(r'/(?:thumb|thumbnail|small|resize|_\d+x\d+|s\d+)/', '/', p.path, flags=re.I)
        if seg != p.path:
            res.append(urllib.parse.urlunsplit((p.scheme, p.netloc, seg, p.query, p.fragment)))
    except Exception:
        pass
    return res


def extract_images(html, page_url, o):
    out, seen = [], set()

    def add(u, src, expand=True):
        n = normalize(u, page_url)
        if not n or n in seen:
            return
        seen.add(n)
        out.append((n, src))
        if expand and o.get('upscale'):
            for c in upscale_candidates(n):
                add(c, '原图候选', False)

    for tag, attrstr in TAG_RE.findall(html):
        tl = tag.lower()
        for name, a, b, c in ATTR_RE.findall(attrstr):
            v = (a or b or c or '').strip()
            if not v or len(v) > 4000:
                continue
            low = name.lower()
            if 'srcset' in low:
                for part in v.split(','):
                    u = part.strip().split()[0] if part.strip() else ''
                    if u:
                        add(u, 'srcset')
                continue
            if 'url(' in v.lower():
                for m in CSS_URL.finditer(v):
                    add(m.group(2), 'style')
                continue
            if v.lower().startswith('data:image/'):
                add(v, 'inline')
                continue
            if (tl in MEDIA_TAGS and low in MEDIA_ATTRS) or looks_image(v):
                add(v, '元素' if tl in MEDIA_TAGS else '属性')

    clean = html.replace('\\/', '/').replace('&amp;', '&')
    for m in URL_IN_TEXT.finditer(clean):
        add(m.group(0), '源码')
    for m in DATA_URI.finditer(clean):
        add(m.group(0), 'inline')
    if o.get('css'):
        for m in CSS_URL.finditer(clean):
            add(m.group(2), 'CSS背景')
    return out


def decode_html(data):
    m = re.search(rb'charset=["\']?([\w-]+)', data[:4096], re.I)
    enc = m.group(1).decode('ascii', 'ignore') if m else 'utf-8'
    try:
        return data.decode(enc, 'replace')
    except Exception:
        return data.decode('utf-8', 'replace')


def decode_data_uri(u):
    m = re.match(r'^data:([^;,]+)?(;base64)?,(.*)$', u, re.S | re.I)
    if not m:
        raise ValueError('无法解析内联图片')
    mime = (m.group(1) or 'image/png').lower()
    if m.group(2):
        return base64.b64decode(re.sub(r'\s', '', m.group(3))), mime
    return urllib.parse.unquote_to_bytes(m.group(3)), mime


def fetch(url, referer, cookie, timeout, ignore_ssl):
    headers = {'User-Agent': UA, 'Accept': '*/*', 'Accept-Encoding': 'identity', 'Connection': 'close'}
    if referer:
        headers['Referer'] = referer
    if cookie:
        headers['Cookie'] = cookie
    req = urllib.request.Request(url, headers=headers)
    ctx = None
    if ignore_ssl:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        data = r.read(30 * 1024 * 1024)
        ctype = (r.headers.get('Content-Type') or '').split(';')[0].strip().lower()
    return data, ctype


def save_image(data, ctype, url, save_dir, idx, min_w, keep_unknown):
    if not data:
        raise ValueError('空文件')
    mime = ctype if ctype and ctype.startswith('image/') else sniff(data)
    if not mime and not keep_unknown:
        raise ValueError('返回内容不是图片')
    w, h = image_size(data)
    if min_w and w and w < min_w:
        raise ValueError('宽度 %d 小于设定值' % w)
    name = '%03d_%s%s' % (idx, safe_name(base_name(url)), ext_of(mime, url))
    with open(os.path.join(save_dir, name), 'wb') as f:
        f.write(data)
    return name, w, h


def pick_folder_dialog():
    """选择文件夹：优先交给主窗口的 Tk 主线程，避免后台线程直接操作 Tk。"""
    cb = STATE.get('folder_picker')
    if cb:
        return cb()
    try:
        import tkinter as tk
        from tkinter import filedialog
        r = tk.Tk()
        r.withdraw()
        try:
            r.attributes('-topmost', True)
        except Exception:
            pass
        try:
            d = filedialog.askdirectory(
                parent=r, title='选择图片保存文件夹',
                initialdir=STATE.get('save_dir') or DEFAULT_DIR)
        finally:
            r.destroy()
        return d or None
    except Exception as e:
        raise RuntimeError('文件夹对话框调用失败：' + str(e))


# ---------------- 运行状态 ----------------
LOCK = threading.Lock()
STATE = {
    'logs': [], 'log_id': 0, 'done': 0, 'total': 0,
    'running': False, 'stop': threading.Event(),
    'save_dir': load_config().get('save_dir') or DEFAULT_DIR,
    'last_result': '', 'folder_picker': None
}


def log(msg, kind='info'):
    with LOCK:
        STATE['log_id'] += 1
        STATE['logs'].append({'id': STATE['log_id'], 't': datetime.now().strftime('%H:%M:%S'),
                              'msg': str(msg), 'kind': kind})
        if len(STATE['logs']) > 1200:
            STATE['logs'] = STATE['logs'][-800:]
    try:
        print('[%s] %s' % (datetime.now().strftime('%H:%M:%S'), msg), flush=True)
    except Exception:
        pass


def set_progress(done, total):
    with LOCK:
        STATE['done'], STATE['total'] = done, total


def num(v, default, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except Exception:
        return default


def norm_opts(o):
    o = o or {}
    return {
        'css': bool(o.get('css', True)), 'upscale': bool(o.get('upscale', True)),
        'unknown': bool(o.get('unknown', False)), 'ignore_ssl': bool(o.get('ignore_ssl', False)),
        'min_w': num(o.get('min_w'), 0, 0, 100000),
        'workers': num(o.get('workers'), 6, 1, 32),
        'timeout': num(o.get('timeout'), 20, 3, 120),
        'cookie': str(o.get('cookie') or '').strip()
    }


def busy():
    with LOCK:
        return STATE['running']


def start_job(pages, images, save_dir, o, referer=''):
    if busy():
        return False
    try:
        os.makedirs(save_dir, exist_ok=True)
    except Exception as e:
        log('无法创建保存文件夹：' + str(e), 'err')
        return False
    with LOCK:
        STATE['running'] = True
        STATE['stop'] = threading.Event()
        STATE['save_dir'] = save_dir
        STATE['done'] = STATE['total'] = 0
        STATE['last_result'] = ''
    threading.Thread(target=run_job, args=(pages, images, save_dir, o, referer), daemon=True).start()
    return True


def run_job(pages, images, save_dir, o, referer):
    stop = STATE['stop']
    try:
        candidates, seen, b64map = [], set(), {}
        for item in images:
            if isinstance(item, dict):
                u, b64, mime = item.get('url', ''), item.get('b64', ''), item.get('mime', '')
            else:
                u, b64, mime = item, '', ''
            n = normalize(u, referer)
            key = n or ('b64:%d' % len(b64map))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(key)
            if b64:
                b64map[key] = (b64, mime)
        for p in pages:
            if stop.is_set():
                break
            log('抓取页面：' + p)
            try:
                data, _ = fetch(p, p, o['cookie'], o['timeout'], o['ignore_ssl'])
                html = decode_html(data)
                found = extract_images(html, p, o)
                log('  源码 %d 字符，提取 %d 个候选地址' % (len(html), len(found)))
                for u, _s in found:
                    if u not in seen:
                        seen.add(u)
                        candidates.append(u)
            except Exception as e:
                log('  页面抓取失败：' + str(e)[:200], 'err')

        total = len(candidates)
        log('去重后共 %d 张，保存到 %s' % (total, save_dir), 'ok')
        set_progress(0, total)
        if not total:
            log('没有找到可下载的图片。需要登录或 JS 渲染的网页，请用浏览器扩展推送。', 'warn')
            return

        ok = fail = 0

        def job(pair):
            idx, u = pair
            if stop.is_set():
                return None
            try:
                if u.startswith('data:'):
                    data, ctype = decode_data_uri(u)
                elif u in b64map:
                    b, ctype = b64map[u]
                    data = base64.b64decode(re.sub(r'\s', '', b))
                else:
                    data, ctype = fetch(u, referer or (pages[0] if pages else ''),
                                        o['cookie'], o['timeout'], o['ignore_ssl'])
                name, w, h = save_image(data, ctype, u, save_dir, idx, o['min_w'], o['unknown'])
                return ('ok', name, w, h, '')
            except Exception as e:
                return ('err', '', 0, 0, str(e)[:160])

        with ThreadPoolExecutor(max_workers=o['workers']) as ex:
            for idx, r in enumerate(ex.map(job, enumerate(candidates, 1)), 1):
                if r is None:
                    continue
                if r[0] == 'ok':
                    ok += 1
                    log('已保存 %s  (%dx%d)' % (r[1], r[2], r[3]))
                else:
                    fail += 1
                    log('跳过：' + r[4], 'warn')
                set_progress(idx, total)
                if stop.is_set():
                    break
        msg = '完成：成功 %d 张，跳过 %d 张。文件位置：%s' % (ok, fail, save_dir)
        log(msg, 'ok' if not fail else 'warn')
        with LOCK:
            STATE['last_result'] = msg
        set_progress(total, total)
    except Exception:
        log('任务异常：\n' + traceback.format_exc(), 'err')
    finally:
        with LOCK:
            STATE['running'] = False


# ---------------- 浏览器界面 ----------------
INDEX_HTML = r'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
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
details{font-size:12px;color:var(--mut)}summary{cursor:pointer;color:var(--fg)}details ul{padding-left:20px}details li{margin-bottom:5px}
</style></head><body><div class="wrap">
<header><h1>网页图片全能抓取器</h1><span class="sub">本地 EXE 服务 · 不限图片格式 · 写入任意本地文件夹</span><span id="conn" class="badge">连接中…</span></header>

<section class="panel"><h2>网页地址（每行一个，可批量；留空则只接收浏览器扩展推送）</h2>
<textarea id="urls" placeholder="https://example.com/gallery&#10;https://example.com/page2"></textarea>
<div class="row" style="margin-top:8px"><span class="lbl">保存位置（真实绝对路径）</span><input type="text" id="saveDir"><button id="btnPick">浏览选择…</button><button id="btnOpen">打开该文件夹</button></div>
<div class="hint">保存路径可以直接粘贴资源管理器地址栏里的路径；也可以点「浏览选择」调系统文件夹对话框。程序包体内的「下载图片」是默认目录。</div></section>

<section class="panel"><h2>抓取选项</h2>
<div class="row">
<label class="chk"><input type="checkbox" id="css" checked> CSS 背景与源码中的图片</label>
<label class="chk"><input type="checkbox" id="upscale" checked> 尝试还原原图</label>
<label class="chk"><input type="checkbox" id="unknown"> 保留无法识别格式的资源</label>
<label class="chk"><input type="checkbox" id="ignore_ssl"> 忽略 HTTPS 证书错误</label></div>
<div class="row"><span class="lbl">最小宽度</span><input type="number" id="min_w" value="0" style="width:80px"><span class="lbl">px</span>
<span class="lbl">并发</span><input type="number" id="workers" value="6" style="width:60px">
<span class="lbl">超时(秒)</span><input type="number" id="timeout" value="20" style="width:60px">
<span class="lbl">Cookie（可选）</span><input type="text" id="cookie" style="width:230px" placeholder="需要登录的站点可填"></div></section>

<div class="bar"><button id="btnStart" class="primary">开始抓取</button><button id="btnStop">停止</button><button id="btnClear">清空日志</button><button id="btnExt">打开扩展文件夹</button>
<div class="prog"><div id="progFill"></div></div><span id="statTxt">就绪</span></div>
<div id="log" class="log"></div>

<details class="panel"><summary>使用说明与包体结构</summary><ul>
<li><b>直接抓网页</b>：填网址 → 设置保存位置 → 开始抓取。会解析 img / srcset / picture / 懒加载 data-* / CSS 背景 / 源码与 JSON 里的图片地址 / 内联 base64 图，格式不限（JPG、PNG、WebP、AVIF、GIF、SVG、BMP、ICO、APNG、JXL、HEIC、无扩展名的动态图片）。</li>
<li><b>抓当前浏览器页面</b>：把包体 <code>browser-extension</code> 文件夹加载为 Chrome / Edge 扩展（开发者模式 → 加载已解压的扩展程序），在目标网页点扩展图标 → 勾选图片 → 「发送到桌面程序」。适合需要登录、JS 动态渲染、懒加载的网页。</li>
<li><b>本地包体</b>：本程序所在文件夹就是包体，含 ImageGrabber.exe、_internal 运行库、browser-extension 扩展、config.json 配置、下载图片 默认输出目录。右键桌面快捷方式 →「打开文件所在的位置」即可直接进入。</li>
<li><b>退出程序</b>：关闭启动时出现的黑色控制台窗口即可（它同时显示运行日志）。</li>
<li>请遵守目标网站条款，商用前确认图片版权授权。</li>
</ul></details>
</div>
<script>
var lastId=0;
function $(s){return document.querySelector(s)}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function api(p,body){return fetch(p,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{}).then(function(r){return r.json()})}
function opts(){return {css:$('#css').checked,upscale:$('#upscale').checked,unknown:$('#unknown').checked,ignore_ssl:$('#ignore_ssl').checked,min_w:$('#min_w').value,workers:$('#workers').value,timeout:$('#timeout').value,cookie:$('#cookie').value}}
function saveCfg(){api('/config',{save_dir:$('#saveDir').value,css:$('#css').checked,upscale:$('#upscale').checked,unknown:$('#unknown').checked,ignore_ssl:$('#ignore_ssl').checked,min_w:$('#min_w').value,workers:$('#workers').value,timeout:$('#timeout').value,cookie:$('#cookie').value})}
function poll(){api('/status').then(function(s){
  $('#conn').textContent=s.running?'抓取中':'服务已连接';
  $('#progFill').style.width=s.total?Math.round(s.done/s.total*100)+'%':'0%';
  $('#statTxt').textContent=s.total?(s.done+' / '+s.total):(s.running?'准备中':'就绪');
  var box=$('#log');
  s.logs.forEach(function(l){if(l.id>lastId){var d=document.createElement('div');d.className=l.kind;d.textContent='['+l.t+'] '+l.msg;box.appendChild(d);lastId=l.id}});
  box.scrollTop=box.scrollHeight;
}).catch(function(){$('#conn').textContent='服务断开'});setTimeout(poll,600)}
$('#btnStart').onclick=function(){var urls=$('#urls').value.split('\n').map(function(x){return x.trim()}).filter(Boolean);api('/start',{urls:urls,save_dir:$('#saveDir').value,opts:opts()}).then(function(r){if(!r.ok)alert(r.error||'启动失败')});saveCfg()};
$('#btnStop').onclick=function(){api('/stop',{})};
$('#btnClear').onclick=function(){$('#log').innerHTML='';lastId=0;api('/clear',{})};
$('#btnPick').onclick=function(){api('/pickdir',{}).then(function(r){if(r.ok&&r.dir){$('#saveDir').value=r.dir;saveCfg()}else if(r.error){alert('选择文件夹失败：'+r.error+'\n可直接粘贴文件夹路径。')}}).catch(function(e){alert('无法连接程序窗口：'+e)})};
$('#btnOpen').onclick=function(){api('/openfolder',{dir:$('#saveDir').value})};
$('#btnExt').onclick=function(){api('/openext',{})};
api('/config').then(function(c){$('#saveDir').value=c.save_dir||'';['css','upscale','unknown','ignore_ssl'].forEach(function(k){if(typeof c[k]==='boolean')$('#'+k).checked=c[k]});['min_w','workers','timeout','cookie'].forEach(function(k){if(c[k]!=null)$('#'+k).value=c[k]})});
poll();
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'ImageGrabber/1.0'

    def log_message(self, *args):
        pass

    def _send(self, code, obj=None, raw=None, ctype='application/json; charset=utf-8'):
        body = raw if raw is not None else json.dumps(obj or {}, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get('Content-Length', '0') or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode('utf-8'))
        except Exception:
            return {}

    def do_OPTIONS(self):
        self._send(200, {'ok': True})

    def do_GET(self):
        p = urllib.parse.urlsplit(self.path).path
        if p in ('/', '/index.html'):
            self._send(200, raw=INDEX_HTML.encode('utf-8'), ctype='text/html; charset=utf-8')
        elif p == '/ping':
            self._send(200, {'ok': True, 'app': APP_NAME, 'version': '1.0', 'running': busy()})
        elif p == '/status':
            with LOCK:
                payload = {'ok': True, 'logs': STATE['logs'][-400:], 'done': STATE['done'],
                           'total': STATE['total'], 'running': STATE['running'],
                           'last_result': STATE['last_result']}
            self._send(200, payload)
        elif p == '/config':
            self._send(200, load_config())
        else:
            self._send(404, {'ok': False, 'error': 'not found'})

    def do_POST(self):
        p = urllib.parse.urlsplit(self.path).path
        b = self._body()
        try:
            if p == '/push':
                items = b.get('urls') or []
                page = b.get('pageUrl') or ''
                d = STATE['save_dir']
                log('收到浏览器推送：%d 张图片，来源 %s' % (len(items), (page or '?')[:70]), 'ok')
                ok = start_job([], items, d, norm_opts(load_config()), page)
                self._send(200, {'ok': ok, 'queued': len(items), 'error': None if ok else '已有任务在运行'})
            elif p == '/start':
                urls = [str(x).strip() for x in (b.get('urls') or []) if str(x).strip()]
                d = str(b.get('save_dir') or '').strip() or DEFAULT_DIR
                o = norm_opts(b.get('opts'))
                cfg = dict(b.get('opts') or {})
                cfg['save_dir'] = d
                save_config(cfg)
                if not urls:
                    log('未填网址，等待浏览器扩展推送…', 'info')
                    self._send(200, {'ok': True, 'waiting': True})
                    return
                ok = start_job(urls, [], d, o, urls[0] if urls else '')
                self._send(200, {'ok': ok, 'error': None if ok else '已有任务在运行'})
            elif p == '/stop':
                STATE['stop'].set()
                log('已请求停止…', 'warn')
                self._send(200, {'ok': True})
            elif p == '/clear':
                with LOCK:
                    STATE['logs'] = []
                self._send(200, {'ok': True})
            elif p == '/config':
                cfg = dict(b)
                save_config(cfg)
                d = str(cfg.get('save_dir') or '').strip()
                if d:
                    with LOCK:
                        STATE['save_dir'] = d
                self._send(200, {'ok': True})
            elif p == '/pickdir':
                d = pick_folder_dialog()
                self._send(200, {'ok': bool(d), 'dir': d or ''})
            elif p == '/openfolder':
                d = str(b.get('dir') or STATE['save_dir'] or DEFAULT_DIR)
                os.makedirs(d, exist_ok=True)
                os.startfile(d)
                self._send(200, {'ok': True})
            elif p == '/openext':
                d = os.path.join(app_dir(), 'browser-extension')
                if os.path.isdir(d):
                    os.startfile(d)
                    self._send(200, {'ok': True})
                else:
                    self._send(200, {'ok': False, 'error': '未找到 browser-extension 文件夹'})
            else:
                self._send(404, {'ok': False, 'error': 'not found'})
        except Exception as e:
            log('请求处理失败：' + str(e), 'err')
            self._send(500, {'ok': False, 'error': str(e)})


class Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    cfg = load_config()
    STATE['save_dir'] = cfg.get('save_dir') or DEFAULT_DIR
    os.makedirs(STATE['save_dir'], exist_ok=True)
    url = 'http://127.0.0.1:%d/' % PORT
    print('=' * 58)
    print('  ' + APP_NAME + '  v1.0')
    print('  本地包体   : ' + app_dir())
    print('  默认保存   : ' + STATE['save_dir'])
    print('  操作界面   : ' + url)
    print('  扩展接收   : http://127.0.0.1:%d/push' % PORT)
    print('-' * 58)
    print('  浏览器界面已自动打开；关闭本窗口即退出程序。')
    print('=' * 58, flush=True)

    try:
        srv = Server(('127.0.0.1', PORT), Handler)
    except Exception as e:
        print('端口 %d 启动失败：%s' % (PORT, e), flush=True)
        input('按回车退出…')
        return
    log('服务已启动，等待任务…', 'ok')
    if '--no-browser' not in sys.argv:
        try:
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        try:
            with open(os.path.join(app_dir(), 'error.log'), 'a', encoding='utf-8') as f:
                f.write(traceback.format_exc() + '\n')
        except Exception:
            pass
        traceback.print_exc()
        try:
            input('按回车退出…')
        except Exception:
            pass
