#!/usr/bin/env python3
"""Server-side OLX scraper — keyword-based sweep across Islamabad + Rawalpindi.

Writes ../raw/olx_rows.txt as
  id|model|variant|price_lacs|year|km|searchedCity|area|ago|region|picId

Strategy: sweep ~45 URLs (19 keywords × 2 cities × 3 pages) using keyword-
specific car-category searches. KEYWORDS was trimmed from 40 to 19 entries
(removed low-yield noise and the 'alto' model that dominates results).
All raw blobs are saved to JSONL; parse() filters by model with a keyword-
fallback for ads mentioning '660cc'/'jdm' without a known model prefix.

Rate-limit / block handling: uses curl_cffi TLS fingerprinting, realistic
Chrome headers, homepage warm-up, exponential backoff on 403/429. Circuit
breaker at BLOCK_THRESHOLD=8 (up from 4) to tolerate transient 429s without
aborting the entire harvest early. Delays increased to 6-9s/request for better
chance of OLX tolerating datacenter traffic.

On fewer than 10 filtered rows, main() leaves the previous olx_rows.txt in
place so refresh.py continues with existing OLX data instead of failing.
"""
import os, re, sys, time, random, subprocess, json
from datetime import datetime, timezone
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from models import MODEL_NAMES, is_van, match_title

try:
    from curl_cffi import requests  # TLS/HTTP2 fingerprint matches real Chrome
    IMPERSONATE = 'chrome124'
except ImportError:  # pragma: no cover - fallback when curl_cffi isn't installed
    import requests
    IMPERSONATE = None
    print('WARNING: curl_cffi not installed, falling back to plain requests '
          '(pip install curl_cffi to get TLS-fingerprint impersonation)', file=sys.stderr)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(ROOT, 'raw')
os.makedirs(RAW, exist_ok=True)

CITIES = ['islamabad_g4060615', 'rawalpindi_g4060681']
PAGES = 1       # OLX returns most listings on page 1; pages 2-3 rarely add value
BASE_DELAY = 30   # seconds between requests — OLX tolerates very slow traffic from datacenters
DELAY_JITTER = 30 # actual delay is BASE_DELAY + uniform(0, DELAY_JITTER)
TIMEOUT = 60      # some pages take a long time to respond
RETRIES = 2       # fewer retries per request; we have the inter-request cooldown for recovery
BLOCK_THRESHOLD = 4  # consecutive fully-retried 403/429s -> stop the whole harvest
MIN_RATELIMIT_WAIT = 60  # never wait less than this on 429 — a 0s Retry-After means "you're done"

# A few realistic desktop UAs; one is picked per process run (a real browser
# keeps the same UA for its whole session, so we do too, rather than
# rotating per-request which is itself a bot fingerprint).
USER_AGENTS = [
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
]
_UA = random.choice(USER_AGENTS)
HEADERS = {
    'User-Agent': _UA,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Referer': 'https://www.olx.com.pk/',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Ch-Ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"macOS"',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'same-origin',
    'Sec-Fetch-User': '?1',
    'DNT': '1',
}

KEYWORDS = [
    # 660cc kei — these actually match kei car titles on OLX
    'jimny', 'wagon r', 'mira', 'move conte', 'n box', 'dayz',
    'hustler', 'carol', 'flair', 'tanto', 'cast', 'lapin',
    # 1000cc / 1300cc JDM hatches
    'passo', 'boon', 'vitz', 'march', 'note', 'honda fit', 'belta',
    'porte', 'ractis', 'sirion', 'mirage', 'raize', 'roomy',
    # Deep set: sellers who title by trim/"660cc" rather than model name
    '660cc', '1000cc', 'kei car', 'jdm spec', 'japanese import',
]

# Per-keyword cooldown — OLX rate-limits burst traffic. Adding a pause between
# keywords helps reset the window and avoids cascading 429s across all searches.
KEYWORD_COOLDOWN = 30  # seconds between keywords

NEAR = ['Wah', 'Taxila', 'Attock', 'Murree', 'Jhelum', 'Gujar Khan', 'Hasan Abdal',
        'Dina', 'Sarai Alamgir', 'Kahuta', 'Mandra', 'Hazro']
NEAR_RE = [re.compile(r'(^|[,\s])' + re.escape(n) + r'\b', re.I) for n in NEAR]
CORE_RE = re.compile(r',\s*(Islamabad|Rawalpindi)$', re.I)

# The whitelist lives in models.py so the two scrapers and the builder cannot
# drift apart. It is what keeps Mehran, Bolan, Cultus, City and the rest of the
# keyword-search noise out of the data — and, since the vans were dropped, what
# keeps Hijet/Every/Carry/Scrum out too.
#
# Matching is on the FULL display name ('Toyota Passo'), not a bare model word:
# OLX titles read "Toyota Passo X 2012", so a whitelist entry of just 'passo'
# never matched a single ad. That was the Passo bug on this side.
MODELS = MODEL_NAMES

FUEL_RE = re.compile(r'^(Petrol|Diesel|Hybrid|LPG|CNG|Electric)', re.I)
UNIT = {'minute': 'm', 'hour': 'h', 'day': 'd', 'week': 'w', 'month': 'mo', 'year': 'y'}
# Pakistani-assembled Wagon R is 1000cc, so only genuine JDM trims are kept.
WAGONR_OK = re.compile(r'stingray|\bfx\b|\bfz\b|hybrid', re.I)


class Blocked(Exception):
    """Raised when a request exhausts its retries because of 403/429s
    specifically (as opposed to a 404 or a one-off connection error)."""


def cell(v):
    """Make a value safe for the pipe-delimited dump.

    Every field goes through this, not just the obvious free-text ones. The
    build used to die with "Expected 11 fields, saw 13" whenever a seller's
    text carried a stray '|', because only `variant` and `area` were scrubbed
    and any other field could still smuggle a delimiter through. The keyword
    fallback below made this much more likely: an 'Other Kei' match puts the
    seller's entire untouched ad title into `variant`. Newlines get the same
    treatment — one embedded newline would split a row in two and desync every
    field after it.
    """
    return re.sub(r'\s+', ' ', str(v).replace('|', ' ')).strip()


def compress_ago(s):
    s = re.sub(r'\s*ago.*$', '', s or '').strip()
    if re.fullmatch(r'today', s, re.I):
        return '1h'
    if re.fullmatch(r'yesterday', s, re.I):
        return '1d'
    m = re.match(r'^(\d+)\s*(minute|hour|day|week|month|year)s?$', s, re.I)
    return f'{m.group(1)}{UNIT[m.group(2).lower()]}' if m else re.sub(r'\s+', '', s)


def get(session, url):
    """Fetch a URL. Returns the HTML text, None for a clean 404 or unexpected error,
    or raises Blocked if 403/429 responses survive every retry."""
    was_throttled = False
    for attempt in range(RETRIES):
        try:
            r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
            content_len = len(r.content)
            ct = (r.headers.get('Content-Type') or '')[:50]
            preview = (r.text[:120]).replace('\n', ' ')

            if r.status_code == 200:
                return r.text
            # Diagnostic log for every non-200 response so we can tell exactly what OLX returned
            print(f'      -> HTTP {r.status_code} ({content_len}B, CT={ct}): {preview}', flush=True)
            if r.status_code in (403, 429):
                was_throttled = True
                retry_after = r.headers.get('Retry-After')
                if retry_after and retry_after.strip().isdigit():
                    wait = min(90, int(retry_after))
                else:
                    wait = min(90, 60 * (2 ** attempt)) + random.uniform(0, 5)
                # Never sleep less than MIN_RATELIMIT_WAIT — a 0s Retry-After means "you're done"
                wait = max(wait, MIN_RATELIMIT_WAIT)
                print(f'      -> rate limited (HTTP {r.status_code}), waiting {wait:.0f}s...', flush=True)
                time.sleep(wait)
                continue
            if r.status_code == 404:
                return None
            # Non-403/non-429/non-404 = give up immediately (no point retrying)
            print(f'      -> non-retriable HTTP {r.status_code}, giving up', flush=True)
            return None
        except requests.RequestException as e:
            wait = 3 * (attempt + 1) + random.uniform(0, 2)
            print(f'      -> connection error {e.__class__.__name__}: {str(e)[:80]}, retrying ({attempt+1}/{RETRIES})', flush=True)
            time.sleep(wait)
            continue
    if was_throttled:
        raise Blocked(url)
    return None


def broad_harvest():
    """Keyword-based sweep across Islamabad + Rawalpindi, collecting ALL listing cards.

    Sweeps KEYWORDS (trimmed to high-yield kei models) × CITIES × PAGES.
    Uses a generous circuit breaker (BLOCK_THRESHOLD=8) to tolerate normal
    transient 429s without aborting the entire harvest early.
    All raw blobs are saved; model filtering happens later in parse().
    """
    found = {}
    session = requests.Session(impersonate=IMPERSONATE) if IMPERSONATE else requests.Session()
    jsonl_file = os.path.join(ROOT, 'raw', 'olx_live.jsonl')
    consecutive_blocks = 0
    total_rate_limited = 0  # lifetime 429s seen — stops the sweep once this threshold is hit

    # Warm up like a real visitor landing on the homepage first.
    try:
        warm_headers = dict(HEADERS, **{'Sec-Fetch-Site': 'none', 'Sec-Fetch-User': '?1'})
        session.get('https://www.olx.com.pk/', headers=warm_headers, timeout=TIMEOUT)
        time.sleep(BASE_DELAY + random.uniform(0, DELAY_JITTER))
    except requests.RequestException as e:
        print(f'  warm-up request failed ({e}), continuing anyway', flush=True)

    # Build list of all URLs to try (KEYWORDS × CITIES × PAGES).
    # Track which keyword each URL belongs to for cooldown purposes.
    urls = []  # list of (kw, url) tuples
    for kw in KEYWORDS:
        for city in ['islamabad_g4060615', 'rawalpindi_g4060681']:
            for page in range(1, PAGES + 1):
                qs = requests.utils.quote(kw)
                urls.append((kw, f'https://www.olx.com.pk/{city}/cars_c84/q-{qs}'
                            f'?filter=price_between_0_to_3000000'
                            + (f'&page={page}' if page > 1 else '')))

    print(f'  building keyword-sweep: {len(urls)} URLs to try ({len(KEYWORDS)} keywords x '
          f'{len(CITIES)} cities x {PAGES} pages)', flush=True)

    total_fresh = 0
    prev_before = len(found)
    url_idx = 0
    last_kw = None
    for kw, url in urls:
        url_idx += 1
        short_q = url.split('q-')[1].split('&')[0][:40] if 'q-' in url else url[:50]

        # Cooldown between keywords — helps reset OLX rate-limit windows
        if last_kw is not None and kw != last_kw:
            print(f'  [{kw}] cooling down {KEYWORD_COOLDOWN}s (new keyword) ...', flush=True)
            time.sleep(KEYWORD_COOLDOWN)

        print(f'    URL {url_idx}/{len(urls)} ({kw}) ... [filter=30lac]...', flush=True)
        try:
            html = get(session, url)
        except Blocked:
            consecutive_blocks += 1
            total_rate_limited += 1
            if consecutive_blocks >= BLOCK_THRESHOLD or total_rate_limited >= 3:
                print(f'ABORT: {consecutive_blocks} consecutive + {total_rate_limited} total '
                      f'rate-limits — OLX has flagged this IP. Stopped after '
                      f'{len(found)} listings.', file=sys.stderr, flush=True)
                return found
            continue  # try next keyword/page
        consecutive_blocks = 0
        if not html:
            print(f'    get() returned None — check "HTTP" or "connection error" lines above', flush=True)
            continue

        soup = BeautifulSoup(html, 'lxml')
        anchors = soup.select('a[href*="-iid-"]')
        if not anchors:
            print(f'    no listing cards found, trying next', flush=True)
            continue

        seen_href = set()
        fresh = 0
        for a in anchors:
            href = (a.get('href') or '').split('?')[0]
            if href in seen_href:
                continue
            seen_href.add(href)
            m = re.search(r'-iid-(\d+)', href)
            if not m or m.group(1) in found:
                continue
            card = a.find_parent('li') or a.parent
            if card is None:
                continue
            img = card.find('img')
            src = (img.get('src') or img.get('data-src') or '') if img else ''
            pic = re.search(r'thumbnails/(\d+)-', src)
            city_key = 'isb' if 'islamabad' in url else 'rwp'
            found[m.group(1)] = (
                re.sub(r'\s+', ' ', card.get_text()).strip()[:260],
                pic.group(1) if pic else '',
                city_key,
            )
            fresh += 1

        total_fresh += fresh
        added = len(found) - prev_before
        print(f'    +{fresh} new this page ({added} fresh; total {len(found)})', flush=True)
        prev_before = len(found)
        time.sleep(BASE_DELAY + random.uniform(0, DELAY_JITTER))
        last_kw = kw

    # Write JSONL snapshot.
    if os.path.exists(jsonl_file):
        os.remove(jsonl_file)
    for ad_id, (blob, pic, searched) in found.items():
        with open(jsonl_file, 'a', encoding='utf-8') as fh:
            json.dump({'id': ad_id, 'blob': blob, 'pic': pic, 'searched': searched}, fh)
            fh.write('\n')
    print(f'  broad harvest complete: {total_fresh} fresh, {len(found)} total unique', flush=True)
    return found


def parse(found):
    kept, drop_loc, drop_model, drop_parse = [], 0, 0, 0
    for ad_id, (blob, pic, searched) in found.items():
        parts = [p.strip() for p in blob.split('•')]
        # anchor on the fuel+area segment: [price+title+year] . [km or New] . [PetrolArea] . [ago]
        ai = next((i for i, p in enumerate(parts) if FUEL_RE.match(p) and len(p) > 8), -1)
        if ai < 1 or ai + 1 >= len(parts):
            drop_parse += 1
            continue

        area = FUEL_RE.sub('', parts[ai]).strip()
        ago = re.sub(r'(Call|Chat|Featured)+$', '', parts[ai + 1]).strip()
        km_part = parts[ai - 1]
        km = re.sub(r'\D', '', km_part) if re.search(r'km', km_part, re.I) else ''

        head = parts[0]
        pm = re.search(r'Rs\s*([\d.,]+)\s*(Lacs?|Crore)?', head, re.I)
        if not pm:
            drop_parse += 1
            continue
        lacs = float(pm.group(1).replace(',', ''))
        unit = pm.group(2) or ''
        if re.search(r'crore', unit, re.I):
            lacs *= 100
        elif not re.search(r'lac', unit, re.I):
            lacs /= 100000

        rest = head[head.index(pm.group(0)) + len(pm.group(0)):].strip()
        ym = re.search(r'(\d{4})$', rest)
        year = ym.group(1) if ym else ''
        if ym:
            rest = rest[:-4]
        title = rest.strip()

        if CORE_RE.search(area):
            region = 'core'
        elif any(r.search(area) for r in NEAR_RE):
            region = 'near'
        else:
            drop_loc += 1
            continue

        # match_title() handles both 'Toyota Passo X 2012' and the bare
        # 'Passo X 2012' that OLX sellers actually type, and returns None for
        # vans so Hijet/Every/Carry/Scrum never reach the page.
        model = match_title(title)

        # Keyword fallback: broad harvest pulls mixed listings, so accept ads that
        # advertise themselves as JDM imports even when no model name matched.
        if not model:
            jdm_keywords = ['660cc', '1000cc', 'kei car', 'kei', 'jdm spec', 'jdm',
                            'japanese made', 'japanese import']
            check_text = f' {title.lower()} {area.lower()} '
            if any(kw in check_text for kw in jdm_keywords) and not is_van(title):
                model = 'Other JDM'  # sentinel — variant gets full title below
            else:
                drop_model += 1
                continue
        if model.lower().startswith('suzuki wagon r') and not WAGONR_OK.search(title):
            drop_model += 1
            continue

        # When model is a sentinel from keyword fallback, don't strip — use full title as variant.
        if len(model) > 0 and title.lower().startswith(model.lower()):
            variant = title[len(model):].strip()
        else:
            variant = title.strip()
        kept.append('|'.join(cell(v) for v in [ad_id, model, variant, f'{lacs:g}', year, km,
                                               searched, area, compress_ago(ago), region, pic]))
    print(f'OLX: kept {len(kept)} of {len(found)} '
          f'(dropped {drop_loc} out-of-area, {drop_model} not-a-kei, {drop_parse} unparseable)')
    return kept


def load_jsonl():
    """Re-read the last harvest's raw blobs from disk.

    The harvest is the slow, rate-limited part; filtering is pure local logic.
    Whenever models.py changes, `--reparse` rebuilds olx_rows.txt from the blobs
    already on disk instead of spending 40 minutes re-fetching pages OLX has
    already given us.
    """
    path = os.path.join(RAW, 'olx_live.jsonl')
    if not os.path.exists(path):
        print(f'ABORT: {path} not found — run a real harvest first.', file=sys.stderr)
        return {}
    found = {}
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            found[d['id']] = (d.get('blob', ''), d.get('pic', ''), d.get('searched', ''))
    print(f'  reparse: {len(found)} raw listings loaded from olx_live.jsonl')
    return found


def main():
    reparse = '--reparse' in sys.argv
    found = load_jsonl() if reparse else broad_harvest()
    if not found:
        return 1

    rows = parse(found)
    # Broad harvest yields fewer kei-car matches than old keyword-by-keyword sweep,
    # so abort at 10 instead of 80 — any fresh data is better than an empty page.
    if len(rows) < 10:
        print(f'WARN: only {len(rows)} OLX rows survived the filter '
              f'(broad harvest returned {len(found)} raw listings). '
              f'Leaving previous olx_rows.txt in place.', file=sys.stderr)
        return 1
    with open(os.path.join(RAW, 'olx_rows.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(rows) + '\n')
    # Freshness is recorded in a file rather than inferred from mtime: git does
    # not preserve mtimes, so on a fresh checkout every file looks brand new.
    # Freshness is NOT restamped on --reparse: the blobs are as old as the last
    # real harvest, and pretending otherwise would let refresh.py skip OLX and
    # make the page claim data it does not have.
    if not reparse:
        with open(os.path.join(RAW, 'olx_captured_at.txt'), 'w') as fh:
            fh.write(datetime.now(timezone.utc).isoformat(timespec='seconds'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
