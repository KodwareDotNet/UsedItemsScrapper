#!/usr/bin/env python3
"""Server-side OLX scraper — replaces the browser javascript_tool route.

Writes ../raw/olx_rows.txt as
  id|model|variant|price_lacs|year|km|searchedCity|area|ago|region|picId

OLX quietly ignores the city in the URL when a keyword has few local matches,
so the raw sweep pulls in Karachi/Lahore/Sialkot cars. Everything is filtered
down to Islamabad/Rawalpindi (region 'core') or the listed nearby towns
('near'); expect roughly 300-500 rows to survive out of ~1,800.

Rate-limit / block handling: GitHub Actions runners share IP ranges with a
huge number of other bots, so OLX throttles/blocks them far more aggressively
than a residential IP ever gets throttled. Several things guard against that:
  - requests go through curl_cffi (falling back to plain `requests` if it
    isn't installed) so the TLS/HTTP2 handshake matches a real Chrome build
    instead of python-requests' default fingerprint. Plenty of "why am I
    getting 403s with perfect headers" cases come from the WAF fingerprinting
    the TLS ClientHello, not the headers — headers alone can't fix that.
  - a real Chrome-like header set (sec-ch-ua*, sec-fetch-*, Accept-Encoding)
    goes out with every request instead of the ~4 headers a bare script
    usually sends.
  - the session visits the homepage once before hitting search URLs, so it
    picks up whatever cookies OLX's edge sets on a normal first visit rather
    than jumping straight to a deep search URL with an empty cookie jar.
  - every request backs off hard (exponential + jitter, respects
    Retry-After) before retrying a 403/429
  - a circuit breaker watches for BLOCK_THRESHOLD consecutive exhausted
    retries and aborts the whole harvest immediately instead of grinding
    through the rest of ~40 keywords while blocked — that only deepens
    the block and burns CI minutes for zero additional data.
On an aborted/short run, main() refuses to overwrite olx_rows.txt with a
too-small result (see the len(rows) < 80 check), so the previous good
snapshot stays live and refresh.py treats it as "continuing with the
previous OLX rows" rather than failing the whole pipeline.

None of this beats a real residential IP outright — if blocks persist even
with all of the above, the reliable fix is moving this one script to run
somewhere with a non-datacenter IP (e.g. a self-hosted Actions runner on a
home machine) rather than squeezing more out of a shared CI IP.
"""
import os, re, sys, time, random, subprocess, json
from datetime import datetime, timezone
from bs4 import BeautifulSoup

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
PAGES = 3
BASE_DELAY = 3.0      # floor of a jittered per-request delay
DELAY_JITTER = 2.5    # actual delay is BASE_DELAY + uniform(0, DELAY_JITTER)
TIMEOUT = 30
RETRIES = 3
BLOCK_THRESHOLD = 4   # consecutive fully-retried 403/429s -> stop the whole harvest

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
    'alto', 'wagon r', 'mira', 'move', 'every', 'hijet', 'ek wagon', 'n box', 'dayz',
    'n one', 'n wgn', 'moco', 'roox', 'carol', 'flair', 'spacia', 'hustler', 'lapin',
    'tanto', 'cast', 'pixis', 'pajero mini', 'minicab', 'scrum', 'clipper', 'life',
    'zest', 'terios kid', 'mr wagon', 'palette', 'esse', 'stella', 'dias', 'vamos',
    'acty', 'town box', 'minica', 'az wagon', 'laputa',
    # deep set: sellers who title by trim or by "660cc"/"JDM" rather than model
    '660cc', 'kei', 'jdm', 'japanese', 'imported', 'n-box', 'n-wgn', 'wagonr',
    'mira es', 'mira cocoa', 'move conte', 'every wagon', 'hijet cargo', 'clipper rio',
    'pixis epoch', 'alto lapin', 'ek custom', 'spacia custom', 'tanto custom',
    'minicab bravo', 'wagon r stingray', 'dayz highway star',
]

NEAR = ['Wah', 'Taxila', 'Attock', 'Murree', 'Jhelum', 'Gujar Khan', 'Hasan Abdal',
        'Dina', 'Sarai Alamgir', 'Kahuta', 'Mandra', 'Hazro']
NEAR_RE = [re.compile(r'(^|[,\s])' + re.escape(n) + r'\b', re.I) for n in NEAR]
CORE_RE = re.compile(r',\s*(Islamabad|Rawalpindi)$', re.I)

# Only genuine Japanese kei models are kept. This whitelist is what keeps Mehran,
# Bolan, Cultus, City and the rest of the keyword-search noise out of the data.
MODELS = sorted([
    'Suzuki Alto Lapin', 'Suzuki Alto', 'Suzuki Wagon R', 'Suzuki Every', 'Suzuki Hustler',
    'Suzuki Spacia', 'Suzuki MR Wagon', 'Suzuki Palette', 'Suzuki Cervo', 'Suzuki Kei', 'Suzuki Twin',
    'Daihatsu Mira ES', 'Daihatsu Mira Cocoa', 'Daihatsu Mira', 'Daihatsu Move Conte',
    'Daihatsu Move', 'Daihatsu Hijet', 'Daihatsu Tanto', 'Daihatsu Esse', 'Daihatsu Cast',
    'Daihatsu Wake', 'Daihatsu Atrai Wagon', 'Daihatsu Atrai', 'Daihatsu Terios Kid',
    'Daihatsu Copen', 'Daihatsu Naked',
    'Honda N Box', 'Honda N One', 'Honda N Wgn', 'Honda N-Box', 'Honda N-One', 'Honda N-Wgn',
    'Honda Life', 'Honda Zest', 'Honda Acty', 'Honda Vamos', 'Honda That S', 'Honda Beat',
    'Nissan Moco', 'Nissan Roox', 'Nissan Dayz Roox', 'Nissan Dayz', 'Nissan Clipper',
    'Nissan Otti', 'Nissan Pino', 'Nissan Kix',
    'Mitsubishi EK Custom', 'Mitsubishi EK Wagon', 'Mitsubishi EK Space', 'Mitsubishi Minicab',
    'Mitsubishi Pajero Mini', 'Mitsubishi Town Box', 'Mitsubishi Minica', 'Mitsubishi Toppo',
    'Mazda Flair', 'Mazda Carol', 'Mazda Scrum', 'Mazda AZ Wagon', 'Mazda Laputa', 'Mazda Spiano',
    'Subaru Pleo', 'Subaru Stella', 'Subaru Dias Wagon', 'Subaru Dias', 'Subaru Sambar',
    'Subaru R2', 'Subaru Vivio',
    'Toyota Pixis',
], key=len, reverse=True)

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
    text (or an odd 'ago' segment) carried a stray '|', because only `variant`
    and `area` were being scrubbed and any other field could still smuggle a
    delimiter through. Newlines get the same treatment: one embedded newline
    would split a row in two and desync every field after it.
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
    """Fetch a URL. Returns the HTML text, None for a clean 404, or raises
    Blocked if 403/429/other-non-200 responses survive every retry."""
    was_throttled = False
    for attempt in range(RETRIES):
        try:
            r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.text
            if r.status_code == 404:
                return None
            # Any other non-200 (403/429, but also 500/502/503/520/522 etc.)
            # is treated as a block. WAFs/CDNs frequently answer automated
            # traffic with a plain error status rather than a clean 403/429,
            # and retrying those instantly with no backoff (the previous
            # behavior) just hammered the block harder and then silently
            # gave up, showing up in the log as "no response, skipping".
            was_throttled = True
            retry_after = r.headers.get('Retry-After')
            if retry_after and retry_after.strip().isdigit():
                wait = min(90, int(retry_after))
            else:
                wait = min(60, 5 * (2 ** attempt)) + random.uniform(0, 3)
            print(f'      -> blocked (HTTP {r.status_code}), waiting {wait:.0f}s...', flush=True)
            time.sleep(wait)
            continue
        except requests.RequestException as e:
            wait = 2 * (attempt + 1) + random.uniform(0, 2)
            print(f'      -> connection error: {e}, waiting {wait:.0f}s...', flush=True)
            time.sleep(wait)
            continue
    if was_throttled:
        raise Blocked(url)
    return None


def harvest():
    """Collect {id: (card_text, thumbnail_id, searched_city)} across every keyword.

    Bails out early via Blocked if BLOCK_THRESHOLD consecutive requests get
    stuck behind rate-limiting, instead of ploughing through the remaining
    keywords while OLX is actively throttling us.
    """
    found = {}
    session = requests.Session(impersonate=IMPERSONATE) if IMPERSONATE else requests.Session()
    jsonl_file = os.path.join(ROOT, 'raw', 'olx_live.jsonl')
    consecutive_blocks = 0

    # Warm up like a real visitor landing on the homepage first, rather than
    # jumping straight into a deep search URL with an empty cookie jar.
    try:
        warm_headers = dict(HEADERS, **{'Sec-Fetch-Site': 'none', 'Sec-Fetch-User': '?1'})
        session.get('https://www.olx.com.pk/', headers=warm_headers, timeout=TIMEOUT)
        time.sleep(BASE_DELAY + random.uniform(0, DELAY_JITTER))
    except requests.RequestException as e:
        print(f'  warm-up request failed ({e}), continuing anyway', flush=True)

    for kw in KEYWORDS:
        before = len(found)
        print(f'  [{kw!r}] starting...', flush=True)
        for city in CITIES:
            for page in range(1, PAGES + 1):
                url = (f'https://www.olx.com.pk/{city}/cars_c84/q-{requests.utils.quote(kw)}'
                       f'?filter=price_between_0_to_3000000' + (f'&page={page}' if page > 1 else ''))
                print(f'    fetching page {page}...', flush=True)
                try:
                    html = get(session, url)
                except Blocked:
                    consecutive_blocks += 1
                    print(f'    page {page}: still blocked after retries '
                          f'({consecutive_blocks}/{BLOCK_THRESHOLD} consecutive)', flush=True)
                    if consecutive_blocks >= BLOCK_THRESHOLD:
                        print(f'ABORT: {consecutive_blocks} consecutive requests were rate-limited — '
                              f'OLX is actively throttling this IP. Stopping now instead of grinding '
                              f'through the remaining {len(KEYWORDS) - KEYWORDS.index(kw)} keywords; '
                              f'the previous olx_rows.txt will be left in place.', file=sys.stderr, flush=True)
                        return found
                    break
                consecutive_blocks = 0
                if not html:
                    print(f'    page {page}: no response, skipping to next keyword', flush=True)
                    break
                soup = BeautifulSoup(html, 'lxml')
                anchors = soup.select('a[href*="-iid-"]')
                if not anchors:
                    break
                fresh, seen_href = 0, set()
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
                    found[m.group(1)] = (
                        re.sub(r'\s+', ' ', card.get_text()).strip()[:260],
                        pic.group(1) if pic else '',
                        'isb' if city.startswith('islamabad') else 'rwp',
                    )
                    fresh += 1
                if not fresh:
                    break
                time.sleep(BASE_DELAY + random.uniform(0, DELAY_JITTER))

        # Save keyword batch to JSONL and commit
        added = len(found) - before
        if added > 0:
            for ad_id in list(found.keys())[before:]:
                blob, pic, searched = found[ad_id]
                with open(jsonl_file, 'a', encoding='utf-8') as fh:
                    json.dump({'id': ad_id, 'blob': blob, 'pic': pic, 'searched': searched}, fh)
                    fh.write('\n')
            # Commit this keyword's batch. Check return codes instead of
            # assuming success — a misconfigured git identity (or any other
            # commit failure) used to be masked by an unconditional "OK"
            # print, which made real failures invisible in the run log.
            subprocess.call(['git', 'add', 'kei-tracker/raw/olx_live.jsonl'])
            commit_rc = subprocess.call(['git', 'commit', '-m', f'scrape: OLX {kw!r} +{added}'])
            if commit_rc == 0:
                push_rc = subprocess.call(['git', 'push'])
                if push_rc == 0:
                    print(f'  OK committed {added} records', flush=True)
                else:
                    print(f'  WARNING: commit succeeded but push failed (rc={push_rc}) '
                          f'— progress is saved locally but not backed up yet', file=sys.stderr, flush=True)
            else:
                print(f'  WARNING: git commit failed (rc={commit_rc}) — is git identity '
                      f'configured? Progress for this batch is only in the working tree.',
                      file=sys.stderr, flush=True)
        print(f'  {kw!r}: +{added} (total {len(found)})')
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

        model = next((m for m in MODELS if title.lower().startswith(m.lower())), None)
        if not model:
            drop_model += 1
            continue
        if model.lower().startswith('suzuki wagon r') and not WAGONR_OK.search(title):
            drop_model += 1
            continue

        variant = title[len(model):].strip()
        kept.append('|'.join(cell(v) for v in [ad_id, model, variant, f'{lacs:g}', year, km,
                                               searched, area, compress_ago(ago), region, pic]))
    print(f'OLX: kept {len(kept)} of {len(found)} '
          f'(dropped {drop_loc} out-of-area, {drop_model} not-a-kei, {drop_parse} unparseable)')
    return kept


def main():
    jsonl_file = os.path.join(RAW, 'olx_live.jsonl')

    # Clear previous live file
    if os.path.exists(jsonl_file):
        os.remove(jsonl_file)

    found = harvest()  # Already writes to JSONL and commits per keyword
    rows = parse(found)
    if len(rows) < 80:
        print(f'ABORT: only {len(rows)} OLX rows survived, expected several hundred '
              f'(likely rate-limited — see log above). Leaving the previous olx_rows.txt in place.',
              file=sys.stderr)
        return 1
    with open(os.path.join(RAW, 'olx_rows.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(rows) + '\n')
    # Freshness is recorded in a file rather than inferred from mtime: git does
    # not preserve mtimes, so on a fresh checkout every file looks brand new.
    with open(os.path.join(RAW, 'olx_captured_at.txt'), 'w') as fh:
        fh.write(datetime.now(timezone.utc).isoformat(timespec='seconds'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
