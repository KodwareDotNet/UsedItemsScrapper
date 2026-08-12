#!/usr/bin/env python3
"""Server-side PakWheels scraper — replaces the browser javascript_tool route.

Writes, into ../raw/ :
  pw_core.txt  id|pkr|ago                       for EVERY live ad
  pw_new.txt   the full 12-field row            only for ids missing from state/pw_static.csv
  pw_rows.txt  emptied (its presence would put build_all.py into full-dump mode)

Field order of the 12-field row must stay exactly:
  id|title|pkr|ago|loc|spec|badge|rating|pics|cache|slug|path
because build_all.py reads it positionally.
"""
import csv, json, os, re, sys, time
import requests
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(ROOT, 'raw')
ST = os.path.join(ROOT, 'state')
os.makedirs(RAW, exist_ok=True)

BASE = 'https://www.pakwheels.com/used-cars/search/-/ct_islamabad/ct_rawalpindi/pr_0_3000000/ec_600_660/yr_2010_2026/'
MAX_PAGES = 45
DELAY = 0.5          # be polite; the browser version used 0.17s
TIMEOUT = 30
RETRIES = 3

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


def compress_ago(text):
    """'Updated about 3 hours ago' -> '3h'.  Matches the JS comp() exactly."""
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
        except requests.RequestException as e:
            last = str(e)
            time.sleep(2 * (attempt + 1))
    print(f'  ! giving up on {url}: {last}', file=sys.stderr)
    return None


def scrape():
    rows = {}
    session = requests.Session()
    for page in range(1, MAX_PAGES + 1):
        url = BASE + (f'?page={page}' if page > 1 else '')
        html = get(session, url)
        if html is None:
            break
        soup = BeautifulSoup(html, 'lxml')
        cards = soup.select('li.classified-listing')
        if not cards:
            break
        fresh = 0
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
                im.group(2) if im else '',
                path,
            ])
            fresh += 1
        print(f'  page {page}: {fresh} new (total {len(rows)})')
        if not fresh:
            break
        time.sleep(DELAY)
    return rows


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
    print(f'PakWheels: {len(rows)} live ads, {len(new)} needing a static record')
    return 0


if __name__ == '__main__':
    sys.exit(main())
