#!/usr/bin/env python3
"""Server-side OLX scraper — replaces the browser javascript_tool route.

Writes ../raw/olx_rows.txt as
  id|model|variant|price_lacs|year|km|searchedCity|area|ago|region|picId

OLX quietly ignores the city in the URL when a keyword has few local matches,
so the raw sweep pulls in Karachi/Lahore/Sialkot cars. Everything is filtered
down to Islamabad/Rawalpindi (region 'core') or the listed nearby towns
('near'); expect roughly 300-500 rows to survive out of ~1,800.
"""
import os, re, sys, time
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(ROOT, 'raw')
os.makedirs(RAW, exist_ok=True)

CITIES = ['islamabad_g4060615', 'rawalpindi_g4060681']
PAGES = 3
DELAY = 0.2  # Reduced from 0.4 to speed up scraping (saves ~1.5 minutes)
TIMEOUT = 30
RETRIES = 3

HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
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


def compress_ago(s):
    s = re.sub(r'\s*ago.*$', '', s or '').strip()
    if re.fullmatch(r'today', s, re.I):
        return '1h'
    if re.fullmatch(r'yesterday', s, re.I):
        return '1d'
    m = re.match(r'^(\d+)\s*(minute|hour|day|week|month|year)s?$', s, re.I)
    return f'{m.group(1)}{UNIT[m.group(2).lower()]}' if m else re.sub(r'\s+', '', s)


def get(session, url):
    for attempt in range(RETRIES):
        try:
            r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.text
            if r.status_code in (403, 429):
                time.sleep(5 * (attempt + 1))
            elif r.status_code == 404:
                return None
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
    return None


def harvest():
    """Collect {id: (card_text, thumbnail_id, searched_city)} across every keyword."""
    found = {}
    session = requests.Session()
    for kw in KEYWORDS:
        before = len(found)
        for city in CITIES:
            for page in range(1, PAGES + 1):
                url = (f'https://www.olx.com.pk/{city}/cars_c84/q-{requests.utils.quote(kw)}'
                       f'?filter=price_between_0_to_3000000' + (f'&page={page}' if page > 1 else ''))
                html = get(session, url)
                if not html:
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
                time.sleep(DELAY)
        print(f'  {kw!r}: +{len(found) - before} (total {len(found)})')
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

        variant = title[len(model):].strip().replace('|', ' ')
        kept.append('|'.join([ad_id, model, variant, f'{lacs:g}', year, km,
                              searched, area.replace('|', ' '), compress_ago(ago), region, pic]))
    print(f'OLX: kept {len(kept)} of {len(found)} '
          f'(dropped {drop_loc} out-of-area, {drop_model} not-a-kei, {drop_parse} unparseable)')
    return kept


def main():
    rows = parse(harvest())
    if len(rows) < 80:
        print(f'ABORT: only {len(rows)} OLX rows survived, expected several hundred. '
              f'Leaving the previous olx_rows.txt in place.', file=sys.stderr)
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
