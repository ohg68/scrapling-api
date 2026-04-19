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
                html_content = r.text

        page = Selector(html_content)
        selector = req.css or "body"
        elements = page.css(selector)
        texts = [el.get_text(separator=" ", strip=True) for el in elements]

        return {"url": req.url, "texts": texts}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
