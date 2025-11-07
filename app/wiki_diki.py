# app/wiki_diki.py
from __future__ import annotations

import asyncio
import re
import time
from typing import Dict, List, Optional, Tuple
import urllib.parse

import httpx
from lxml import html

USER_AGENT = "en2pl-web/1.4 (+https://localhost) httpx"
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

# Safe helpers that accept a prebuilt key tuple
def _cache_get_key(key: Tuple[str, Tuple]):
    v = _cache.get(key)
    if not v:
        return None
    ts, data = v
    if time.time() - ts > CACHE_TTL:
        _cache.pop(key, None)
        return None
    return data

def _cache_set_key(key: Tuple[str, Tuple], value):
    _cache[key] = (time.time(), value)

# ---------------- Helpers ---------------------------

def _clean_text(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s).strip(" ,;:–—-")
    return s

def _strip_parentheticals(s: str) -> str:
    return re.sub(r"\([^)]*\)", "", s).strip()

def _uniq(seq: List[str]) -> List[str]:
    out, seen = [], set()
    for s in seq:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out

# ---------------- Wikipedia / Wikidata ---------------

async def resolve_en_title(term_or_url: str) -> Optional[str]:
    key = ("resolve_en_title", (term_or_url,))
    cached = _cache_get_key(key)
    if cached is not None:
        return cached

    s = (term_or_url or "").strip()
    m = re.match(r"^https?://(en\.)?wikipedia\.org/wiki/([^#\?]+)", s)
    if m:
        raw = m.group(2)
        title = raw.replace("_", " ")
        title = re.sub(r"%28", "(", title)
        title = re.sub(r"%29", ")", title)
        _cache_set_key(key, title)
        return title

    if not s:
        _cache_set_key(key, None)
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

    _cache_set_key(key, title)
    return title

async def english_to_polish_wikipedia(en_title: str) -> Optional[Dict[str, str]]:
    key = ("english_to_polish_wikipedia", (en_title,))
    cached = _cache_get_key(key)
    if cached is not None:
        return cached

    if not en_title:
        _cache_set_key(key, None)
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
            _cache_set_key(key, out)
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
        _cache_set_key(key, out)
        return out
    except Exception:
        _cache_set_key(key, None)
        return None

# ---------------- Diki (UNLIMITED results) ----------

async def diki_lookup(english_term: str) -> List[str]:
    key = ("diki_lookup", (english_term,))
    cached = _cache_get_key(key)
    if cached is not None:
        return cached

    english_term = (english_term or "").strip()
    if not english_term:
        _cache_set_key(key, [])
        return []

    url = "https://www.diki.pl/slownik-angielskiego"
    try:
        r = await _aget(url, params={"q": english_term})
    except Exception:
        _cache_set_key(key, [])
        return []

    text = r.text or ""
    if "foreignToNativeMeanings" not in text and 'class="hw"' not in text:
        await asyncio.sleep(0.2)
        try:
            r = await _aget(url, params={"q": english_term})
            text = r.text or ""
        except Exception:
            _cache_set_key(key, [])
            return []

    try:
        doc = html.fromstring(text)
    except Exception:
        _cache_set_key(key, [])
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
        for a in li.xpath('.//a[contains(@class,"plainLink")]/text()'):
            add(a)
        for hw in li.xpath('.//span[contains(@class,"hw")]/text()'):
            add(hw)
        full = li.text_content() or ""
        full = _strip_parentheticals(full)
        token = re.split(r"[;,—–-]", full, maxsplit=1)[0]
        add(token)

    cleaned = [_clean_text(x) for x in results if x]
    out = _uniq(cleaned)
    _cache_set_key(key, out)
    return out

# ---------------- ProZ (URL or term) ----------------

async def _fetch_proz(url: str, params: Optional[dict] = None) -> Optional[html.HtmlElement]:
    try:
        r = await _aget(url, params=params)
        return html.fromstring(r.text or "")
    except Exception:
        return None

def _extract_polish_terms(doc: html.HtmlElement) -> List[str]:
    """
    Heuristics to extract Polish terms from a ProZ search results page.
    Returns a de-duplicated list of Polish headwords/translations.
    """
    pl_terms: List[str] = []

    # 1) Scope to containers that mention "Polish" and pull obvious term nodes
    containers = doc.xpath(
        '//*[contains(translate(normalize-space(.),"POLISH","polish"),"polish")]/ancestor-or-self::*[self::section or self::article or self::div][1]'
    )
    for c in containers:
        texts = c.xpath(
            './/a[contains(@class,"term")]/text()'
            ' | .//div[contains(@class,"term")]/text()'
            ' | .//span[contains(@class,"term")]/text()'
        )
        pl_terms.extend([_clean_text(t) for t in texts])

    # 2) Fallback: list items near a “Polish” label
    if not pl_terms:
        li_texts = doc.xpath('//*[contains(text(),"Polish")]/following::li[position()<=6]//text()')
        pl_terms.extend([_clean_text(_strip_parentheticals(t)) for t in li_texts])

    # 3) Broad fallback: any “term” class anywhere
    if not pl_terms:
        pl_terms.extend([_clean_text(t) for t in doc.xpath('//*[contains(@class,"term")]/text()')])

    pl_terms = [t for t in pl_terms if t and t.lower() not in {"np.", "np", "itp.", "itd."}]
    return _uniq(pl_terms)

def _find_blocks(doc: html.HtmlElement):
    blocks = doc.xpath('//article | //section | //div[contains(@class,"result") or contains(@class,"entry") or contains(@class,"card")]')
    return blocks if blocks else [doc]

def _extract_en_pl_pairs(doc: html.HtmlElement) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    blocks = _find_blocks(doc)

    for b in blocks:
        en_scope = b.xpath('.//*[contains(translate(.,"ENGLISH","english"),"english")]')
        pl_scope = b.xpath('.//*[contains(translate(.,"POLISH","polish"),"polish")]')
        if not en_scope or not pl_scope:
            continue

        en_terms: List[str] = []
        for n in en_scope:
            en_terms += n.xpath(
                './/a[contains(@class,"term")]/text()'
                ' | .//div[contains(@class,"term")]/text()'
                ' | .//span[contains(@class,"term")]/text()'
            )
            if not en_terms:
                en_terms += n.xpath('.//strong/text() | .//b/text() | .//*[self::h1 or self::h2 or self::h3]/text()')
        en_terms = [_clean_text(t) for t in en_terms if _clean_text(t)]

        pl_terms: List[str] = []
        for n in pl_scope:
            pl_terms += n.xpath(
                './/a[contains(@class,"term")]/text()'
                ' | .//div[contains(@class,"term")]/text()'
                ' | .//span[contains(@class,"term")]/text()'
            )
            if not pl_terms:
                pl_terms += n.xpath('.//li[position()<=3]//text()')
        pl_terms = [_clean_text(_strip_parentheticals(t)) for t in pl_terms if _clean_text(t)]

        if en_terms and pl_terms:
            for en in en_terms[:3]:
                for pl in pl_terms[:5]:
                    pairs.append((en, pl))

    # de-dup pairs
    uniq_pairs: List[Tuple[str, str]] = []
    seen = set()
    for en, pl in pairs:
        key = (en, pl)
        if en and pl and key not in seen:
            seen.add(key)
            uniq_pairs.append((en, pl))
    return uniq_pairs

async def proz_lookup(english_term_or_url: str, max_results: int = 50) -> List[str]:
    """
    Accepts either a raw English term (e.g., "chaperone") or a full ProZ URL
    like "https://www.proz.com/search/?term=Chaperone&es=1".
    Returns a list of Polish translations (deduped). Limited by `max_results`.
    """
    key = ("proz_lookup_v2", (english_term_or_url, max_results))
    cached = _cache_get_key(key)
    if cached is not None:
        return cached

    s = (english_term_or_url or "").strip()
    doc = None

    if s.lower().startswith("http"):
        doc = await _fetch_proz(s, None)
    else:
        base = "https://www.proz.com/search/"
        params = {"term": s, "source_lang": "ENG", "target_lang": "POL", "es": "1"}
        doc = await _fetch_proz(base, params)

    if doc is None:
        _cache_set_key(key, [])
        return []

    results = _extract_polish_terms(doc)
    out = results[:max_results] if max_results and max_results > 0 else results
    _cache_set_key(key, out)
    return out

async def proz_lookup_pairs(english_term_or_url: str, max_pairs: int = 25) -> List[Tuple[str, str]]:
    """
    Returns best-effort (EN, PL) pairs from a ProZ page/term.
    If exact pairs can't be reliably derived, falls back to pairing the query term with each PL term.
    """
    key = ("proz_lookup_pairs_v1", (english_term_or_url, max_pairs))
    cached = _cache_get_key(key)
    if cached is not None:
        return cached

    s = (english_term_or_url or "").strip()
    if s.lower().startswith("http"):
        doc = await _fetch_proz(s, None)
        query_term = urllib.parse.parse_qs(urllib.parse.urlparse(s).query).get("term", [s])[0]
    else:
        base = "https://www.proz.com/search/"
        params = {"term": s, "source_lang": "ENG", "target_lang": "POL", "es": "1"}
        doc = await _fetch_proz(base, params)
        query_term = s

    if doc is None:
        _cache_set_key(key, [])
        return []

    pairs = _extract_en_pl_pairs(doc)
    if not pairs:
        pl_only = _extract_polish_terms(doc)
        pairs = [(query_term, pl) for pl in pl_only]

    out = pairs[:max_pairs] if max_pairs and max_pairs > 0 else pairs
    _cache_set_key(key, out)
    return out
