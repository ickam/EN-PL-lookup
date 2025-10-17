# app/wiki_diki.py
from __future__ import annotations

import asyncio
import re
import time
from typing import Dict, List, Optional, Tuple

import httpx
from lxml import html

USER_AGENT = "en2pl-web/1.3 (+https://localhost) httpx"
HTTP_TIMEOUT = 5.0
CACHE_TTL = 60 * 60 * 6  # 6 hours

# ---------------- Shared async client ----------------

_client: httpx.AsyncClient | None = None

def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "pl,en;q=0.9",
            },
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
        )
    return _client

async def _aget(url: str, params: dict | None = None) -> httpx.Response:
    r = await get_client().get(url, params=params)
    r.raise_for_status()
    return r

# ---------------- Tiny TTL cache ---------------------

_cache: dict[Tuple[str, Tuple], Tuple[float, object]] = {}

def _akey(name: str, *parts) -> Tuple[str, Tuple]:
    return (name, parts)

def _cache_get(name: str, *parts):
    k = _akey(name, *parts)
    v = _cache.get(k)
    if not v:
        return None
    ts, data = v
    if time.time() - ts > CACHE_TTL:
        _cache.pop(k, None)
        return None
    return data

def _cache_set(name: str, value, *parts):
    k = _akey(name, *parts)
    _cache[k] = (time.time(), value)

# ---------------- Helpers ---------------------------

def _clean_text(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s).strip(" ,;:–—-")
    return s

def _strip_parentheticals(s: str) -> str:
    return re.sub(r"\([^)]*\)", "", s).strip()

# ---------------- Wikipedia / Wikidata ---------------

async def resolve_en_title(term_or_url: str) -> Optional[str]:
    key = ("resolve_en_title", term_or_url)
    cached = _cache_get(*key)
    if cached is not None:
        return cached

    s = (term_or_url or "").strip()
    m = re.match(r"^https?://(en\.)?wikipedia\.org/wiki/([^#\?]+)", s)
    if m:
        raw = m.group(2)
        title = raw.replace("_", " ")
        title = re.sub(r"%28", "(", title)
        title = re.sub(r"%29", ")", title)
        _cache_set(*key, value=title)
        return title

    if not s:
        _cache_set(*key, value=None)
        return None

    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "list": "search",
        "srsearch": s,
        "srlimit": 1,
    }
    try:
        resp = (await _aget("https://en.wikipedia.org/w/api.php", params=params)).json()
        title = resp["query"]["search"][0]["title"]
    except Exception:
        title = None

    _cache_set(*key, value=title)
    return title

async def english_to_polish_wikipedia(en_title: str) -> Optional[Dict[str, str]]:
    key = ("english_to_polish_wikipedia", en_title)
    cached = _cache_get(*key)
    if cached is not None:
        return cached

    if not en_title:
        _cache_set(*key, value=None)
        return None

    # 1) Direct langlinks
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "prop": "langlinks",
        "titles": en_title,
        "lllang": "pl",
        "llprop": "url",
    }
    try:
        resp = (await _aget("https://en.wikipedia.org/w/api.php", params=params)).json()
        page = resp["query"]["pages"][0]
        ll = page.get("langlinks", [])
        if ll:
            out = {"en_title": en_title, "pl_title": ll[0]["title"], "pl_url": ll[0]["url"]}
            _cache_set(*key, value=out)
            return out
    except Exception:
        pass

    # 2) Via Wikidata
    try:
        pp_params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "pageprops",
            "titles": en_title,
        }
        pp = (await _aget("https://en.wikipedia.org/w/api.php", params=pp_params)).json()
        qid = pp["query"]["pages"][0]["pageprops"]["wikibase_item"]

        wd_params = {
            "action": "wbgetentities",
            "format": "json",
            "ids": qid,
            "props": "sitelinks/urls",
            "sitefilter": "plwiki",
        }
        wd = (await _aget("https://www.wikidata.org/w/api.php", params=wd_params)).json()
        pl = wd["entities"][qid]["sitelinks"]["plwiki"]
        out = {"en_title": en_title, "pl_title": pl["title"], "pl_url": pl["url"]}
        _cache_set(*key, value=out)
        return out
    except Exception:
        _cache_set(*key, value=None)
        return None

# ---------------- Diki (UNLIMITED results) ----------

async def diki_lookup(english_term: str) -> List[str]:
    key = ("diki_lookup", english_term)
    cached = _cache_get(*key)
    if cached is not None:
        return cached

    english_term = (english_term or "").strip()
    if not english_term:
        _cache_set(*key, value=[])
        return []

    url = "https://www.diki.pl/slownik-angielskiego"
    try:
        r = await _aget(url, params={"q": english_term})
    except Exception:
        _cache_set(*key, value=[])
        return []

    text = r.text or ""
    if "foreignToNativeMeanings" not in text and 'class="hw"' not in text:
        await asyncio.sleep(0.2)
        try:
            r = await _aget(url, params={"q": english_term})
            text = r.text or ""
        except Exception:
            _cache_set(*key, value=[])
            return []

    try:
        doc = html.fromstring(text)
    except Exception:
        _cache_set(*key, value=[])
        return []

    results: List[str] = []
    li_nodes = doc.xpath('//ol[contains(@class,"foreignToNativeMeanings")]//li')
    if not li_nodes:
        li_nodes = doc.xpath('//li[contains(@class,"meaning") or contains(@class,"dictionaryEntry")]')

    def add(val: str):
        v = _clean_text(val)
        if not v or v.lower() in {"np.", "np", "itp.", "itd."}:
            return
        if v not in results:
            results.append(v)

    for li in li_nodes:
        # 1) a.plainLink (preferred headword)
        for a in li.xpath('.//a[contains(@class,"plainLink")]/text()'):
            add(a)
        # 2) span.hw
        for hw in li.xpath('.//span[contains(@class,"hw")]/text()'):
            add(hw)
        # 3) Fallback: cleaned li text first token
        full = li.text_content() or ""
        full = _strip_parentheticals(full)
        token = re.split(r"[;,—–-]", full, maxsplit=1)[0]
        add(token)

    cleaned = [_clean_text(x) for x in results if x]
    out = list(dict.fromkeys(cleaned))  # dedupe preserve order
    _cache_set(*key, value=out)
    return out

# ---------------- ProZ (limit kept; heavier pages) ---

async def proz_lookup(english_term: str, max_results: int = 5) -> List[str]:
    key = ("proz_lookup", english_term, max_results)
    cached = _cache_get(*key)
    if cached is not None:
        return cached

    english_term = (english_term or "").strip()
    if not english_term:
        _cache_set(*key, value=[])
        return []

    url = "https://www.proz.com/search/"
    params = {"term": english_term, "source_lang": "ENG", "target_lang": "POL"}
    try:
        r = await _aget(url, params=params)
    except Exception:
        _cache_set(*key, value=[])
        return []

    try:
        doc = html.fromstring(r.text or "")
    except Exception:
        _cache_set(*key, value=[])
        return []

    texts: List[str] = []
    texts += [t.strip() for t in doc.xpath('//div[contains(@class,"term")]/text()') if t.strip()]
    texts += [t.strip() for t in doc.xpath('//a[contains(@class,"term")]/text()') if t.strip()]
    if not texts:
        texts += [t.strip() for t in doc.xpath('//*[contains(text(),"Polish")]/following::li[1]//text()') if t.strip()]

    out: List[str] = []
    for t in texts:
        t = _clean_text(_strip_parentheticals(t))
        if t and t not in out:
            out.append(t)
        if len(out) >= max_results:
            break

    _cache_set(*key, value=out)
    return out
