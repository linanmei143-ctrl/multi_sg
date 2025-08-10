import os, httpx, xml.etree.ElementTree as ET
from fastapi import FastAPI, HTTPException, Query
from dotenv import load_dotenv

load_dotenv()
app = FastAPI(title="multi-source-lit-api")

SPRINGER = os.getenv("SPRINGER_API_KEY")
TIMEOUT = 20

# -------------------- 基础工具 --------------------
def _ok(r: httpx.Response):
    if r.status_code != 200:
        raise HTTPException(r.status_code, r.text[:1000])
    return r.json()

def _norm_doi(doi: str | None) -> str | None:
    if not doi: return None
    doi = doi.strip().lower()
    return doi.replace("https://doi.org/", "").replace("http://doi.org/", "")

# -------------------- 各源 fetch --------------------
def fetch_springer_oa(q: str):
    if not SPRINGER: raise HTTPException(500, "SPRINGER_API_KEY missing")
    return httpx.get("https://api.springernature.com/openaccess/json",
                     params={"q": q, "p": "5", "api_key": SPRINGER}, timeout=TIMEOUT)

def fetch_crossref(q: str):
    return httpx.get("https://api.crossref.org/works",
                     params={"query": q, "rows": "5"}, timeout=TIMEOUT)

def fetch_doaj(q: str):
    return httpx.get(f"https://doaj.org/api/v2/search/articles/{q}",
                     params={"pageSize": "5"}, timeout=TIMEOUT)

def fetch_openalex(q: str):
    return httpx.get("https://api.openalex.org/works",
                     params={"search": q, "per-page": "5"}, timeout=TIMEOUT)

def fetch_arxiv_xml(q: str):
    headers = {
        "User-Agent": "multi-sg/0.1 (mailto:you@example.com)",
        "Accept": "application/atom+xml"
    }
    return httpx.get(
        "https://export.arxiv.org/api/query",          # 改为 https
        params={"search_query": f"all:{q}", "start": 0, "max_results": 5},
        headers=headers, timeout=TIMEOUT,
        follow_redirects=True                          # 允许跟随 301/302
    )

def fetch_pubmed_esearch(q: str):
    return httpx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                     params={"db": "pubmed", "term": q, "retmax": 5, "retmode": "json"},
                     timeout=TIMEOUT)

def fetch_pubmed_efetch_xml(ids_csv: str):
    return httpx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                     params={"db": "pubmed", "id": ids_csv, "retmode": "xml"},
                     timeout=TIMEOUT)

# -------------------- 透传 --------------------
@app.get("/health")
def health(): return {"ok": True}

@app.get("/search")
def search(q: str = Query(..., min_length=1),
           source: str = Query("springer")):
    if source == "springer":
        return _ok(fetch_springer_oa(q))
    elif source == "crossref":
        return _ok(fetch_crossref(q))
    elif source == "doaj":
        return _ok(fetch_doaj(q))
    elif source == "openalex":
        return _ok(fetch_openalex(q))
    elif source == "arxiv":
        r = fetch_arxiv_xml(q)
        if r.status_code != 200: raise HTTPException(r.status_code, r.text[:1000])
        return {"xml": r.text}
    elif source == "pubmed":
        r = fetch_pubmed_esearch(q); js = _ok(r)
        ids = ",".join(js.get("esearchresult", {}).get("idlist", []))
        if not ids: return {"ids": []}
        r2 = fetch_pubmed_efetch_xml(ids)
        if r2.status_code != 200: raise HTTPException(r2.status_code, r2.text[:1000])
        return {"xml": r2.text}
    elif source == "all":
        out = {
            "springer": _ok(fetch_springer_oa(q)),
            "crossref": _ok(fetch_crossref(q)),
            "doaj": _ok(fetch_doaj(q)),
            "openalex": _ok(fetch_openalex(q)),
        }
        # arXiv / PubMed 返回 XML，单独放
        rx = fetch_arxiv_xml(q); out["arxiv_xml"] = rx.text if rx.status_code==200 else None
        pm = _ok(fetch_pubmed_esearch(q)); ids = ",".join(pm.get("esearchresult",{}).get("idlist",[]))
        if ids:
            pmx = fetch_pubmed_efetch_xml(ids); out["pubmed_xml"] = pmx.text if pmx.status_code==200 else None
        else:
            out["pubmed_xml"] = None
        return out
    else:
        raise HTTPException(400, "unknown source")

# -------------------- 精简并区分来源（按 DOI 去重） --------------------
@app.get("/search/compact")
def search_compact(q: str = Query(..., min_length=1),
                   source: str = Query("springer")):
    if source == "springer":
        return _compact_springer(q)
    elif source == "crossref":
        return _compact_crossref(q)
    elif source == "doaj":
        return _compact_doaj(q)
    elif source == "openalex":
        return _compact_openalex(q)
    elif source == "arxiv":
        return _compact_arxiv(q)
    elif source == "pubmed":
        return _compact_pubmed(q)
    elif source == "all":
        agg = []
        agg += _compact_springer(q)
        agg += _compact_crossref(q)
        agg += _compact_doaj(q)
        agg += _compact_openalex(q)
        agg += _compact_arxiv(q)
        agg += _compact_pubmed(q)
        # 去重：优先 DOI，其次 URL
        seen_doi, seen_url, dedup = set(), set(), []
        for it in agg:
            doi = _norm_doi(it.get("doi"))
            url = (it.get("url") or "").strip().lower() or None
            if doi:
                if doi in seen_doi: continue
                seen_doi.add(doi)
            else:
                if url and url in seen_url: continue
                if url: seen_url.add(url)
            dedup.append(it)
        # 可选排序：有 DOI 优先，再按年份
        dedup.sort(key=lambda x: (x.get("doi") is None, str(x.get("date") or "")))
        return dedup
    else:
        raise HTTPException(400, "unknown source")

def _compact_springer(q: str):
    js = _ok(fetch_springer_oa(q))
    recs = js.get("records", []) or []
    out = []
    for x in recs:
        url_item = (x.get("url") or [{}])[0]
        out.append({
            "title": x.get("title"),
            "doi": _norm_doi(x.get("doi")),
            "url": url_item.get("value"),
            "journal": x.get("publicationName"),
            "date": x.get("publicationDate") or x.get("onlineDate"),
            "oa": x.get("openAccess"),
            "source": "springer_openaccess",
        })
    return out

def _compact_crossref(q: str):
    items = _ok(fetch_crossref(q)).get("message", {}).get("items", [])
    def ymd(it):
        dp = (it.get("issued", {}).get("date-parts") or [[None]])[0]
        return "-".join(str(i) for i in dp if i is not None) if dp else None
    out = []
    for it in items:
        out.append({
            "title": (it.get("title") or [None])[0],
            "doi": _norm_doi(it.get("DOI")),
            "url": it.get("URL"),
            "journal": (it.get("container-title") or [None])[0],
            "date": ymd(it),
            "source": "crossref",
        })
    return out

def _compact_doaj(q: str):
    hits = _ok(fetch_doaj(q)).get("results", [])
    out = []
    for h in hits:
        bib = h.get("bibjson", {}) or {}
        doi = None
        for idn in bib.get("identifier", []) or []:
            if idn.get("type") == "doi":
                doi = idn.get("id")
        url = None
        links = bib.get("link") or []
        if links:
            url = links[0].get("url")
        out.append({
            "title": bib.get("title"),
            "doi": _norm_doi(doi),
            "url": url,
            "journal": (bib.get("journal") or {}).get("title"),
            "date": bib.get("year"),
            "source": "doaj",
        })
    return out

def _compact_openalex(q: str):
    res = _ok(fetch_openalex(q)).get("results", []) or []
    out = []
    for w in res:
        url = (w.get("primary_location") or {}).get("landing_page_url") \
              or (w.get("ids") or {}).get("openalex") \
              or (w.get("primary_location") or {}).get("pdf_url")
        journal = (w.get("host_venue") or {}).get("display_name")
        out.append({
            "title": w.get("title"),
            "doi": _norm_doi((w.get("ids") or {}).get("doi")),
            "url": url,
            "journal": journal,
            "date": w.get("publication_year"),
            "source": "openalex",
        })
    return out

def _compact_arxiv(q: str):
    r = fetch_arxiv_xml(q)
    if r.status_code != 200: raise HTTPException(r.status_code, r.text[:1000])
    txt = r.text
    root = ET.fromstring(txt)

    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    entries = root.findall("atom:entry", ns)
    # 无前缀时的兜底
    if not entries:
        entries = root.findall("{http://www.w3.org/2005/Atom}entry")

    out = []
    for e in entries:
        def g(path, default=None):
            el = e.find(path, ns) if ":" in path else e.find(path)
            return (el.text.strip() if el is not None and el.text else default)
        title = g("atom:title") or g("{http://www.w3.org/2005/Atom}title")
        doi = g("arxiv:doi")
        # link 优先 alternate，否则用 id
        link = e.find("atom:link[@rel='alternate']", ns)
        url = (link.attrib.get("href") if link is not None else g("atom:id") or g("{http://www.w3.org/2005/Atom}id"))
        journal = g("arxiv:journal_ref")
        date = g("atom:published") or g("{http://www.w3.org/2005/Atom}published")

        out.append({
            "title": title,
            "doi": _norm_doi(doi),
            "url": url,
            "journal": journal,
            "date": date,
            "source": "arxiv",
        })
    return out

def _compact_pubmed(q: str):
    # esearch -> efetch(xml)
    js = _ok(fetch_pubmed_esearch(q))
    ids = js.get("esearchresult", {}).get("idlist", [])
    if not ids: return []
    ids_csv = ",".join(ids)
    r = fetch_pubmed_efetch_xml(ids_csv)
    if r.status_code != 200: raise HTTPException(r.status_code, r.text[:1000])
    root = ET.fromstring(r.text)
    out = []
    for art in root.findall(".//PubmedArticle"):
        # 标题
        title_el = art.find(".//ArticleTitle")
        title = "".join(title_el.itertext()).strip() if title_el is not None else None
        # 期刊
        journal = None
        jt = art.find(".//Journal/Title")
        if jt is not None: journal = jt.text
        # 日期（年优先）
        year = None
        yel = art.find(".//JournalIssue/PubDate/Year")
        if yel is not None and yel.text: year = yel.text
        # DOI
        doi = None
        for idn in art.findall(".//ArticleIdList/ArticleId"):
            if idn.attrib.get("IdType") == "doi":
                doi = idn.text
                break
        # URL（用 PubMed 页面）
        pmid_el = art.find(".//PMID")
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid_el.text}/" if pmid_el is not None else None
        out.append({
            "title": title,
            "doi": _norm_doi(doi),
            "url": url,
            "journal": journal,
            "date": year,
            "source": "pubmed",
        })
    return out

# -------------------- 入口 --------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("fastapi_app:app", host="127.0.0.1", port=int(os.getenv("PORT", 8000)))
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
