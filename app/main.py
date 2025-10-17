# app/main.py
from __future__ import annotations
import asyncio
from typing import Optional, Dict, Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from .wiki_diki import (
    resolve_en_title,
    english_to_polish_wikipedia,
    diki_lookup,
    proz_lookup,
)

app = FastAPI(title="EN → PL Lookup", version="1.3.0")
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, q: Optional[str] = None):
    data: Dict[str, Any] = {}
    if q:
        en_title = await resolve_en_title(q)
        data["query"] = q
        data["en_title"] = en_title

        term_for_dicts = en_title or q

        # Run lookups in parallel
        wiki_task = english_to_polish_wikipedia(en_title) if en_title else asyncio.sleep(0, result=None)
        diki_task = diki_lookup(term_for_dicts)  # unlimited results
        proz_task = proz_lookup(term_for_dicts, max_results=5)
        wiki, diki, proz = await asyncio.gather(wiki_task, diki_task, proz_task)

        data["wiki"] = wiki
        data["diki"] = diki
        data["proz"] = proz

    return templates.TemplateResponse("index.html", {"request": request, "data": data})

@app.get("/api/lookup")
async def api_lookup(
    q: str = Query(..., description="English term or enwiki URL"),
    proz_limit: int = Query(5, ge=1, le=50),
):
    en_title = await resolve_en_title(q)
    term_for_dicts = en_title or q

    wiki_task = english_to_polish_wikipedia(en_title) if en_title else asyncio.sleep(0, result=None)
    diki_task = diki_lookup(term_for_dicts)  # unlimited
    proz_task = proz_lookup(term_for_dicts, max_results=proz_limit)
    wiki, diki, proz = await asyncio.gather(wiki_task, diki_task, proz_task)

    return JSONResponse({
        "query": q,
        "resolved_en_title": en_title,
        "wikipedia": wiki,
        "diki": diki,
        "proz": proz,
    })

@app.get("/healthz")
async def healthz():
    return {"ok": True}
