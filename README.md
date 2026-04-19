# Scrapling Parser API — Vercel Deploy

API REST de parsing HTML construida con [Scrapling](https://github.com/D4Vinci/Scrapling) + FastAPI.  
Funciona en Vercel sin browsers (parser puro + httpx para fetch básico).

## Endpoints

| Method | Path | Descripción |
|--------|------|-------------|
| GET | `/` | Health check |
| POST | `/parse` | CSS o XPath selector sobre URL o HTML raw |
| POST | `/extract-text` | Texto limpio desde URL o HTML |
| POST | `/extract-links` | Todos los links desde URL o HTML |

## Uso

### `/parse` — Con URL
```json
POST /parse
{
  "url": "https://quotes.toscrape.com/",
  "css": ".quote .text::text"
}
```

### `/parse` — Con HTML directo (sin fetch)
```json
POST /parse
{
  "html": "<html>...</html>",
  "css": "h1::text"
}
```

### `/extract-links`
```json
POST /extract-links
{
  "url": "https://example.com"
}
```

## Deploy en Vercel

```bash
# 1. Clonar / fork este repo en tu GitHub (ohg68)
git clone https://github.com/ohg68/scrapling-api
cd scrapling-api

# 2. Deploy con Vercel CLI
npm i -g vercel
vercel

# O conectar el repo desde vercel.com → New Project
```

## Limitaciones en Vercel

- ✅ Parser HTML (CSS, XPath, text, links)
- ✅ Fetch HTTP básico con httpx
- ❌ StealthyFetcher (requiere Playwright)
- ❌ DynamicFetcher (requiere Chromium)
- ⏱️ Timeout máximo: 10s (Vercel Hobby) / 60s (Pro)

Para scraping avanzado con anti-bot bypass → Railway + Docker.
