# app/main.py
from __future__ import annotations
import asyncio
from typing import Optional, Dict, Any
from urllib.parse import quote_plus

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from .wiki_diki import (
    resolve_en_title,
    english_to_polish_wikipedia,
    diki_lookup,
    proz_lookup,
    proz_lookup_pairs,
    get_client,
)

app = FastAPI(title="EN → PL Lookup", version="1.4.0", debug=True)
templates = Jinja2Templates(directory="templates")

@app.on_event("shutdown")
async def _close_http_client():
    client = get_client()
    try:
        await client.aclose()
    except Exception:
        pass

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, q: Optional[str] = None):
    data: Dict[str, Any] = {}
    if q:
        en_title = await resolve_en_title(q)
        data["query"] = q
        data["en_title"] = en_title

        # Let ProZ accept either a full URL or a plain term; Diki always gets a term
        term_for_dicts = en_title or q

        wiki_task = english_to_polish_wikipedia(en_title) if en_title else asyncio.sleep(0, result=None)
        diki_task = diki_lookup(term_for_dicts)
        proz_terms_task = proz_lookup(q, max_results=50)  # pass q directly (URL or term)
        proz_pairs_task = proz_lookup_pairs(q, max_pairs=25)

        wiki, diki, proz_terms, proz_pairs = await asyncio.gather(
            wiki_task, diki_task, proz_terms_task, proz_pairs_task
        )

        data["wiki"] = wiki
        data["diki"] = diki
        data["proz"] = proz_terms
        data["proz_pairs"] = proz_pairs

        # Pre-encode helpful links
        if en_title:
            enc = quote_plus(en_title)
            data["diki_href"] = f"https://www.diki.pl/slownik-angielskiego?q={enc}"
            data["proz_href"] = f"https://www.proz.com/search/?term={enc}&source_lang=ENG&target_lang=POL&es=1"
        data["api_link"] = f"/api/lookup?q={quote_plus(q)}"

    return templates.TemplateResponse("index.html", {"request": request, "data": data})

@app.get("/api/lookup")
async def api_lookup(
    q: str = Query(..., description="English term, enwiki URL, or a ProZ URL"),
    proz_limit: int = Query(50, ge=1, le=100),
    proz_pairs_limit: int = Query(25, ge=1, le=100),
):
    en_title = await resolve_en_title(q)
    term_for_dicts = en_title or q

    wiki_task = english_to_polish_wikipedia(en_title) if en_title else asyncio.sleep(0, result=None)
    diki_task = diki_lookup(term_for_dicts)  # unlimited
    proz_terms_task = proz_lookup(q, max_results=proz_limit)       # q may be a URL
    proz_pairs_task = proz_lookup_pairs(q, max_pairs=proz_pairs_limit)

    wiki, diki, proz_terms, proz_pairs = await asyncio.gather(
        wiki_task, diki_task, proz_terms_task, proz_pairs_task
    )

    return JSONResponse({
        "query": q,
        "resolved_en_title": en_title,
        "wikipedia": wiki,
        "diki": diki,
        "proz": proz_terms,
        "proz_pairs": proz_pairs,
    })

@app.get("/healthz")
async def healthz():
    return {"ok": True}
