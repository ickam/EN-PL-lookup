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

        # Build ProZ proxy URLs for both EN→PL and PL→EN
        search_term = en_title or q
        enc_term = quote_plus(search_term)
        data["proz_en_pl_url"] = f"/proz-proxy?term={enc_term}&source_lang=ENG&target_lang=POL"
        data["proz_pl_en_url"] = f"/proz-proxy?term={enc_term}&source_lang=POL&target_lang=ENG"
        data["proz_en_pl_external"] = f"https://www.proz.com/search/?term={enc_term}&source_lang=ENG&target_lang=POL&es=1"
        data["proz_pl_en_external"] = f"https://www.proz.com/search/?term={enc_term}&source_lang=POL&target_lang=ENG&es=1"

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
        "proz_en_pl_proxy": f"/proz-proxy?term={enc_term}&source_lang=ENG&target_lang=POL",
        "proz_pl_en_proxy": f"/proz-proxy?term={enc_term}&source_lang=POL&target_lang=ENG",
        "proz_en_pl_url": f"https://www.proz.com/search/?term={enc_term}&source_lang=ENG&target_lang=POL&es=1",
        "proz_pl_en_url": f"https://www.proz.com/search/?term={enc_term}&source_lang=POL&target_lang=ENG&es=1",
    })

@app.get("/proz-proxy")
async def proz_proxy(
    term: str = Query(..., description="Search term"),
    source_lang: str = Query("ENG", description="Source language"),
    target_lang: str = Query("POL", description="Target language"),
):
    """Proxy endpoint for ProZ search to bypass iframe restrictions."""
    from .wiki_diki import _aget
    from lxml import html as lxml_html
    import re

    # Fetch ProZ search page
    url = "https://www.proz.com/search/"
    params = {
        "term": term,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "es": "1"
    }

    try:
        response = await _aget(url, params=params)
        html_content = response.text

        # Parse HTML
        doc = lxml_html.fromstring(html_content)

        # Find "All resources" section or main results container
        # Try multiple selectors to find the results section
        results_section = None

        # Look for various possible containers
        possible_selectors = [
            '//h2[contains(text(), "All resources")]/following-sibling::*',
            '//h3[contains(text(), "All resources")]/following-sibling::*',
            '//*[contains(@class, "results")]',
            '//*[contains(@class, "search-results")]',
            '//main',
            '//*[@id="search-results"]',
        ]

        # Try to find "All resources" heading and extract from there
        all_resources_heading = doc.xpath('//h2[contains(text(), "All resources")] | //h3[contains(text(), "All resources")]')

        if all_resources_heading:
            # Get all content after "All resources"
            heading = all_resources_heading[0]
            results_html = lxml_html.tostring(heading, encoding='unicode')

            # Get all following siblings
            following = heading.xpath('./following-sibling::*')
            for elem in following:
                results_html += lxml_html.tostring(elem, encoding='unicode')

        else:
            # Fallback: try to find main results container
            results_container = doc.xpath('//*[contains(@class, "search-results")] | //main | //*[@role="main"]')
            if results_container:
                results_html = lxml_html.tostring(results_container[0], encoding='unicode')
            else:
                # Last resort: use full body
                results_html = html_content

        # Filter out entries with wrong language pairs
        # Look for language pair indicators and remove non-matching ones
        doc_filtered = lxml_html.fromstring(results_html)

        # Remove entries that don't match the requested language pair
        # Common patterns: lang pairs shown as "ENG>POL", "ENG to POL", etc.
        wrong_lang_indicators = []

        # Build list of wrong language pairs to filter out
        all_langs = ['ENG', 'POL', 'SPA', 'FRA', 'DEU', 'ITA', 'RUS', 'CHI', 'JPN', 'POR']
        for lang1 in all_langs:
            for lang2 in all_langs:
                if not ((lang1 == source_lang and lang2 == target_lang) or
                       (lang1 == target_lang and lang2 == source_lang)):
                    wrong_lang_indicators.append(f"{lang1}>{lang2}")
                    wrong_lang_indicators.append(f"{lang1} to {lang2}")
                    wrong_lang_indicators.append(f"{lang1}-{lang2}")

        # Remove elements containing wrong language pairs
        for indicator in wrong_lang_indicators[:20]:  # Limit to avoid too many removals
            elements = doc_filtered.xpath(f'//*[contains(text(), "{indicator}")]')
            for elem in elements:
                # Remove the parent container (likely a result entry)
                parent = elem.getparent()
                if parent is not None:
                    grandparent = parent.getparent()
                    if grandparent is not None:
                        grandparent.remove(parent)

        results_html = lxml_html.tostring(doc_filtered, encoding='unicode')

        # Rewrite relative URLs to absolute URLs
        results_html = results_html.replace('href="/', 'href="https://www.proz.com/')
        results_html = results_html.replace("href='/", "href='https://www.proz.com/")
        results_html = results_html.replace('src="/', 'src="https://www.proz.com/')
        results_html = results_html.replace("src='/", "src='https://www.proz.com/")

        # Build minimal HTML page with styles
        final_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <base href="https://www.proz.com/">
            <title>ProZ Results: {term}</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 1rem; }}
                a {{ color: #2563eb; }}
            </style>
        </head>
        <body>
            {results_html}
        </body>
        </html>
        """

        return HTMLResponse(content=final_html)
    except Exception as e:
        error_html = f"""
        <html>
        <head><title>ProZ Error</title></head>
        <body style="font-family: sans-serif; padding: 2rem;">
            <h2>Unable to load ProZ results</h2>
            <p>Error: {str(e)}</p>
            <p><a href="https://www.proz.com/search/?term={term}&source_lang={source_lang}&target_lang={target_lang}&es=1" target="_blank">
                Open ProZ search in new window
            </a></p>
        </body>
        </html>
        """
        return HTMLResponse(content=error_html, status_code=500)

@app.get("/healthz")
async def healthz():
    return {"ok": True}
