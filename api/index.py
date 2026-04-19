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
