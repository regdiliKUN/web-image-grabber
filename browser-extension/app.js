const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const CHUNK_SIZE = 120;

const S = {
  tabId: null,
  tabUrl: '',
  items: [],
  filtered: [],
  renderedCount: 0,
  dirHandle: null,
  dirName: '',
  busy: false,
  cancel: false,
  cardMap: new Map(),
  lbItem: null,
  batchSeq: 0,
  observer: null
};

const MIME_EXT = {
  'image/jpeg': '.jpg', 'image/jpg': '.jpg', 'image/png': '.png', 'image/gif': '.gif',
  'image/webp': '.webp', 'image/avif': '.avif', 'image/svg+xml': '.svg',
  'image/x-icon': '.ico', 'image/vnd.microsoft.icon': '.ico', 'image/bmp': '.bmp',
  'image/x-ms-bmp': '.bmp', 'image/apng': '.png', 'image/heic': '.heic',
  'image/heif': '.heif', 'image/tiff': '.tiff', 'image/jxl': '.jxl'
};

function log(msg, kind) {
  const box = $('#log');
  const d = document.createElement('div');
  if (kind) d.className = kind;
  d.textContent = '[' + new Date().toLocaleTimeString('zh-CN', { hour12: false }) + '] ' + msg;
  box.appendChild(d);
  box.scrollTop = box.scrollHeight;
  while (box.children.length > 300) box.removeChild(box.firstChild);
}

function send(msg) {
  return new Promise((res) => {
    chrome.runtime.sendMessage(msg, (r) => {
      const err = chrome.runtime.lastError ? chrome.runtime.lastError.message : '';
      res(r || { ok: false, error: '后台无响应：' + err });
    });
  });
}

function setBusy(v) {
  S.busy = v;
  if (!v) S.cancel = false;
  ['#btnScan', '#btnDeep', '#btnDownload', '#btnPush', '#btnPick', '#btnReauth', '#btnClearNet', '#btnRetry'].forEach((s) => {
    const el = $(s);
    if (el) el.disabled = v;
  });
  $('#btnCancel').style.display = v ? 'inline-block' : 'none';
}

function progress(cur, total, text) {
  $('#progFill').style.width = total ? Math.round((cur / total) * 100) + '%' : '0%';
  $('#progText').textContent = text || (cur + ' / ' + total);
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

function safeName(s) {
  return String(s || 'image').replace(/[\\/:*?"<>|\x00-\x1f]/g, '_').replace(/\s+/g, '_').slice(0, 80) || 'image';
}

function baseName(u) {
  try {
    const x = new URL(u);
    const last = x.pathname.split('/').pop();
    return last ? decodeURIComponent(last).replace(/\.[a-z0-9]{2,5}$/i, '') : 'image';
  } catch (e) { return 'image'; }
}

function extFrom(mime, url) {
  if (mime && MIME_EXT[String(mime).toLowerCase()]) return MIME_EXT[String(mime).toLowerCase()];
  const m = /\.([a-z0-9]{2,5})(?:[?#]|$)/i.exec(String(url || ''));
  return m ? '.' + m[1].toLowerCase() : '.img';
}

const PRIMARY_FMTS = new Set(['jpg', 'png', 'webp', 'gif', 'svg', 'avif']);

function fmtOf(it) {
  const s = (it.mime || '') + ' ' + (it.url || '');
  if (/image\/jpeg|\.jpe?g/i.test(s)) return 'jpg';
  if (/image\/apng|\.apng/i.test(s)) return 'apng';
  if (/image\/png|\.png/i.test(s)) return 'png';
  if (/image\/webp|\.webp/i.test(s)) return 'webp';
  if (/image\/gif|\.gif/i.test(s)) return 'gif';
  if (/image\/svg|\.svg/i.test(s)) return 'svg';
  if (/image\/avif|\.avif/i.test(s)) return 'avif';
  if (/image\/jxl|\.jxl/i.test(s)) return 'jxl';
  if (/image\/heic|\.heic/i.test(s)) return 'heic';
  if (/image\/heif|\.heif/i.test(s)) return 'heif';
  if (/x-icon|\.ico/i.test(s)) return 'ico';
  if (/image\/bmp|ms-bmp|\.bmp/i.test(s)) return 'bmp';
  if (/image\/tiff|\.tiff?/i.test(s)) return 'tiff';
  return 'other';
}

function sniff(u8) {
  const h = u8.slice(0, 16);
  const a = (i, n) => String.fromCharCode.apply(null, h.subarray(i, i + n));
  if (h[0] === 0xff && h[1] === 0xd8 && h[2] === 0xff) return 'image/jpeg';
  if (h[0] === 0x89 && a(1, 3) === 'PNG') return 'image/png';
  if (a(0, 3) === 'GIF') return 'image/gif';
  if (a(0, 4) === 'RIFF' && a(8, 4) === 'WEBP') return 'image/webp';
  if (a(4, 4) === 'ftyp' && /avif|avis/.test(a(8, 4))) return 'image/avif';
  if (a(0, 4) === 'BM') return 'image/bmp';
  const head = a(0, Math.min(16, h.length)).trim().toLowerCase();
  if (head.indexOf('<svg') >= 0 || head.indexOf('<?xml') >= 0) return 'image/svg+xml';
  return '';
}

function b64ToBytes(b64) {
  const s = atob(String(b64).replace(/\s/g, ''));
  const u = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i);
  return u;
}

/* ---------- 持久化 ---------- */
function openDB() {
  return new Promise((res, rej) => {
    const r = indexedDB.open('img-grabber', 1);
    r.onupgradeneeded = () => { if (!r.result.objectStoreNames.contains('kv')) r.result.createObjectStore('kv'); };
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
}

async function dbGet(k) {
  try {
    const db = await openDB();
    return await new Promise((res, rej) => {
      const q = db.transaction('kv', 'readonly').objectStore('kv').get(k);
      q.onsuccess = () => res(q.result);
      q.onerror = () => rej(q.error);
    });
  } catch (e) { return null; }
}

async function dbSet(k, v) {
  try {
    const db = await openDB();
    return await new Promise((res, rej) => {
      const t = db.transaction('kv', 'readwrite');
      t.objectStore('kv').put(v, k);
      t.oncomplete = () => res(true);
      t.onerror = () => rej(t.error);
    });
  } catch (e) { return false; }
}

function prefs() {
  return {
    autoScroll: $('#autoScroll').checked,
    upscale: $('#upscale').checked,
    autoDropInvalid: $('#autoDropInvalid').checked,
    unknown: $('#unknown').checked,
    mode: ($('input[name=mode]:checked') || {}).value || 'fs',
    concurrency: $('#concurrency').value,
    subdirMode: $('#subdirMode').value,
    subdirCustom: $('#subdirCustom').value,
    fmt: $('#fmt').value,
    minW: $('#minW').value,
    minH: $('#minH').value,
    sortBy: $('#sortBy').value,
    kw: $('#kw').value,
    dirName: S.dirName
  };
}

async function savePrefs() { await chrome.storage.local.set({ prefs: prefs() }); }

async function loadPrefs() {
  let p = {};
  try { p = (await chrome.storage.local.get('prefs')).prefs || {}; } catch (e) {}
  if (typeof p.autoScroll === 'boolean') $('#autoScroll').checked = p.autoScroll;
  if (typeof p.upscale === 'boolean') $('#upscale').checked = p.upscale;
  if (typeof p.autoDropInvalid === 'boolean') $('#autoDropInvalid').checked = p.autoDropInvalid;
  if (typeof p.unknown === 'boolean') $('#unknown').checked = p.unknown;
  if (p.mode) { const r = $('input[name=mode][value="' + p.mode + '"]'); if (r) r.checked = true; }
  if (p.concurrency) $('#concurrency').value = p.concurrency;
  if (p.subdirMode) $('#subdirMode').value = p.subdirMode;
  if (p.subdirCustom) $('#subdirCustom').value = p.subdirCustom;
  if (p.fmt) $('#fmt').value = p.fmt;
  if (p.minW != null) $('#minW').value = p.minW;
  if (p.minH != null) $('#minH').value = p.minH;
  if (p.sortBy) $('#sortBy').value = p.sortBy;
  if (p.kw) $('#kw').value = p.kw;
  S.dirName = p.dirName || '';
}

function renderDir() {
  const el = $('#dirName');
  if (S.dirHandle) {
    el.className = 'dir';
    el.textContent = '已选文件夹：' + S.dirName + '（重开浏览器后点「重新授权文件夹」即可继续用）';
  } else {
    el.className = 'dir off';
    el.textContent = S.dirName ? '上次使用：' + S.dirName + ' — 需要重新授权' : '尚未选择文件夹';
  }
}

/* ---------- 标签页与桌面端检测 ---------- */
async function fillTabs() {
  const tabs = await chrome.tabs.query({});
  const sel = $('#tabSel');
  const usable = tabs.filter((t) => /^https?:/i.test(t.url || ''));
  sel.innerHTML = '';
  usable.forEach((t) => {
    const o = document.createElement('option');
    o.value = String(t.id);
    o.textContent = (t.title || t.url).slice(0, 90);
    sel.appendChild(o);
  });
  if (!usable.length) {
    const o = document.createElement('option');
    o.textContent = '没有可用网页标签页';
    sel.appendChild(o);
    S.tabId = null;
  } else {
    const wanted = S.tabId && usable.some((t) => t.id === S.tabId);
    S.tabId = wanted ? S.tabId : usable.find((t) => t.active)?.id || usable[0].id;
    sel.value = String(S.tabId);
    const t = usable.find((x) => x.id === S.tabId);
    S.tabUrl = t ? t.url : '';
  }
}

async function pingDesktop(quiet) {
  const badge = $('#deskStatus');
  const text = $('#deskText');
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 1500);
    const r = await fetch('http://127.0.0.1:17890/ping', { signal: ctrl.signal });
    clearTimeout(timer);
    const j = await r.json();
    badge.className = 'desk-badge on';
    text.textContent = j.pending_jobs ? `桌面程序已连接 (排队 ${j.pending_jobs})` : '桌面程序已连接';
    if (!quiet) log('桌面程序已连接，可直接用「发送到桌面程序」', 'ok');
    return true;
  } catch (e) {
    badge.className = 'desk-badge off';
    text.textContent = '桌面程序未运行';
    if (!quiet) log('桌面程序未运行；如需一键发到本地文件夹，请先启动 ImageGrabber.exe', 'warn');
    return false;
  }
}

/* ---------- 注入目标页面的扫描函数（必须完全自包含） ---------- */
function scanPage(opts) {
  const IMG_EXT = /\.(?:jpe?g|png|gif|webp|avif|bmp|ico|svg|apng|jxl|heic|heif|tiff?)(?:[?#]|$)/i;
  const SKIP_STYLE_TAG = /^(?:SCRIPT|STYLE|META|LINK|NOSCRIPT|HEAD|TITLE|BR|HR|PATH|CIRCLE|RECT|POLYGON|LINE)$/i;
  const SKIP_ATTR = /^(?:class|id|role|tabindex|aria-.*|onclick|onchange|oninput|alt|title|placeholder)$/i;
  const out = [];
  const seen = new Set();
  const base = document.baseURI;

  const clean = (u) => {
    if (u == null) return null;
    let s = String(u).trim().replace(/^["'`]+|["'`]+$/g, '');
    if (!s) return null;
    s = s.replace(/\\\//g, '/').replace(/&amp;/g, '&');
    if (s.startsWith('data:image/') || s.startsWith('blob:')) return s;
    if (s.startsWith('//')) s = location.protocol + s;
    try { return new URL(s, base).href; } catch (e) { return /^https?:/i.test(s) ? s : null; }
  };

  const push = (u, source, isCand) => {
    const c = clean(u);
    if (!c || seen.has(c)) return;
    seen.add(c);
    out.push({ url: c, source: source, mime: '', isCand: Boolean(isCand) });
  };

  const looksImage = (s) => {
    if (!s) return false;
    if (/^data:image\//i.test(s) || /^blob:/i.test(s)) return true;
    if (IMG_EXT.test(s)) return true;
    if (/\/(?:image|img|photo|pic|upload|media|thumb|file)s?\//i.test(s)) return true;
    if (/[?&](?:image|img|file|url|src|pic)=/i.test(s)) return true;
    return false;
  };

  const upscale = (u) => {
    const res = [];
    if (!u || u.startsWith('data:') || u.startsWith('blob:')) return res;
    let c;
    try { c = new URL(u, base); } catch (e) { return res; }
    if (!/^https?:$/.test(c.protocol)) return res;
    const em = c.pathname.match(/\.[a-z0-9]{2,5}$/i);
    if (em) {
      const ext = em[0];
      const stem = c.pathname.slice(0, -ext.length);
      const cleaned = stem
        .replace(/[-_]\d{2,4}x\d{2,4}$/i, '')
        .replace(/[-_@](?:thumb|thumbnail|small|mini|s\d{2,4}|m\d{2,4}|l\d{2,4}|q\d{2,3})$/i, '')
        .replace(/[-_]\d{2,4}w$/i, '')
        .replace(/[-_]+$/, '');
      if (cleaned !== stem && cleaned) res.push(c.origin + cleaned + ext + c.search);
    }
    const q = new URLSearchParams(c.search);
    let changed = false;
    ['w', 'width', 'h', 'height', 'size', 's', 'q', 'quality', 'resize', 'thumb', 'x-oss-process', 'imageMogr2', 'imageView2', 'spec'].forEach((k) => {
      if (q.has(k)) { q.delete(k); changed = true; }
    });
    if (changed) {
      const qs = q.toString();
      res.push(c.origin + c.pathname + (qs ? '?' + qs : ''));
    }
    const seg = c.pathname.replace(/\/(?:thumb|thumbnail|small|resize|_\d+x\d+|s\d+)\//gi, '/');
    if (seg !== c.pathname) res.push(c.origin + seg + c.search);
    return res;
  };

  const extractUrls = (text, source) => {
    if (!text || text.indexOf('url(') < 0) return;
    const re = /url\(\s*(['"]?)([^'")]+)\1\s*\)/gi;
    let m;
    while ((m = re.exec(text))) push(m[2], source, false);
  };

  const attrValue = (name, val, source) => {
    if (!val || val.length > 4000 || SKIP_ATTR.test(name)) return;
    const v = val.trim();
    if (/srcset/i.test(name)) {
      v.split(',').forEach((part) => {
        const u = part.trim().split(/\s+/)[0];
        if (u) {
          push(u, 'srcset', false);
          if (opts.upscale) upscale(u).forEach((x) => push(x, '原图候选', true));
        }
      });
      return;
    }
    if (/url\(/i.test(v)) { extractUrls(v, 'style'); return; }
    if (/^data:image\//i.test(v)) { push(v, 'inline', false); return; }
    if (looksImage(v)) {
      push(v, source || 'attr', false);
      if (opts.upscale) upscale(v).forEach((x) => push(x, '原图候选', true));
    }
  };

  const MEDIA_TAG = /^(?:img|image|source|input|video|audio|picture|embed|object)$/i;
  let n = 0;

  const walk = (root, depth) => {
    if (depth > 3) return;
    let all;
    try { all = root.querySelectorAll('*'); } catch (e) { return; }
    for (const el of all) {
      n++;
      if (n > 20000) return;
      if (MEDIA_TAG.test(el.tagName)) {
        ['src', 'href', 'xlink:href', 'poster', 'data', 'srcset', 'data-srcset'].forEach((a) => {
          const v = el.getAttribute(a);
          if (v) attrValue(a, v, '元素');
        });
        if (el.currentSrc) push(el.currentSrc, '元素', false);
      }
      if (el.tagName === 'A' && looksImage(el.getAttribute('href') || '')) push(el.getAttribute('href'), '原图链接', false);
      for (const a of el.attributes) attrValue(a.name, a.value, '属性');

      if (!SKIP_STYLE_TAG.test(el.tagName)) {
        try {
          const cs = getComputedStyle(el);
          if (cs.backgroundImage && cs.backgroundImage !== 'none') extractUrls(cs.backgroundImage, 'CSS背景');
          if (el.className || el.id) {
            const pb = getComputedStyle(el, '::before');
            if (pb.content !== 'none' && pb.backgroundImage && pb.backgroundImage !== 'none') extractUrls(pb.backgroundImage, 'CSS伪元素');
            const pa = getComputedStyle(el, '::after');
            if (pa.content !== 'none' && pa.backgroundImage && pa.backgroundImage !== 'none') extractUrls(pa.backgroundImage, 'CSS伪元素');
          }
        } catch (e) {}
      }
      if (el.shadowRoot) walk(el.shadowRoot, depth + 1);
    }
  };

  walk(document, 0);
  try {
    document.querySelectorAll('iframe').forEach((f) => {
      try { if (f.contentDocument) walk(f.contentDocument, 1); } catch (e) {}
    });
  } catch (e) {}

  try {
    performance.getEntriesByType('resource').forEach((e) => {
      if (e.name && looksImage(e.name)) push(e.name, '资源记录', false);
    });
  } catch (e) {}

  try {
    const html = document.documentElement.outerHTML.replace(/\\\//g, '/').replace(/&amp;/g, '&');
    const re = /(?:https?:)?\/\/[^\s"'<>(){}|\\^`\[\]]+?\.(?:jpe?g|png|gif|webp|avif|bmp|ico|svg|apng|jxl|heic|heif|tiff?)(?:\?[^\s"'<>(){}|\\^`\[\]]*)?/gi;
    let m;
    let count = 0;
    while ((m = re.exec(html)) && count < 2500) {
      push(m[0], '源码', false);
      if (opts.upscale) upscale(m[0]).forEach((x) => push(x, '原图候选', true));
      count++;
    }
    const dre = /data:image\/[a-z0-9.+-]+;base64,[a-z0-9+/=]+/gi;
    while ((m = dre.exec(html)) && count < 2500) { push(m[0], 'inline', false); count++; }
  } catch (e) {}

  return { items: out.slice(0, 4000), title: document.title, url: location.href };
}

/* ---------- 自动滚动 ---------- */
async function injectScroll(tabId) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      func: async () => {
        const step = Math.max(250, Math.floor(window.innerHeight * 0.85));
        let lastH = -1, stable = 0;
        for (let i = 0; i < 150; i++) {
          window.scrollBy(0, step);
          await new Promise((r) => setTimeout(r, 200));
          const h = document.documentElement.scrollHeight;
          if (h === lastH) stable++; else { stable = 0; lastH = h; }
          if (window.scrollY + window.innerHeight >= h - 3 && stable >= 3) break;
        }
        window.scrollTo(0, 0);
        await new Promise((r) => setTimeout(r, 400));
        return true;
      }
    });
  } catch (e) { log('自动滚动失败：' + e, 'warn'); }
}

function waitComplete(tabId, ms) {
  return new Promise((resolve) => {
    let done = false;
    const fin = () => { if (done) return; done = true; chrome.tabs.onUpdated.removeListener(l); resolve(); };
    const l = (id, info) => { if (id === tabId && info.status === 'complete') fin(); };
    chrome.tabs.onUpdated.addListener(l);
    setTimeout(fin, ms || 20000);
  });
}

/* ---------- 扫描与合并 ---------- */
function normalizeUrl(raw, pageUrl) {
  if (!raw) return null;
  let s = String(raw).trim().replace(/^["'`]+|["'`]+$/g, '');
  if (!s || s.length > 8000) return null;
  s = s.replace(/&amp;/g, '&');
  if (s.startsWith('data:image/') || s.startsWith('blob:')) return s;
  try { return new URL(s, pageUrl || location.href).href; } catch (e) { return null; }
}

function guessMime(u) {
  const m = /\.([a-z0-9]{2,5})(?:[?#]|$)/i.exec(String(u || ''));
  if (!m) return '';
  const e = m[1].toLowerCase();
  const map = { jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', gif: 'image/gif', webp: 'image/webp', avif: 'image/avif', svg: 'image/svg+xml', ico: 'image/x-icon', bmp: 'image/bmp', apng: 'image/apng', jxl: 'image/jxl', heic: 'image/heic', heif: 'image/heif', tif: 'image/tiff', tiff: 'image/tiff' };
  return map[e] || '';
}

async function scan(deep) {
  if (S.busy) return;
  if (!S.tabId) { log('请先选择要抓取的网页标签页', 'err'); return; }
  setBusy(true);
  progress(0, 0, '准备中');
  try {
    const tab = await chrome.tabs.get(S.tabId).catch(() => null);
    S.tabUrl = tab && tab.url ? tab.url : S.tabUrl;
    if (!/^https?:/i.test(S.tabUrl)) { log('这个页面不是普通网页（chrome:// 等无法注入脚本）', 'err'); return; }

    if (deep) {
      log('清空网络记录并重新载入页面…');
      await send({ type: 'clearNetwork', tabId: S.tabId });
      const done = waitComplete(S.tabId, 20000);
      await chrome.tabs.reload(S.tabId);
      await done;
      await sleep(1200);
    }
    if ($('#autoScroll').checked) {
      log('自动滚动页面，触发懒加载…');
      progress(0, 0, '滚动中');
      await injectScroll(S.tabId);
    }
    log('读取网络层图片资源…');
    const net = await send({ type: 'getNetwork', tabId: S.tabId });
    log('扫描页面 DOM、CSS、源码与资源记录…');
    let dom = { items: [] };
    try {
      const r = await chrome.scripting.executeScript({
        target: { tabId: S.tabId },
        func: scanPage,
        args: [{ upscale: $('#upscale').checked }]
      });
      dom = (r && r[0] && r[0].result) || { items: [] };
    } catch (e) { log('DOM 扫描失败：' + e, 'warn'); }

    const map = new Map();
    const add = (raw, from, mime, isCand) => {
      const u = normalizeUrl(raw, S.tabUrl);
      if (!u) return;
      if (map.has(u)) {
        const e = map.get(u);
        if (!e.from.includes(from)) e.from.push(from);
        if (mime && !e.mime) e.mime = mime;
        if (!isCand) e.isCand = false;
        return;
      }
      map.set(u, { url: u, mime: mime || guessMime(u), from: [from], checked: true, w: 0, h: 0, isCand: Boolean(isCand), dlStatus: '' });
    };
    ((net && net.items) || []).forEach((x) => add(x.url, '网络', x.mime, false));
    ((dom && dom.items) || []).forEach((x) => add(x.url, x.source || 'DOM', x.mime, x.isCand));

    S.items = Array.from(map.values());
    S.items.forEach((it, i) => { it.id = i + 1; it.fmt = fmtOf(it); });
    S.cardMap.clear();
    S.batchSeq = 0;
    $('#btnRetry').style.display = 'none';
    log('扫描完成：网络层 ' + (((net && net.items) || []).length) + ' 条，DOM/源码 ' + (((dom && dom.items) || []).length) + ' 条，去重后 ' + S.items.length + ' 张候选', 'ok');
    render(true);
    progress(0, 0, '就绪');
  } finally {
    setBusy(false);
  }
}

/* ---------- 分批虚拟渲染、筛选与排序 ---------- */
function matchesFilter(it) {
  const fmt = $('#fmt').value;
  const minW = Number($('#minW').value) || 0;
  const minH = Number($('#minH').value) || 0;
  const kw = $('#kw').value.trim().toLowerCase();

  if (fmt === 'other') {
    if (PRIMARY_FMTS.has(it.fmt)) return false;
  } else if (fmt !== 'all' && it.fmt !== fmt) return false;
  if (minW > 0 && it.w > 0 && it.w < minW) return false;
  if (minH > 0 && it.h > 0 && it.h < minH) return false;
  if (kw && it.url.toLowerCase().indexOf(kw) < 0) return false;
  return true;
}

function visibleItems() {
  const list = S.items.filter(matchesFilter);
  const sortBy = $('#sortBy').value;
  if (sortBy === 'area_desc') {
    list.sort((a, b) => (b.w * b.h) - (a.w * a.h));
  } else if (sortBy === 'w_desc') {
    list.sort((a, b) => b.w - a.w);
  } else if (sortBy === 'fmt') {
    list.sort((a, b) => a.fmt.localeCompare(b.fmt) || a.id - b.id);
  }
  return list;
}

function updateCounts() {
  $('#stat').textContent = '共 ' + S.items.length + ' 张，当前符合 ' + S.filtered.length + ' 张（已渲染 ' + S.renderedCount + '）';
  $('#selCount').textContent = '已选 ' + S.items.filter((i) => i.checked).length + ' 张';
}

function syncCardSelection(it) {
  const card = S.cardMap.get(it.id);
  if (!card) return;
  card.classList.toggle('on', it.checked);
  const cb = card.querySelector('.cb');
  if (cb && cb.checked !== it.checked) cb.checked = it.checked;
}

function syncCardDownloadState(it) {
  const card = S.cardMap.get(it.id);
  if (!card) return;
  card.classList.toggle('dl-ok', it.dlStatus === 'ok');
  card.classList.toggle('dl-fail', it.dlStatus === 'fail');
  let badge = card.querySelector('.badge-stat');
  if (!it.dlStatus) {
    if (badge) badge.remove();
    return;
  }
  if (!badge) {
    badge = document.createElement('span');
    badge.className = 'badge-stat';
    card.querySelector('.thumb').appendChild(badge);
  }
  badge.className = 'badge-stat ' + it.dlStatus;
  badge.textContent = it.dlStatus === 'ok' ? '已保存' : '失败';
}

function buildCard(it) {
  const card = document.createElement('div');
  card.className = 'card' + (it.checked ? ' on' : '');
  card.dataset.id = String(it.id);
  const tag = (it.fmt || '?').toUpperCase();
  card.innerHTML =
    '<input class="cb" type="checkbox" ' + (it.checked ? 'checked' : '') + '>' +
    '<span class="tag">' + esc(tag) + '</span>' +
    '<div class="thumb" title="点击放大预览"><img loading="lazy" alt="" src="' + esc(it.url) + '">' +
    (it.isCand ? '<span class="badge-cand">原图候选</span>' : '') +
    '</div>' +
    '<div class="meta"><b class="dim">' + (it.w ? it.w + ' x ' + it.h : '尺寸读取中') + '</b>' +
    '<span title="' + esc(it.url) + '">' + esc(it.url.length > 90 ? it.url.slice(0, 90) + '…' : it.url) + '</span>' +
    '<span>' + esc(it.from.join(' / ')) + '</span></div>';

  const img = card.querySelector('img');
  img.addEventListener('load', () => {
    it.w = img.naturalWidth;
    it.h = img.naturalHeight;
    card.querySelector('.dim').textContent = it.w + ' x ' + it.h;
    if (!matchesFilter(it)) {
      card.style.display = 'none';
      S.filtered = S.filtered.filter((x) => x.id !== it.id);
      updateCounts();
    }
  });
  img.addEventListener('error', () => {
    const t = card.querySelector('.thumb');
    t.innerHTML = '<span class="noimg">' + (it.isCand ? '原图候选无效' : '预览失败（仍可下载）') + '</span>';
    if (it.isCand && $('#autoDropInvalid').checked && it.checked) {
      it.checked = false;
      syncCardSelection(it);
      updateCounts();
    }
  });

  card.querySelector('.thumb').addEventListener('click', (e) => {
    e.stopPropagation();
    openLightbox(it);
  });
  card.querySelector('.cb').addEventListener('change', (e) => {
    it.checked = e.target.checked;
    card.classList.toggle('on', it.checked);
    updateCounts();
  });
  card.querySelector('.meta').addEventListener('click', () => {
    it.checked = !it.checked;
    syncCardSelection(it);
    updateCounts();
  });
  S.cardMap.set(it.id, card);
  return card;
}

function appendNextChunk(count) {
  if (S.renderedCount >= S.filtered.length) {
    $('#loadSentinel').style.display = 'none';
    return;
  }
  const grid = $('#grid');
  const end = Math.min(S.filtered.length, S.renderedCount + (count || CHUNK_SIZE));
  const frag = document.createDocumentFragment();
  for (let i = S.renderedCount; i < end; i++) {
    const it = S.filtered[i];
    const c = S.cardMap.get(it.id) || buildCard(it);
    c.style.display = '';
    syncCardSelection(it);
    syncCardDownloadState(it);
    frag.appendChild(c);
  }
  grid.appendChild(frag);
  S.renderedCount = end;

  const sentinel = $('#loadSentinel');
  if (S.renderedCount < S.filtered.length) {
    sentinel.style.display = 'block';
    $('#sentinelText').textContent = `已加载 ${S.renderedCount} / ${S.filtered.length} 张，向下滚动继续加载…`;
  } else {
    sentinel.style.display = 'none';
  }
  updateCounts();
}

function render(resetCards) {
  const grid = $('#grid');
  if (resetCards) S.cardMap.clear();
  S.filtered = visibleItems();
  S.renderedCount = 0;
  grid.innerHTML = '';

  $('#empty').style.display = S.filtered.length ? 'none' : 'block';
  $('#empty').textContent = S.items.length ? '没有符合筛选条件的图片' : '点击「扫描当前页」或「刷新并完整抓取」开始';
  $('#loadSentinel').style.display = 'none';

  if (S.filtered.length) {
    appendNextChunk(CHUNK_SIZE);
  } else {
    updateCounts();
  }
}

/* ---------- 灯箱预览 ---------- */
function openLightbox(it) {
  S.lbItem = it;
  $('#lbImg').src = it.url;
  $('#lbInfo').textContent = `${it.fmt.toUpperCase()} · ${it.w ? it.w + 'x' + it.h : '未知尺寸'} · ${it.from.join('/')} · ${it.url}`;
  $('#lbToggle').textContent = it.checked ? '取消选中' : '选中此图';
  $('#lightbox').classList.add('open');
}

function closeLightbox() {
  $('#lightbox').classList.remove('open');
  $('#lbImg').src = '';
  S.lbItem = null;
}

/* ---------- 文件夹 ---------- */
async function pickFolder() {
  if (!window.showDirectoryPicker) {
    log('当前浏览器不支持选择任意文件夹，请改用「浏览器默认下载目录」模式', 'err');
    return;
  }
  try {
    const h = await window.showDirectoryPicker({ mode: 'readwrite' });
    S.dirHandle = h;
    S.dirName = h.name;
    await dbSet('dir', h);
    await chrome.storage.local.set({ dirName: h.name });
    renderDir();
    log('已选择文件夹：' + h.name, 'ok');
  } catch (e) {
    if (e && e.name !== 'AbortError') log('选择文件夹失败：' + e, 'err');
  }
}

async function ensurePermission() {
  if (!S.dirHandle) return false;
  try {
    let p = await S.dirHandle.queryPermission({ mode: 'readwrite' });
    if (p !== 'granted') p = await S.dirHandle.requestPermission({ mode: 'readwrite' });
    if (p !== 'granted') { log('没有获得文件夹写入权限', 'err'); return false; }
    return true;
  } catch (e) { log('权限检查失败：' + e, 'err'); return false; }
}

function subdirName() {
  const mode = $('#subdirMode').value;
  if (mode === 'none') return '';
  if (mode === 'custom') return safeName($('#subdirCustom').value.trim() || '图片');
  let host = '网页';
  try { host = new URL(S.tabUrl).hostname.replace(/^www\./, ''); } catch (e) {}
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return safeName(host + '_' + d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + '_' + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds()));
}

/* ---------- 取图：优先扩展直接 fetch（零 Base64 开销），防盗链时回退目标页上下文 ---------- */
function pageFetch(url) {
  return (async () => {
    try {
      const r = await fetch(url, { credentials: 'include' });
      if (!r.ok) return { ok: false, error: 'HTTP ' + r.status };
      const ab = await r.arrayBuffer();
      const bytes = new Uint8Array(ab);
      const CH = 0x8000;
      let s = '';
      for (let i = 0; i < bytes.length; i += CH) s += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
      return { ok: true, b64: btoa(s), mime: (r.headers.get('content-type') || '').split(';')[0].trim() };
    } catch (e) { return { ok: false, error: String(e) }; }
  })();
}

async function fetchImageData(it) {
  if (it.url.startsWith('data:')) {
    const m = /^data:([^;,]+)?(;base64)?,(.*)$/s.exec(it.url);
    if (!m) throw new Error('无法解析内联图片');
    const u8 = m[2] ? b64ToBytes(m[3]) : new TextEncoder().encode(decodeURIComponent(m[3]));
    return { u8, mime: m[1] || 'image/png' };
  }

  let last = '';
  if (!it.url.startsWith('blob:')) {
    try {
      const r = await fetch(it.url, { credentials: 'include' });
      if (r.ok) {
        const ab = await r.arrayBuffer();
        const u8 = new Uint8Array(ab);
        if (u8.length) {
          return { u8, mime: (r.headers.get('content-type') || '').split(';')[0].trim() };
        }
      } else {
        last = 'HTTP ' + r.status;
      }
    } catch (e) {
      last = String(e);
    }
  }

  try {
    const r = await chrome.scripting.executeScript({ target: { tabId: S.tabId }, func: pageFetch, args: [it.url] });
    const v = r && r[0] && r[0].result;
    if (v && v.ok) return { u8: b64ToBytes(v.b64), mime: v.mime };
    if (v && v.error) last = v.error;
  } catch (e) { last = String(e); }

  const r2 = await send({ type: 'fetchImage', url: it.url });
  if (r2.ok) return { u8: b64ToBytes(r2.b64), mime: r2.mime };
  throw new Error((r2.error || last || '下载失败').slice(0, 160));
}

async function saveOne(it, seq, dir, usedNames) {
  const got = await fetchImageData(it);
  const u8 = got.u8;
  if (!u8.length) throw new Error('空文件');
  let mime = String(got.mime || '').toLowerCase();
  if (!/^image\//.test(mime)) mime = sniff(u8);
  if (!mime && !$('#unknown').checked) throw new Error('返回内容不是图片，已跳过');

  const ext = extFrom(mime, it.url);
  const base = String(seq).padStart(3, '0') + '_' + safeName(baseName(it.url));
  let name = base + ext;
  let dup = 2;
  while (usedNames.has(name)) {
    name = base + '_' + dup + ext;
    dup++;
  }
  usedNames.add(name);

  const fh = await dir.getFileHandle(name, { create: true });
  const w = await fh.createWritable();
  await w.write(new Blob([u8], { type: mime || 'application/octet-stream' }));
  await w.close();
  return name;
}

/* ---------- 下载 ---------- */
async function download(targetItems) {
  if (S.busy) return;
  const sel = Array.isArray(targetItems) ? targetItems : S.items.filter((i) => i.checked);
  if (!sel.length) { log('没有勾选任何图片', 'warn'); return; }
  const mode = ($('input[name=mode]:checked') || {}).value || 'fs';
  const sub = subdirName();

  if (mode === 'fs') {
    if (!S.dirHandle) {
      log('请先点「选择保存文件夹」挑一个目录', 'warn');
      await pickFolder();
      if (!S.dirHandle) return;
    }
    if (!(await ensurePermission())) return;
  } else {
    const skipped = sel.filter((i) => i.url.startsWith('data:') || i.url.startsWith('blob:'));
    if (skipped.length) log('下载目录模式会跳过 ' + skipped.length + ' 张内联/blob 图片，请用「直接写入指定文件夹」模式', 'warn');
  }

  setBusy(true);
  S.cancel = false;
  let ok = 0, fail = 0;
  const failed = [];
  const usedNames = new Set();

  try {
    if (mode === 'fs') {
      let dir = S.dirHandle;
      if (sub) dir = await dir.getDirectoryHandle(sub, { create: true });
      const workersCount = Math.max(1, Math.min(12, Number($('#concurrency').value) || 6));
      log('开始写入：' + S.dirName + (sub ? ' / ' + sub : '') + '，共 ' + sel.length + ' 张（' + workersCount + ' 并发）', 'ok');
      let idx = 0;
      const worker = async () => {
        while (!S.cancel) {
          const i = idx++;
          if (i >= sel.length) break;
          const it = sel[i];
          const seq = ++S.batchSeq;
          try {
            const name = await saveOne(it, seq, dir, usedNames);
            ok++;
            it.dlStatus = 'ok';
            syncCardDownloadState(it);
            log('已保存 ' + name);
          } catch (e) {
            fail++;
            it.dlStatus = 'fail';
            syncCardDownloadState(it);
            failed.push(it);
            log('失败 ' + String(e).slice(0, 150) + ' — ' + it.url.slice(0, 120), 'err');
          }
          progress(ok + fail, sel.length, (ok + fail) + ' / ' + sel.length);
        }
      };
      await Promise.all(Array.from({ length: workersCount }, () => worker()));
    } else {
      const items = sel
        .filter((i) => !i.url.startsWith('data:') && !i.url.startsWith('blob:'))
        .map((it) => {
          const seq = ++S.batchSeq;
          return { url: it.url, filename: String(seq).padStart(3, '0') + '_' + safeName(baseName(it.url)) + extFrom(it.mime, it.url) };
        });
      log('交给浏览器下载：' + items.length + ' 张' + (sub ? '，子目录 ' + sub : ''), 'ok');
      const r = await send({ type: 'downloadFallback', items, subdir: sub });
      if (r && r.ok) {
        (r.results || []).forEach((x) => { if (x.ok) ok++; else { fail++; log('下载失败 ' + x.error, 'err'); } });
      } else { fail += items.length; log('下载调用失败：' + (r && r.error), 'err'); }
      progress(ok + fail, sel.length, (ok + fail) + ' / ' + sel.length);
    }

    if (S.cancel) {
      log('已手动停止：成功 ' + ok + ' 张，失败 ' + fail + ' 张', 'warn');
    } else {
      log('完成：成功 ' + ok + ' 张，失败 ' + fail + ' 张' + (failed.length ? '（可点下方「重试失败项」）' : ''), fail ? 'warn' : 'ok');
    }
    $('#btnRetry').style.display = failed.length ? 'inline-block' : 'none';
  } catch (e) {
    log('保存过程中断：' + e, 'err');
  } finally {
    setBusy(false);
  }
}

/* ---------- 发送到本地桌面程序 ---------- */
async function pushToDesktop() {
  const sel = S.items.filter((i) => i.checked);
  if (!sel.length) { log('没有勾选任何图片', 'warn'); return; }
  const items = [];
  for (const it of sel) {
    if (it.url.startsWith('blob:')) {
      try {
        const g = await fetchImageData(it);
        const CH = 0x8000;
        let s = '';
        for (let i = 0; i < g.u8.length; i += CH) s += String.fromCharCode.apply(null, g.u8.subarray(i, i + CH));
        items.push({ url: '', b64: btoa(s), mime: g.mime });
      } catch (e) { log('blob 图片转换失败：' + e, 'warn'); }
    } else {
      items.push({ url: it.url });
    }
  }
  if (!items.length) return;
  log('正在把 ' + items.length + ' 张图片发送到桌面程序…');
  try {
    const r = await fetch('http://127.0.0.1:17890/push', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls: items, pageUrl: S.tabUrl })
    });
    const j = await r.json();
    if (j.ok) {
      const qMsg = j.pending_jobs > 1 ? `（当前队列第 #${j.pending_jobs} 位）` : '';
      log('已加入桌面端队列：' + j.queued + ' 张' + qMsg + '，切到桌面程序窗口看下载进度', 'ok');
      pingDesktop(true);
    } else {
      log('桌面程序返回错误：' + (j.error || '未知'), 'err');
    }
  } catch (e) {
    log('发送失败：' + e + '。请先运行 ImageGrabber.exe 并保持窗口打开。', 'err');
    pingDesktop(true);
  }
}

/* ---------- 事件绑定 ---------- */
function bind() {
  $('#tabSel').addEventListener('change', async () => {
    S.tabId = Number($('#tabSel').value) || null;
    const t = S.tabId ? await chrome.tabs.get(S.tabId).catch(() => null) : null;
    S.tabUrl = t ? t.url : '';
    log('已切换目标网页：' + (t ? (t.title || t.url).slice(0, 80) : '?'));
  });
  $('#btnRefresh').addEventListener('click', fillTabs);
  $('#btnClearNet').addEventListener('click', async () => {
    if (!S.tabId) return;
    await send({ type: 'clearNetwork', tabId: S.tabId });
    log('已清空当前标签页的网络图片缓存', 'ok');
  });
  $('#deskStatus').addEventListener('click', () => pingDesktop(false));
  $('#btnScan').addEventListener('click', () => scan(false));
  $('#btnDeep').addEventListener('click', () => scan(true));
  $('#btnPick').addEventListener('click', pickFolder);
  $('#btnReauth').addEventListener('click', async () => {
    if (!S.dirHandle) { await pickFolder(); return; }
    await ensurePermission();
    log('文件夹授权状态已刷新', 'ok');
  });

  $('#btnApply').addEventListener('click', () => { render(false); savePrefs(); });
  $('#fmt').addEventListener('change', () => { render(false); savePrefs(); });
  $('#sortBy').addEventListener('change', () => { render(false); savePrefs(); });
  $('#kw').addEventListener('keydown', (e) => { if (e.key === 'Enter') render(false); });
  $('#minW').addEventListener('keydown', (e) => { if (e.key === 'Enter') render(false); });
  $('#minH').addEventListener('keydown', (e) => { if (e.key === 'Enter') render(false); });

  $('#btnAll').addEventListener('click', () => {
    S.filtered.forEach((i) => { i.checked = true; syncCardSelection(i); });
    updateCounts();
  });
  $('#btnNone').addEventListener('click', () => {
    S.items.forEach((i) => { i.checked = false; syncCardSelection(i); });
    updateCounts();
  });
  $('#btnInv').addEventListener('click', () => {
    S.filtered.forEach((i) => { i.checked = !i.checked; syncCardSelection(i); });
    updateCounts();
  });
  $('#btnSelectBig').addEventListener('click', () => {
    const vis = new Set(S.filtered.map((i) => i.id));
    S.items.forEach((i) => {
      i.checked = vis.has(i.id) && (i.w >= 400 || i.h >= 400);
      syncCardSelection(i);
    });
    updateCounts();
  });
  $('#btnLinks').addEventListener('click', async () => {
    const urls = S.items.filter((i) => i.checked).map((i) => i.url).join('\n');
    try { await navigator.clipboard.writeText(urls); log('已复制 ' + S.items.filter((i) => i.checked).length + ' 条链接', 'ok'); }
    catch (e) { log('复制失败：' + e, 'err'); }
  });

  $('#btnLoadAll').addEventListener('click', () => appendNextChunk(S.filtered.length));
  $('#btnDownload').addEventListener('click', () => download());
  $('#btnRetry').addEventListener('click', () => {
    const failed = S.items.filter((i) => i.dlStatus === 'fail');
    if (failed.length) download(failed);
  });
  $('#btnCancel').addEventListener('click', () => { S.cancel = true; log('正在停止下载…', 'warn'); });
  $('#btnPush').addEventListener('click', pushToDesktop);
  $$('input, select').forEach((el) => el.addEventListener('change', savePrefs));

  // 滚动哨兵自动分批加载
  S.observer = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting) && S.renderedCount < S.filtered.length) {
      appendNextChunk(CHUNK_SIZE);
    }
  }, { rootMargin: '250px' });
  S.observer.observe($('#loadSentinel'));

  // 灯箱事件
  $('#lbClose').addEventListener('click', closeLightbox);
  $('#lightbox').addEventListener('click', (e) => { if (e.target.id === 'lightbox') closeLightbox(); });
  $('#lbToggle').addEventListener('click', () => {
    if (!S.lbItem) return;
    S.lbItem.checked = !S.lbItem.checked;
    syncCardSelection(S.lbItem);
    updateCounts();
    $('#lbToggle').textContent = S.lbItem.checked ? '取消选中' : '选中此图';
  });
  $('#lbOpen').addEventListener('click', () => { if (S.lbItem) window.open(S.lbItem.url, '_blank'); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeLightbox(); });
}

async function init() {
  const p = new URLSearchParams(location.search);
  S.tabId = Number(p.get('tab')) || null;
  await loadPrefs();
  const h = await dbGet('dir');
  if (h) S.dirHandle = h;
  await fillTabs();
  bind();
  renderDir();
  render(true);
  log('就绪。选择目标网页后点「扫描当前页」；想抓得最全用「刷新并完整抓取」。', 'ok');
  pingDesktop(false);
  if (S.dirHandle) {
    const perm = await S.dirHandle.queryPermission({ mode: 'readwrite' }).catch(() => 'prompt');
    if (perm !== 'granted') log('检测到上次使用的文件夹，请点「重新授权文件夹」后即可继续写入。', 'warn');
  }
}

if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.id) {
  init();
} else {
  const box = $('#log');
  if (box) box.textContent = '这是界面预览。把本文件夹加载为浏览器扩展后，从工具栏图标打开即可正常使用。';
  const pt = $('#progText');
  if (pt) pt.textContent = '预览模式';
}
