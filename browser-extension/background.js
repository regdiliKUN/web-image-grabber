const IMG_LIKE = /\.(?:jpe?g|png|gif|webp|avif|bmp|ico|svg|apng|jxl|heic|heif|tiff?)(?:[?#]|$)/i;
const cache = new Map();
const bucketLoading = new Map();
const timers = new Map();
const tabUrls = new Map();
const MAX_PER_TAB = 4000;

const skey = (tabId) => 'netimg_' + tabId;

async function bucket(tabId) {
  if (cache.has(tabId)) return cache.get(tabId);
  if (bucketLoading.has(tabId)) return bucketLoading.get(tabId);

  const promise = (async () => {
    let arr = [];
    try {
      const got = await chrome.storage.session.get(skey(tabId));
      if (Array.isArray(got[skey(tabId)])) arr = got[skey(tabId)];
    } catch (e) {}
    const m = new Map(arr.map((x) => [x.url, x]));
    cache.set(tabId, m);
    bucketLoading.delete(tabId);
    return m;
  })();

  bucketLoading.set(tabId, promise);
  return promise;
}

function persist(tabId) {
  if (timers.has(tabId)) return;
  timers.set(tabId, setTimeout(async () => {
    timers.delete(tabId);
    try {
      const b = await bucket(tabId);
      await chrome.storage.session.set({ [skey(tabId)]: [...b.values()] });
    } catch (e) {}
  }, 500));
}

async function clearTabBucket(tabId) {
  if (timers.has(tabId)) {
    clearTimeout(timers.get(tabId));
    timers.delete(tabId);
  }
  cache.set(tabId, new Map());
  try {
    await chrome.storage.session.remove(skey(tabId));
  } catch (e) {}
}

function arrayToB64(ab) {
  const bytes = new Uint8Array(ab);
  const CH = 0x8000;
  let s = '';
  for (let i = 0; i < bytes.length; i += CH) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
  }
  return btoa(s);
}

// 顶层页面跳转（非仅 hash 变化）时自动清空旧页面的网络图片记录
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (!changeInfo.url) return;
  const newBase = changeInfo.url.split('#')[0];
  const oldBase = tabUrls.get(tabId);
  if (oldBase && oldBase !== newBase) {
    clearTabBucket(tabId);
  }
  tabUrls.set(tabId, newBase);
});

// 网络层：所有实际加载过的图片响应，不限格式、不依赖 DOM 结构
chrome.webRequest.onResponseStarted.addListener(
  async (d) => {
    if (d.tabId < 0 || !d.url || d.url.startsWith('data:')) return;
    const h = (d.responseHeaders || []).find((x) => x.name.toLowerCase() === 'content-type');
    const mime = h ? String(h.value).split(';')[0].trim().toLowerCase() : '';
    const isImage = d.type === 'image' || /^image\//.test(mime) || IMG_LIKE.test(d.url.split('?')[0]);
    if (!isImage) return;

    const b = await bucket(d.tabId);
    if (b.has(d.url)) return;
    b.set(d.url, { url: d.url, mime, from: 'network', ts: Date.now() });
    if (b.size > MAX_PER_TAB) b.delete(b.keys().next().value);
    persist(d.tabId);
  },
  { urls: ['<all_urls>'], types: ['image', 'other', 'xmlhttprequest'] },
  ['responseHeaders']
);

chrome.runtime.onMessage.addListener((msg, sender, send) => {
  (async () => {
    try {
      if (msg.type === 'getNetwork') {
        const b = await bucket(msg.tabId);
        send({ ok: true, items: [...b.values()] });
      } else if (msg.type === 'clearNetwork') {
        await clearTabBucket(msg.tabId);
        send({ ok: true });
      } else if (msg.type === 'fetchImage') {
        const r = await fetch(msg.url, { credentials: 'include' });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const ab = await r.arrayBuffer();
        send({
          ok: true,
          b64: arrayToB64(ab),
          mime: (r.headers.get('content-type') || '').split(';')[0].trim()
        });
      } else if (msg.type === 'downloadFallback') {
        const results = [];
        for (const it of msg.items) {
          try {
            await chrome.downloads.download({
              url: it.url,
              filename: (msg.subdir ? msg.subdir + '/' : '') + it.filename,
              conflictAction: 'uniquify'
            });
            results.push({ url: it.url, ok: true });
          } catch (e) {
            results.push({ url: it.url, ok: false, error: String(e) });
          }
        }
        send({ ok: true, results });
      } else {
        send({ ok: false, error: 'unknown message type' });
      }
    } catch (e) {
      send({ ok: false, error: String(e) });
    }
  })();
  return true;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  if (timers.has(tabId)) {
    clearTimeout(timers.get(tabId));
    timers.delete(tabId);
  }
  cache.delete(tabId);
  bucketLoading.delete(tabId);
  tabUrls.delete(tabId);
  try {
    const p = chrome.storage.session.remove(skey(tabId));
    if (p && typeof p.catch === 'function') p.catch(() => {});
  } catch (e) {}
});

let lastSourceTab = null;

chrome.action.onClicked.addListener(async (tab) => {
  const appUrl = chrome.runtime.getURL('app.html');
  const extRoot = chrome.runtime.getURL('');
  let sourceId = tab.id;
  if (tab.url && tab.url.startsWith(extRoot)) {
    sourceId = lastSourceTab;
  } else {
    lastSourceTab = tab.id;
  }
  const target = appUrl + (sourceId ? '?tab=' + sourceId : '');
  const all = await chrome.tabs.query({});
  const existing = all.find((t) => t.url && t.url.startsWith(appUrl));
  if (existing) {
    await chrome.tabs.update(existing.id, { active: true, url: target });
  } else {
    await chrome.tabs.create({ url: target });
  }
});
