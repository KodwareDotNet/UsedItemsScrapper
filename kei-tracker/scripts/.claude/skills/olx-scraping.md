---
name: olx-scraping
description: Handle OLX scraping for used cars — rate-limit tolerance and anti-blocking strategies
---

# OLX Scraping Strategy

## When to use
This skill applies whenever scraping OLX.com.pk (or similar classifieds sites) faces bot detection, rate-limiting (HTTP 429), or IP blocking.

## Key Principles

### 1. Keyword-based search over broad-category pages
OLX's car category pages are rendered client-side via JavaScript. Server HTML contains **no listing cards** (`<a href*="-iid-">` elements). Use keyword-specific search URLs instead:
```
https://www.olx.com.pk/{city}/cars_c84/q-{keyword}?filter=price_between_0_to_3000000
```
Where `city` = `islamabad_g4060615` or `rawalpindi_g4060681`, and the price filter is in raw PKR (3,000,000 = 30 lac).

### 2. Minimize keywords aggressively
- Remove low-yield models that generate thousands of non-relevant results (e.g., Suzuki Alto)
- Keep only high-yield kei car keywords: `jimny`, `wagon r`, `mira`, `move conte`, `every wagon`, `n box`, `dayz`, `hustler`, `spacia`, `hijet`, `carol`, `flair`, `tanto`, `cast`, `lapin`
- Add deep-set: `660cc`, `kei car`, `jdm spec`, `japanese import`
- Target: ~15-20 keywords max, not 40+

### 3. Aggressive rate-limit tolerance settings
```python
BASE_DELAY = 30          # seconds between requests (minimum)
DELAY_JITTER = 30        # additional random delay (actual delay = BASE_DELAY + uniform(0, DELAY_JITTER))
TIMEOUT = 60             # generous request timeout
RETRIES = 2              # few retries per request; inter-request cooldown handles recovery
BLOCK_THRESHOLD = 4      # consecutive fully-retried 403/429s → abort sweep
MIN_RATELIMIT_WAIT = 60  # never sleep less than this on a 429
                         # A Retry-After=0 means "you're done," not "try now"
```

### 4. Total rate-limit cap (not just consecutive)
Track `total_rate_limited` across the entire sweep. If it reaches 3, abort immediately — OLX has flagged your IP and no amount of delay will help from that IP.

### 5. Circuit breaker behavior
```python
if r.status_code in (403, 429):
    was_throttled = True
    retry_after = r.headers.get('Retry-After')
    if retry_after and retry_after.strip().isdigit():
        wait = min(90, int(retry_after))
    else:
        wait = min(90, 60 * (2 ** attempt)) + random.uniform(0, 5)
    wait = max(wait, MIN_RATELIMIT_WAIT)  # NEVER less than 60s!
```

### 6. Keyword-level cooldown
Add `KEYWORD_COOLDOWN = 30` seconds when switching to a new keyword — this helps reset OLX rate-limit windows between different searches.

### 7. Per-page delay
Add `time.sleep(BASE_DELAY + random.uniform(0, DELAY_JITTER))` after each successful page fetch.

### 8. TLS fingerprinting with curl_cffi
Always use `curl_cffi` for TLS/HTTP2 fingerprint matching real Chrome. Plain `requests` gets flagged faster:
```python
from curl_cffi import requests
IMPERSONATE = 'chrome124'
session = requests.Session(impersonate=IMPERSONATE)
```

### 9. Always include curl_cffi in requirements.txt
Without it, fall back to plain `requests` which has a botter fingerprint and gets blocked more easily.

## Anti-Patterns to Avoid

- **Never skip inter-request delays** — even 5 seconds is too fast for datacenter IPs on OLX
- **Don't trust Retry-After=0** — it means "don't come back," not "try again immediately"
- **Don't scrape broad-category URLs** — they're JS-only; server HTML has no listing content
- **Don't use 3+ pages per keyword** — OLX returns most listings on page 1; pages 2-3 rarely add value and burn your rate-limit budget
- **Don't skip the homepage warm-up** — visit `https://www.olx.com.pk/` first to pick up edge cookies

## Diagnostic Checklist

When an OLX scrape fails, check for:
1. `-> HTTP XXX (YB, CT=...)` — what status code/content type does OLX return?
2. `-> suspicious small response` — less than expected content (possible captcha)
3. `-> connection error` — timeout or DNS failure
4. `ABORT: X consecutive + Y total rate-limits` — IP is flagged, nothing will help from this IP

## Common Failure Modes

| Symptom | Cause | Fix |
|---|---|---|
| All keywords hit with 0s delays between retries | Retry-After=0 treated as immediate retry | Use `MIN_RATELIMIT_WAIT = 60` |
| Works for first keyword, fails on second | IP flagged during first burst | Longer per-request delay (30s+), smaller keyword list |
| Empty results on broad category | JS-rendered pages with no server HTML | Use keyword-specific URLs instead |
| Consistent 429s across runs | Datacenter IP blocked | Self-hosted runner on residential IP needed |

## Reference Files
- `scrape_olx.py` — main scraper implementation
- `build_all.py` — parsing and model filtering (line ~103)
