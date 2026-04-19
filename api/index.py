from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import httpx

app = FastAPI(title="Scrapling API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ScrapeRequest(BaseModel):
    url: str
    css: Optional[str] = None
    xpath: Optional[str] = None
    html: Optional[str] = None  # pass raw HTML directly (no fetch needed)


class ScrapeResponse(BaseModel):
    url: Optional[str]
    results: Optional[list]
    raw_html: Optional[str]
    error: Optional[str] = None


@app.get("/")
def root():
    return {"status": "ok", "service": "Scrapling Parser API"}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/parse", response_model=ScrapeResponse)
async def parse(req: ScrapeRequest):
    """
    Parse HTML with CSS or XPath selectors.
    You can either pass a URL to fetch, or pass raw HTML directly via `html` field.
    """
    from scrapling.parser import Selector

    try:
        html_content = req.html

        # If no raw HTML provided, fetch the URL
        if not html_content:
            if not req.url:
                raise HTTPException(status_code=400, detail="Provide either `url` or `html`")
            async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    )
                }
                response = await client.get(req.url, headers=headers)
                response.raise_for_status()
                html_content = response.text

        page = Selector(html_content)

        if req.css:
            results = page.css(req.css).getall()
            return ScrapeResponse(url=req.url, results=results, raw_html=None)

        if req.xpath:
            results = page.xpath(req.xpath).getall()
            return ScrapeResponse(url=req.url, results=results, raw_html=None)

        # No selector — return truncated raw HTML
        return ScrapeResponse(
            url=req.url,
            results=None,
            raw_html=html_content[:10000],
        )

    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Fetch error: {e.response.status_code}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Request failed: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/extract-text")
async def extract_text(req: ScrapeRequest):
    """
    Returns clean text content from a URL or raw HTML.
    Strips all tags, returns readable text.
    """
    from scrapling.parser import Selector

    try:
        html_content = req.html

        if not html_content:
            async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                headers = {"User-Agent": "Mozilla/5.0 Chrome/122"}
                r = await client.get(req.url, headers=headers)
                r.raise_for_status()
                # Respect declared charset (e.g. iso-8859-1)
                content_type = r.headers.get("content-type", "")
                if "charset=" in content_type:
                    charset = content_type.split("charset=")[-1].split(";")[0].strip()
                    try:
                        html_content = r.content.decode(charset, errors="replace")
                    except (LookupError, UnicodeDecodeError):
                        html_content = r.text
                else:
                    html_content = r.text

        page = Selector(html_content)
        selector = req.css or "body"
        elements = page.css(selector)
        texts = [el.get_text(separator=" ", strip=True) for el in elements]

        return {"url": req.url, "texts": texts}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/enrich")
async def enrich(req: ScrapeRequest):
    """
    Given a website URL, extract emails and social media handles.
    Automatically tries /contact, /contacto, /about subpages too.
    """
    from scrapling.parser import Selector
    import re

    if not req.url:
        raise HTTPException(status_code=400, detail="url is required")

    EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
    JUNK_RE  = re.compile(r'\.(png|jpg|jpeg|gif|svg|webp|css|js)$', re.I)
    JUNK_KW  = {"sentry", "webpack", "example", "domain", "email@", "user@", "test@", "noreply@", "no-reply@", "ejemplo", "correo@", "tunombre@", "yourname@", "nombre@"}
    SOCIAL_RE = [
        ("facebook",  re.compile(r'(?:facebook\.com|fb\.com)/([A-Za-z0-9._\-]{3,})', re.I)),
        ("instagram", re.compile(r'instagram\.com/([A-Za-z0-9._]{3,})',              re.I)),
        ("linkedin",  re.compile(r'linkedin\.com/(?:company|in)/([A-Za-z0-9._\-]{2,})', re.I)),
        ("twitter",   re.compile(r'(?:twitter\.com|x\.com)/([A-Za-z0-9._]{2,})',    re.I)),
        ("tiktok",    re.compile(r'tiktok\.com/@([A-Za-z0-9._]{2,})',               re.I)),
        ("youtube",   re.compile(r'youtube\.com/(?:channel|c|@)([A-Za-z0-9._\-]{2,})', re.I)),
    ]
    SKIP_SOCIAL = {"sharer", "share", "home", "index", "login", "signup", "about", "watch"}

    base = req.url.rstrip("/")
    urls_to_try = [base, f"{base}/contacto", f"{base}/contact", f"{base}/about", f"{base}/sobre-nosotros"]

    # Auto-discover contact-like internal links from homepage
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as _c:
            _r = await _c.get(base, headers={"User-Agent": "Mozilla/5.0 Chrome/122"})
            if _r.is_success:
                ct = _r.headers.get("content-type","")
                if "charset=" in ct:
                    cs = ct.split("charset=")[-1].split(";")[0].strip()
                    try: _html = _r.content.decode(cs, errors="replace")
                    except: _html = _r.text
                else:
                    _html = _r.text
                _page = Selector(_html)
                contact_kw = re.compile(r'contact|contacto|contactar|contact-us|about|sobre|aviso', re.I)
                for href in _page.css("a::attr(href)").getall():
                    if not href or href.startswith(("http","#","mailto","tel","javascript")):
                        continue
                    if contact_kw.search(href):
                        full = f"{base}/{href.lstrip('/')}"
                        if full not in urls_to_try:
                            urls_to_try.append(full)
    except Exception:
        pass

    all_emails: set = set()
    all_socials: dict = {}
    all_phones:  set = set()
    phone_re = re.compile(r'(?:\+34|\+351)[\s.\-]?[0-9]{2,3}[\s.\-]?[0-9]{3}[\s.\-]?[0-9]{3,4}')

    async with httpx.AsyncClient(follow_redirects=True, timeout=12) as client:
        headers = {"User-Agent": "Mozilla/5.0 Chrome/122 Safari/537.36"}
        for url in urls_to_try:
            try:
                r = await client.get(url, headers=headers)
                if not r.is_success:
                    continue
                ct = r.headers.get("content-type","")
                if "charset=" in ct:
                    cs = ct.split("charset=")[-1].split(";")[0].strip()
                    try: html = r.content.decode(cs, errors="replace")
                    except: html = r.text
                else:
                    html = r.text
                page = Selector(html)

                # Emails from text + href="mailto:"
                for e in EMAIL_RE.findall(html):
                    if JUNK_RE.search(e):
                        continue
                    if any(k in e.lower() for k in JUNK_KW):
                        continue
                    all_emails.add(e.lower())

                mailto_links = page.css('a[href^="mailto:"]::attr(href)').getall()
                for m in mailto_links:
                    addr = m.replace("mailto:", "").split("?")[0].strip().lower()
                    if addr and "@" in addr:
                        all_emails.add(addr)

                # Social links from <a href>
                links_html = " ".join(page.css("a::attr(href)").getall())
                for name, pattern in SOCIAL_RE:
                    if name in all_socials:
                        continue
                    m = pattern.search(links_html + " " + html)
                    if m and m.group(1).lower() not in SKIP_SOCIAL:
                        all_socials[name] = m.group(1)

                # Phones
                for p in phone_re.findall(html):
                    all_phones.add(re.sub(r'[\s.\-]', '', p))

            except Exception:
                continue

    return {
        "url": req.url,
        "emails":  sorted(all_emails)[:8],
        "socials": all_socials,
        "phones":  sorted(all_phones)[:4],
    }


@app.post("/extract-links")
async def extract_links(req: ScrapeRequest):
    """
    Extracts all links from a URL or raw HTML.
    """
    from scrapling.parser import Selector

    try:
        html_content = req.html

        if not html_content:
            async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                headers = {"User-Agent": "Mozilla/5.0 Chrome/122"}
                r = await client.get(req.url, headers=headers)
                r.raise_for_status()
                # Respect declared charset (e.g. iso-8859-1)
                content_type = r.headers.get("content-type", "")
                if "charset=" in content_type:
                    charset = content_type.split("charset=")[-1].split(";")[0].strip()
                    try:
                        html_content = r.content.decode(charset, errors="replace")
                    except (LookupError, UnicodeDecodeError):
                        html_content = r.text
                else:
                    html_content = r.text

        page = Selector(html_content)
        links = page.css("a::attr(href)").getall()
        texts = page.css("a::text").getall()

        result = [
            {"href": h, "text": t}
            for h, t in zip(links, texts)
            if h and h.startswith("http")
        ]

        return {"url": req.url, "count": len(result), "links": result}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ResearchRequest(BaseModel):
    company: str          # company name  e.g. "A3M Asesoría"
    url: Optional[str] = None   # website if known
    location: Optional[str] = None  # city/country hint


@app.post("/research")
async def research(req: ResearchRequest):
    """
    Multi-source company research:
    - DuckDuckGo web search
    - Spanish business directories (Einforma, Axesor, Infocif, PáginaAmarillas)
    - LinkedIn company search
    - Website enrichment (if url provided)
    Returns aggregated: emails, phones, socials, description, address, employees hint
    """
    from scrapling.parser import Selector
    import re, asyncio

    EMAIL_RE  = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
    PHONE_RE  = re.compile(r'(?:\+34\s?|0034\s?)?[679]\d{2}[\s\.\-]?\d{3}[\s\.\-]?\d{3}')
    JUNK_MAIL = {"sentry","webpack","example","domain","email@","user@","test@","noreply@",
                 "no-reply@","ejemplo","correo@","tunombre@","yourname@","nombre@",
                 "wixpress","schema.org","w3.org","openstreetmap"}
    SOCIAL_RE = [
        ("linkedin",  re.compile(r'linkedin\.com/company/([A-Za-z0-9._\-]{2,})', re.I)),
        ("facebook",  re.compile(r'(?:facebook\.com|fb\.com)/([A-Za-z0-9._\-]{3,})', re.I)),
        ("instagram", re.compile(r'instagram\.com/([A-Za-z0-9._]{3,})', re.I)),
        ("twitter",   re.compile(r'(?:twitter\.com|x\.com)/([A-Za-z0-9._]{2,})', re.I)),
    ]
    SKIP_SOCIAL = {"sharer","share","home","index","login","signup","about","watch","intent",
                   "in","company","posts","search","feed"}

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "es-ES,es;q=0.9",
    }

    all_emails:  set = set()
    all_phones:  set = set()
    all_socials: dict = {}
    sources_hit: list = []
    snippets:    list = []

    def clean_phone(p: str) -> str:
        return re.sub(r'[\s\.\-]', '', p)

    def extract_all(html: str, source_label: str = ""):
        for e in EMAIL_RE.findall(html):
            if not any(k in e.lower() for k in JUNK_MAIL):
                all_emails.add(e.lower())
        for p in PHONE_RE.findall(html):
            cp = clean_phone(p)
            if len(cp) >= 9:
                all_phones.add(cp)
        for name, pattern in SOCIAL_RE:
            if name in all_socials:
                continue
            m = pattern.search(html)
            if m and m.group(1).lower() not in SKIP_SOCIAL:
                all_socials[name] = m.group(1)

    async def fetch_safe(client, url, label):
        try:
            r = await client.get(url, headers=headers, timeout=12, follow_redirects=True)
            if r.is_success:
                ct = r.headers.get("content-type","")
                if "charset=" in ct:
                    cs = ct.split("charset=")[-1].split(";")[0].strip()
                    try: return r.content.decode(cs, errors="replace")
                    except: pass
                return r.text
        except Exception:
            pass
        return ""

    company_q = req.company
    loc_q = f" {req.location}" if req.location else ""

    async with httpx.AsyncClient() as client:

        # ── 1. DuckDuckGo HTML search ────────────────────────────────────────
        ddg_queries = [
            f'"{company_q}" email contacto{loc_q}',
            f'"{company_q}" teléfono dirección{loc_q}',
        ]
        for q in ddg_queries:
            html = await fetch_safe(
                client,
                f"https://html.duckduckgo.com/html/?q={httpx.URL('', params={'q': q}).params}",
                "duckduckgo"
            )
            if html:
                page = Selector(html)
                # Extract result snippets
                for snippet in page.css(".result__snippet, .result__body").getall()[:8]:
                    clean = re.sub(r'<[^>]+>', '', snippet).strip()
                    if clean and clean not in snippets:
                        snippets.append(clean)
                extract_all(html, "duckduckgo")
                sources_hit.append("duckduckgo")

        # ── 2. Páginas Amarillas ──────────────────────────────────────────────
        pa_slug = company_q.lower().replace(' ', '-')
        pa_loc  = (req.location or "").lower().replace(' ', '-')
        pa_url  = f"https://www.paginasamarillas.es/search/{pa_slug}/all-ma/all-is/all-ba/all-zn/all-ar/all-da/1"
        html = await fetch_safe(client, pa_url, "paginasamarillas")
        if html and company_q.lower().split()[0] in html.lower():
            extract_all(html, "paginasamarillas")
            sources_hit.append("paginasamarillas")

        # ── 3. Infocif (Spanish company registry) ────────────────────────────
        infocif_url = f"https://infocif.es/buscador?nombre={httpx.URL('',params={'nombre':company_q}).params}"
        html = await fetch_safe(client, infocif_url, "infocif")
        if html:
            extract_all(html, "infocif")
            # Try to get NIF / description
            page = Selector(html)
            cif_match = re.search(r'\b[A-Z]\d{7}[A-Z0-9]\b', html)
            if cif_match:
                snippets.insert(0, f"CIF: {cif_match.group()}")
            sources_hit.append("infocif")

        # ── 4. Axesor ─────────────────────────────────────────────────────────
        axesor_url = f"https://www.axesor.es/buscar?q={httpx.URL('',params={'q':company_q}).params}"
        html = await fetch_safe(client, axesor_url, "axesor")
        if html:
            extract_all(html, "axesor")
            sources_hit.append("axesor")

        # ── 5. LinkedIn company search ────────────────────────────────────────
        li_url = f"https://www.linkedin.com/company/{pa_slug}/"
        html = await fetch_safe(client, li_url, "linkedin")
        if html and 'linkedin' not in all_socials:
            m = re.search(r'linkedin\.com/company/([A-Za-z0-9._\-]+)', html)
            if m:
                all_socials['linkedin'] = m.group(1)
            sources_hit.append("linkedin")

        # ── 6. Website enrichment (if url provided) ───────────────────────────
        if req.url:
            base = req.url.rstrip("/")
            subpages = [base, f"{base}/contacto", f"{base}/contact",
                        f"{base}/contactar", f"{base}/about", f"{base}/sobre-nosotros"]
            # Auto-discover links
            home_html = await fetch_safe(client, base, "website")
            if home_html:
                page = Selector(home_html)
                kw = re.compile(r'contact|contacto|contactar|about|sobre|equipo', re.I)
                for href in page.css("a::attr(href)").getall():
                    if not href or href.startswith(("http","#","mailto","tel","javascript")):
                        continue
                    if kw.search(href):
                        full = f"{base}/{href.lstrip('/')}"
                        if full not in subpages:
                            subpages.append(full)
            for url in subpages:
                html = await fetch_safe(client, url, "website")
                if html:
                    extract_all(html, "website")
            sources_hit.append("website")

    # Clean up
    final_emails = sorted(e for e in all_emails
                          if not any(k in e.lower() for k in JUNK_MAIL))[:8]
    final_phones = sorted(
        {p for p in all_phones if len(re.sub(r'\D','',p)) in (9,11,12)},
        key=lambda p: (0 if p.startswith('+') else 1)
    )[:5]

    return {
        "company":  req.company,
        "emails":   final_emails,
        "phones":   final_phones,
        "socials":  all_socials,
        "snippets": snippets[:6],
        "sources":  list(dict.fromkeys(sources_hit)),
    }
