#!/usr/bin/env python3
"""Server-side PakWheels scraper.

Two engine sweeps, because the models Atif wants do not share one cc band:
  600-660   the 660cc kei cars
  670-1350  the 1000cc / 1300cc JDM hatches (Passo, Boon, Vitz, March, Fit ...)
The second sweep also returns Mehran, Corolla, City and friends; models.py's
PW_SLUG allow-list is what keeps them out. This is the reason Passo never
showed up before: the only sweep was ec_600_660, and a Passo is 1000cc, so no
amount of fixing the model lists downstream could have made it appear.

Writes, into ../raw/ :
  pw_core.txt    id|pkr|ago                 for EVERY live ad
  pw_new.txt     the full 12-field row      only for ids missing from state/pw_static.csv
  pw_detail.csv  id,color,reg_city,assembly,cc,body   persistent enrichment cache
  pw_rows.txt    emptied (its presence would put build_all.py into full-dump mode)

Field order of the 12-field row must stay exactly:
  id|title|pkr|ago|loc|spec|badge|rating|pics|cache|slug|path
because build_all.py reads it positionally. Colour / registration city do NOT
live in that row — they come from the detail page, one extra request per ad, so
they are cached separately in pw_detail.csv and merged by id at build time.
"""
import csv, json, os, re, sys, time
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from models import PW_SLUG, is_van_slug

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(ROOT, 'raw')
ST = os.path.join(ROOT, 'state')
os.makedirs(RAW, exist_ok=True)

CITY = 'ct_islamabad/ct_rawalpindi'
PRICE = 'pr_0_3000000'
# No year segment in these URLs on purpose. PakWheels supports one (yr_2010_2026)
# and it used to be here, but it is a hard gate: a 2004 Passo is never fetched,
# so nothing downstream can put it back. Age is a judgement call per car, so it
# belongs in the page's "Year from" filter, which is applied after the fact.
SWEEPS = [
    ('660cc kei', f'https://www.pakwheels.com/used-cars/search/-/{CITY}/{PRICE}/ec_600_660/'),
    ('1000-1300cc', f'https://www.pakwheels.com/used-cars/search/-/{CITY}/{PRICE}/ec_670_1350/'),
]
MAX_PAGES = 45
DELAY = 0.5          # be polite
TIMEOUT = 30
RETRIES = 3

# How many detail pages to fetch per run. Only ads with no cached colour need
# one, so after the first backfill this is a handful per run. Capped so a cold
# cache costs a few slow minutes rather than an unbounded stall.
ENRICH_BUDGET = int(os.environ.get('PW_ENRICH_BUDGET', '900'))
ENRICH_DELAY = 0.35

HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

BADGES = {'Managed by PakWheels': 'M', 'PakWheels Certified': 'C',
          'PakWheels Inspected': 'I', 'Auction Sheet Verified': 'A'}
BADGE_RE = re.compile('|'.join(map(re.escape, BADGES)))
UNIT = {'minute': 'm', 'hour': 'h', 'day': 'd', 'week': 'w', 'month': 'mo', 'year': 'y'}

DETAIL_FIELDS = ['id', 'color', 'reg_city', 'assembly', 'cc', 'body']
DETAIL_LABELS = {'Registered In': 'reg_city', 'Color': 'color',
                 'Assembly': 'assembly', 'Engine Capacity': 'cc', 'Body Type': 'body'}

# Slug prefixes checked longest-first so 'daihatsu-mira-es' beats 'daihatsu-mira'.
SLUG_KEYS = sorted(PW_SLUG, key=len, reverse=True)


def wanted(slug):
    """True if this ad's slug is one of the models we track and not a van."""
    base = re.sub(r'-\d{4}-\d+$', '', slug or '').lower()
    if is_van_slug(base):
        return False
    return any(base.startswith(k) for k in SLUG_KEYS)


def compress_ago(text):
    """'Updated about 3 hours ago' -> '3h'."""
    s = re.sub(r'^Updated\s*', '', text or '', flags=re.I)
    s = re.sub(r'\s*ago$', '', s, flags=re.I)
    s = re.sub(r'^about\s*', '', s, flags=re.I).strip()
    m = re.match(r'^(a|an|\d+)\s*(minute|hour|day|week|month|year)s?$', s, re.I)
    if not m:
        # 'less than a minute' and friends -> treat as brand new, not as junk
        if re.search(r'less than a minute|just now|second', s, re.I):
            return '1m'
        return re.sub(r'\s+', '', s)
    n = 1 if m.group(1).lower() in ('a', 'an') else m.group(1)
    return f'{n}{UNIT[m.group(2).lower()]}'


def text_of(node, selector):
    el = node.select_one(selector)
    return re.sub(r'\s+', ' ', el.get_text()).strip() if el else ''


def get(session, url):
    last = None
    for attempt in range(RETRIES):
        try:
            r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.text
            last = f'HTTP {r.status_code}'
            # 403/429 means we are being throttled — back off hard before retrying
            if r.status_code in (403, 429):
                time.sleep(5 * (attempt + 1))
            elif r.status_code == 404:
                return None
        except requests.RequestException as e:
            last = str(e)
            time.sleep(2 * (attempt + 1))
    print(f'  ! giving up on {url}: {last}', file=sys.stderr)
    return None


def scrape_sweep(session, label, base, rows):
    """Add every wanted ad from one engine-capacity sweep into `rows`."""
    kept_here = 0
    for page in range(1, MAX_PAGES + 1):
        url = base + (f'?page={page}' if page > 1 else '')
        html = get(session, url)
        if html is None:
            break
        soup = BeautifulSoup(html, 'lxml')
        cards = soup.select('li.classified-listing')
        if not cards:
            break
        fresh = skipped = 0
        for c in cards:
            a = c.select_one('a[href*="-for-sale-in-"]')
            if not a:
                continue
            path = (a.get('href') or '').split('?')[0]
            m = re.search(r'-(\d+)$', path)
            if not m:
                continue
            ad_id = m.group(1)
            if ad_id in rows:
                continue

            ld, img = {}, ''
            tag = c.select_one('script[type="application/ld+json"]')
            if tag:
                try:
                    ld = json.loads(tag.string or tag.get_text())
                    img = ld.get('image', '') or ''
                except (ValueError, TypeError):
                    pass
            im = re.match(r'^https://cache(\d)\.pakwheels\.com/ad_pictures/\d+/(.+)\.jpg$', img)
            slug = im.group(2) if im else ''
            if not wanted(slug or path.split('/')[-1]):
                skipped += 1
                continue

            blob = re.sub(r'\s+', ' ', c.get_text())
            bm = BADGE_RE.search(blob)
            rm = re.search(r'(\d(?:\.\d)?)/10', blob)

            rows[ad_id] = '|'.join([
                ad_id,
                text_of(c, '.search-title').replace(' for Sale', '').replace('|', ' '),
                str((ld.get('offers') or {}).get('price', '')),
                compress_ago(text_of(c, '.dated')),
                text_of(c, '.search-vehicle-info').replace('|', ' '),
                text_of(c, '.search-vehicle-info-2').replace('|', ' '),
                BADGES.get(bm.group(0), '') if bm else '',
                rm.group(1) if rm else '',
                re.sub(r'\D', '', text_of(c, '.total-pictures-bar')),
                im.group(1) if im else '',
                slug,
                path,
            ])
            fresh += 1
            kept_here += 1
        print(f'  [{label}] page {page}: {fresh} kept, {skipped} off-list (total {len(rows)})')
        if not fresh and not skipped:
            break
        time.sleep(DELAY)
    return kept_here


def scrape():
    rows = {}
    session = requests.Session()
    counts = {}
    for label, base in SWEEPS:
        counts[label] = scrape_sweep(session, label, base, rows)
    for label, n in counts.items():
        print(f'  {label}: {n} ads')
    return rows


# ------------------------------------------------------------------ enrichment
def load_detail_cache():
    path = os.path.join(RAW, 'pw_detail.csv')
    if not os.path.exists(path):
        return {}
    with open(path, newline='', encoding='utf-8') as fh:
        return {r['id']: r for r in csv.DictReader(fh) if r.get('id')}


def save_detail_cache(cache):
    path = os.path.join(RAW, 'pw_detail.csv')
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=DETAIL_FIELDS)
        w.writeheader()
        for rec in cache.values():
            w.writerow({k: rec.get(k, '') for k in DETAIL_FIELDS})


def parse_detail(html):
    """Pull the spec table off an ad page.

    PakWheels renders it as a flat run of label/value cells inside
    #scroll_car_detail, so we read it as consecutive pairs rather than trying
    to pin down a row structure that changes between the grid and list layouts.
    """
    soup = BeautifulSoup(html, 'lxml')
    tbl = soup.select_one('#scroll_car_detail')
    if not tbl:
        return {}
    cells = [re.sub(r'\s+', ' ', c.get_text()).strip() for c in tbl.select('li,td')]
    out = {}
    for i, c in enumerate(cells):
        if c in DETAIL_LABELS and i + 1 < len(cells):
            out[DETAIL_LABELS[c]] = cells[i + 1].replace('|', ' ')
    return out


def enrich(rows, cache):
    """Fetch colour / registered-city / assembly for ads we have not seen before.

    One request per ad, so this is the expensive part of the run — but only on a
    cold cache. Ads already in pw_detail.csv are never refetched: colour and
    registration city do not change over an ad's life.
    """
    todo = [i for i in rows if i not in cache]
    if not todo:
        print('PakWheels detail: cache complete, nothing to fetch')
        return 0
    budget = min(len(todo), ENRICH_BUDGET)
    print(f'PakWheels detail: {len(todo)} ads need enrichment, fetching {budget} '
          f'this run (~{budget * ENRICH_DELAY / 60:.1f} min)')
    session = requests.Session()
    done = 0
    for n, ad_id in enumerate(todo[:budget], 1):
        path = rows[ad_id].split('|')[11]
        html = get(session, 'https://www.pakwheels.com' + path)
        if html:
            d = parse_detail(html)
            if d:
                cache[ad_id] = dict(d, id=ad_id)
                done += 1
            else:
                # Record the miss so a page we cannot parse is not retried every run.
                cache[ad_id] = {'id': ad_id}
        else:
            cache[ad_id] = {'id': ad_id}
        if n % 50 == 0:
            print(f'    {n}/{budget} detail pages ({done} parsed)', flush=True)
            save_detail_cache(cache)   # checkpoint, so an abort keeps progress
        time.sleep(ENRICH_DELAY)
    print(f'PakWheels detail: {done}/{budget} parsed')
    return done


def known_ids():
    path = os.path.join(ST, 'pw_static.csv')
    if not os.path.exists(path):
        return set()
    with open(path, newline='', encoding='utf-8') as fh:
        return {r['id'] for r in csv.DictReader(fh) if r.get('id')}


def main():
    rows = scrape()
    if len(rows) < 200:
        # A healthy run returns ~700. Anything far below that means we were
        # blocked or the markup changed — refuse to overwrite good data with junk.
        print(f'ABORT: only {len(rows)} ads scraped, expected several hundred. '
              f'Refusing to write a truncated snapshot.', file=sys.stderr)
        return 1

    with open(os.path.join(RAW, 'pw_core.txt'), 'w', encoding='utf-8') as fh:
        for row in rows.values():
            p = row.split('|')
            fh.write(f'{p[0]}|{p[2]}|{p[3]}\n')

    seen = known_ids()
    new = [r for i, r in rows.items() if i not in seen]
    with open(os.path.join(RAW, 'pw_new.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(new) + ('\n' if new else ''))

    open(os.path.join(RAW, 'pw_rows.txt'), 'w').close()

    cache = load_detail_cache()
    enrich(rows, cache)
    # Drop cache entries for ads that are long gone, so the file does not grow
    # without bound; keep anything still live.
    save_detail_cache({i: r for i, r in cache.items() if i in rows})

    print(f'PakWheels: {len(rows)} live ads, {len(new)} needing a static record')
    return 0


if __name__ == '__main__':
    sys.exit(main())
