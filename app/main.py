# app/main.py
from __future__ import annotations
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from typing import Optional, Dict
from .wiki_diki import resolve_en_title, english_to_polish_wikipedia, diki_lookup

app = FastAPI(title="EN → PL Lookup", version="1.0.0")
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
def index(request: Request, q: Optional[str] = None):
    data: Dict = {}
    if q:
        en_title = resolve_en_title(q)
        data["query"] = q
        data["en_title"] = en_title

        if en_title:
            wiki = english_to_polish_wikipedia(en_title)
            data["wiki"] = wiki
            data["diki"] = diki_lookup(en_title, max_results=5)
        else:
            data["wiki"] = None
            data["diki"] = diki_lookup(q, max_results=5)

    return templates.TemplateResponse("index.html", {"request": request, "data": data})

@app.get("/api/lookup")
def api_lookup(q: str = Query(..., description="English term or enwiki URL"),
               diki_limit: int = Query(5, ge=1, le=20)):
    en_title = resolve_en_title(q)
    resp = {
        "query": q,
        "resolved_en_title": en_title,
        "wikipedia": None,
        "diki": [],
    }
    if en_title:
        resp["wikipedia"] = english_to_polish_wikipedia(en_title)
        resp["diki"] = diki_lookup(en_title, max_results=diki_limit)
    else:
        # If we couldn’t resolve to an enwiki title, still try Diki on raw query
        resp["diki"] = diki_lookup(q, max_results=diki_limit)
    return JSONResponse(resp)

@app.get("/healthz")
def healthz():
    return {"ok": True}

