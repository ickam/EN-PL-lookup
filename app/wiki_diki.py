# app/wiki_diki.py
from __future__ import annotations

import re
import time
from functools import lru_cache
from typing import Dict, List, Optional

import requests
from lxml import html

USER_AGENT = "en2pl-web/1.1 (+https://localhost) requests"
HTTP_TIMEOUT = 10.0

# --- HTTP helper -------------------------------------------------------------

def _http_get(url: str, params: dict | None = None, timeout: float = HTTP_TIMEOUT) -> requests.Response:
    """GET with a friendly UA, short timeout, and .raise_for_status()."""
    r = requests.get(
        url,
        params=params,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "pl,en;q=0.9",
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r

# --- Wikipedia / Wikidata ----------------------------------------------------

@lru_cache(maxsize=2048)
def resolve_en_title(term_or_url: str) -> Optional[str]:
    """
    Accepts an English term or an enwiki URL and returns a resolved English page title, or None.
    Cached for speed.
    """
    s = (term_or_url or "").strip()

    # If it's an enwiki URL, extract the title.
    m = re.match(r"^https?://(en\.)?wikipedia\.org/wiki/([^#\?]+)", s)
    if m:
        raw = m.group(2)
        title = raw.replace("_", " ")
        # mild unescape for common cases
        title = re.sub(r"%28", "(", title)
        title = re.sub(r"%29", ")", title)
        return title

    if not s:
        return None

    # Search English Wikipedia (top result only).
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "list": "search",
        "srsearch": s,
        "srlimit": 1,
    }
    try:
        resp = _http_get("https://en.wikipedia.org/w/api.php", params=params).json()
        return resp["query"]["search"][0]["title"]
    except Exception:
        return None

@lru_cache(maxsize=2048)
def english_to_polish_wikipedia(en_title: str) -> Optional[Dict[str, str]]:
    """
    Returns {"en_title": "...", "pl_title": "...", "pl_url": "..."} or None.
    Cached for speed.
    """
    if not en_title:
        return None

    # 1) Try direct langlinks
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
        resp = _http_get("https://en.wikipedia.org/w/api.php", params=params).json()
        page = resp["query"]["pages"][0]
        ll = page.get("langlinks", [])
        if ll:
            return {"en_title": en_title, "pl_title": ll[0]["title"], "pl_url": ll[0]["url"]}
    except Exception:
        pass

    # 2) Fallback via Wikidata sitelinks
    try:
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "pageprops",
            "titles": en_title,
        }
        pp = _http_get("https://en.wikipedia.org/w/api.php", params=params).json()
        qid = pp["query"]["pages"][0]["pageprops"]["wikibase_item"]

        params = {
            "action": "wbgetentities",
            "format": "json",
            "ids": qid,
            "props": "sitelinks/urls",
            "sitefilter": "plwiki",
        }
        wd = _http_get("https://www.wikidata.org/w/api.php", params=params).json()
        pl = wd["entities"][qid]["sitelinks"]["plwiki"]
        return {"en_title": en_title, "pl_title": pl["title"], "pl_url": pl["url"]}
    except Exception:
        return None

# --- Diki.pl scraping --------------------------------------------------------

def _clean_text(s: str) -> str:
    s = (s or "").strip()
    # collapse whitespace, drop outer punctuation
    s = re.sub(r"\s+", " ", s).strip(" ,;:–—-")
    return s

def _strip_parentheticals(s: str) -> str:
    # remove simple (...) blocks (qualifiers like "(stacjonarna)")
    return re.sub(r"\([^)]*\)", "", s).strip()

def _dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for it in items:
        if not it:
            continue
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out

@lru_cache(maxsize=2048)
def diki_lookup(english_term: str, max_results: int = 5) -> List[str]:
    """
    Return up to `max_results` Polish headwords from Diki.pl for the English term.
    Strategy (in priority):
      1) <li>…<a class~="plainLink">HEAD</a>…  (most reliable)
      2) <li>…<span class~="hw">HEAD</span>…   (common)
      3) Fallback: cleaned first token of the <li> text (minus qualifiers).
    De-duplicates and avoids returning only qualifiers/examples.
    Cached for speed.
    """
    english_term = (english_term or "").strip()
    if not english_term:
        return []

    url = "https://www.diki.pl/slownik-angielskiego"
    try:
        resp = _http_get(url, params={"q": english_term})
    except Exception:
        return []

    # Quick guard against anti-bot or empty pages
    text = resp.text or ""
    if "foreignToNativeMeanings" not in text and "class=\"hw\"" not in text:
        # Give it one tiny retry after a moment (Diki can be moody)
        time.sleep(0.3)
        try:
            resp = _http_get(url, params={"q": english_term})
            text = resp.text or ""
        except Exception:
            return []

    try:
        doc = html.fromstring(text)
    except Exception:
        return []

    results: List[str] = []

    # Collect candidate <li> nodes containing translations
    li_nodes = doc.xpath('//ol[contains(@class,"foreignToNativeMeanings")]//li')
    if not li_nodes:
        # Sometimes the structure differs slightly; broaden
        li_nodes = doc.xpath('//li[contains(@class,"meaning") or contains(@class,"dictionaryEntry")]')

    def add(text_value: str):
        t = _clean_text(text_value)
        if not t:
            return
        if t.lower() in {"np.", "np", "itp.", "itd."}:
            return
        if t and t not in results:
            results.append(t)

    for li in li_nodes:
        if len(results) >= max_results:
            break

        # 1) a.plainLink (preferred headword anchor)
        anchors = li.xpath('.//a[contains(@class,"plainLink")]/text()')
        for a in anchors:
            add(a)
            if len(results) >= max_results:
                break
        if len(results) >= max_results:
            break

        # 2) span.hw (headword)
        hws = li.xpath('.//span[contains(@class,"hw")]/text()')
        for hw in hws:
            add(hw)
            if len(results) >= max_results:
                break
        if len(results) >= max_results:
            break

        # 3) Fallback: first token from li text, minus qualifiers
        if len(results) < max_results:
            full = li.text_content() or ""
            full = _strip_parentheticals(full)
            # take first bit up to comma/semicolon/em dash/dash
            token = re.split(r"[;,—–-]", full, maxsplit=1)[0]
            add(token)

    # Clean & dedupe, cut to limit
    cleaned = [_clean_text(x) for x in results if x]
    cleaned = [re.sub(r"^\W+|\W+$", "", c) for c in cleaned]  # trim odd punctuation
    cleaned = _dedupe_preserve_order(cleaned)
    return cleaned[: max_results]
