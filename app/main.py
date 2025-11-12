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
    get_client,
)
from .dsl_parser import dsl_lookup

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

        wiki, diki = await asyncio.gather(wiki_task, diki_task)

        # DSL lookup (synchronous, local)
        dsl_en_pl = dsl_lookup(term_for_dicts, direction="en-pl")
        dsl_pl_en = dsl_lookup(term_for_dicts, direction="pl-en")

        data["wiki"] = wiki
        data["diki"] = diki
        data["dsl_en_pl"] = dsl_en_pl
        data["dsl_pl_en"] = dsl_pl_en

        # Pre-encode helpful links
        if en_title:
            enc = quote_plus(en_title)
            data["diki_href"] = f"https://www.diki.pl/slownik-angielskiego?q={enc}"

        # Build ProZ URLs for both EN→PL and PL→EN
        search_term = en_title or q
        enc_term = quote_plus(search_term)
        data["proz_en_pl_url"] = f"https://www.proz.com/search/?term={enc_term}&source_lang=ENG&target_lang=POL&es=1"
        data["proz_pl_en_url"] = f"https://www.proz.com/search/?term={enc_term}&source_lang=POL&target_lang=ENG&es=1"

        data["api_link"] = f"/api/lookup?q={quote_plus(q)}"

    return templates.TemplateResponse("index.html", {"request": request, "data": data})

@app.get("/api/lookup")
async def api_lookup(
    q: str = Query(..., description="English term or enwiki URL"),
):
    en_title = await resolve_en_title(q)
    term_for_dicts = en_title or q

    wiki_task = english_to_polish_wikipedia(en_title) if en_title else asyncio.sleep(0, result=None)
    diki_task = diki_lookup(term_for_dicts)  # unlimited

    wiki, diki = await asyncio.gather(wiki_task, diki_task)

    # DSL lookup (synchronous, local)
    dsl_en_pl = dsl_lookup(term_for_dicts, direction="en-pl")
    dsl_pl_en = dsl_lookup(term_for_dicts, direction="pl-en")

    # Build ProZ URLs
    search_term = en_title or q
    enc_term = quote_plus(search_term)

    return JSONResponse({
        "query": q,
        "resolved_en_title": en_title,
        "wikipedia": wiki,
        "diki": diki,
        "dsl_en_pl": dsl_en_pl,
        "dsl_pl_en": dsl_pl_en,
        "proz_en_pl_url": f"https://www.proz.com/search/?term={enc_term}&source_lang=ENG&target_lang=POL&es=1",
        "proz_pl_en_url": f"https://www.proz.com/search/?term={enc_term}&source_lang=POL&target_lang=ENG&es=1",
    })

@app.get("/healthz")
async def healthz():
    return {"ok": True}
