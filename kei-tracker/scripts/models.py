#!/usr/bin/env python3
"""Single source of truth for which cars the tracker tracks.

Edit THIS file to add or drop a model. Both scrapers and the builder import
from here, so one edit lands everywhere. The lists used to be copy-pasted into
three files, which is how 'passo' ended up added to two of them in a form
neither could actually match.

Two tiers:
  KEI    660cc Japanese kei cars      (PakWheels engine filter 600-660)
  SMALL  1000cc / 1300cc JDM hatches  (PakWheels engine filter 670-1350)

VANS is a deny-list of commercial van / loader bodies, dropped from both tiers.

Keys in PW_SLUG are PakWheels URL-slug prefixes ('toyota-passo' matches the ad
slug 'toyota-passo-x-2010-148881333'); values are the display name, which is
also what OLX ad titles are matched against. Longest key wins, so
'daihatsu-mira-es' is checked before 'daihatsu-mira'.
"""

# ---------------------------------------------------------------- vans (drop)
# Cargo / loader bodies. Passenger tall-boys (N Box, Tanto, Spacia, Wake ...)
# are NOT vans and stay in.
VANS = [
    'Daihatsu Hijet', 'Daihatsu Atrai Wagon', 'Daihatsu Atrai',
    'Suzuki Every Wagon', 'Suzuki Every', 'Suzuki Carry', 'Suzuki Ravi',
    'Mazda Scrum', 'Mitsubishi Minicab', 'Mitsubishi Town Box',
    'Nissan Clipper', 'Nissan NV100', 'Honda Acty', 'Honda Vamos',
    'Subaru Sambar', 'Subaru Dias Wagon', 'Subaru Dias', 'Toyota Pixis Van',
]
VAN_SLUGS = [
    'daihatsu-hijet', 'daihatsu-atrai', 'suzuki-every', 'suzuki-carry',
    'suzuki-ravi', 'mazda-scrum', 'mitsubishi-minicab', 'mitsubishi-town-box',
    'nissan-clipper', 'nissan-nv100', 'honda-acty', 'honda-vamos',
    'subaru-sambar', 'subaru-dias',
]

# --------------------------------------------------------- tier 1: 660cc kei
KEI_SLUG = {
    'suzuki-alto-lapin': 'Suzuki Alto Lapin',
    'suzuki-alto': 'Suzuki Alto',
    'suzuki-wagon-r': 'Suzuki Wagon R',
    'suzuki-hustler': 'Suzuki Hustler',
    'suzuki-spacia-gear': 'Suzuki Spacia Gear',
    'suzuki-spacia': 'Suzuki Spacia',
    'suzuki-mr-wagon': 'Suzuki MR Wagon',
    'suzuki-palette': 'Suzuki Palette',
    'suzuki-cervo': 'Suzuki Cervo',
    'suzuki-kei': 'Suzuki Kei',
    'suzuki-twin': 'Suzuki Twin',
    'suzuki-jimny': 'Suzuki Jimny',
    'jimny': 'Suzuki Jimny',
    'daihatsu-mira-es': 'Daihatsu Mira ES',
    'daihatsu-mira-cocoa': 'Daihatsu Mira Cocoa',
    'daihatsu-mira': 'Daihatsu Mira',
    'daihatsu-move-conte': 'Daihatsu Move Conte',
    'daihatsu-move': 'Daihatsu Move',
    'daihatsu-tanto': 'Daihatsu Tanto',
    'daihatsu-esse': 'Daihatsu Esse',
    'daihatsu-cast': 'Daihatsu Cast',
    'daihatsu-wake': 'Daihatsu Wake',
    'daihatsu-terios-kid': 'Daihatsu Terios Kid',
    'daihatsu-copen': 'Daihatsu Copen',
    'daihatsu-naked': 'Daihatsu Naked',
    'honda-n-box': 'Honda N Box',
    'honda-none': 'Honda N One',
    'honda-n-one': 'Honda N One',
    'honda-n-wgn': 'Honda N Wgn',
    'honda-life': 'Honda Life',
    'honda-zest': 'Honda Zest',
    'honda-that-s': 'Honda That S',
    'honda-beat': 'Honda Beat',
    'nissan-moco': 'Nissan Moco',
    'nissan-dayz-roox': 'Nissan Dayz Roox',
    'nissan-dayz': 'Nissan Dayz',
    'nissan-roox': 'Nissan Roox',
    'nissan-otti': 'Nissan Otti',
    'nissan-pino': 'Nissan Pino',
    'mitsubishi-ek-custom': 'Mitsubishi EK Custom',
    'mitsubishi-ek-wagon': 'Mitsubishi EK Wagon',
    'mitsubishi-ek-space': 'Mitsubishi EK Space',
    'mitsubishi-pajero-mini': 'Mitsubishi Pajero Mini',
    'mitsubishi-minica': 'Mitsubishi Minica',
    'mitsubishi-toppo': 'Mitsubishi Toppo',
    'mazda-flair': 'Mazda Flair',
    'mazda-carol': 'Mazda Carol',
    'mazda-az-wagon': 'Mazda AZ Wagon',
    'mazda-laputa': 'Mazda Laputa',
    'mazda-spiano': 'Mazda Spiano',
    'subaru-pleo': 'Subaru Pleo',
    'subaru-stella': 'Subaru Stella',
    'subaru-r2': 'Subaru R2',
    'subaru-vivio': 'Subaru Vivio',
    'toyota-pixis': 'Toyota Pixis',
    # Added Sep 2026 after sweeping the raw 600-660cc band and finding these
    # sitting in Islamabad/Rawalpindi with nothing on the allow-list to catch
    # them. All genuine JDM kei; none of them are locally assembled.
    'mitsubishi-i': 'Mitsubishi i',
    'daihatsu-sonica': 'Daihatsu Sonica',
    'nissan-kix': 'Nissan Kix',
    'mazda-az-offroad': 'Mazda AZ Offroad',
    'subaru-chiffon': 'Subaru Chiffon',
}

# ------------------------------------------- tier 2: 1000cc / 1300cc JDM small
SMALL_SLUG = {
    'toyota-passo-sette': 'Toyota Passo Sette',
    'toyota-passo': 'Toyota Passo',
    'toyota-vitz': 'Toyota Vitz',
    'toyota-belta': 'Toyota Belta',
    'toyota-porte': 'Toyota Porte',
    'toyota-ractis': 'Toyota Ractis',
    'toyota-spade': 'Toyota Spade',
    'toyota-roomy': 'Toyota Roomy',
    'toyota-tank': 'Toyota Tank',
    'toyota-raize': 'Toyota Raize',
    'toyota-iq': 'Toyota IQ',
    'daihatsu-boon': 'Daihatsu Boon',
    'daihatsu-sirion': 'Daihatsu Sirion',
    'daihatsu-storia': 'Daihatsu Storia',
    'daihatsu-thor': 'Daihatsu Thor',
    'daihatsu-rocky': 'Daihatsu Rocky',
    'daihatsu-coo': 'Daihatsu Coo',
    'daihatsu-materia': 'Daihatsu Materia',
    'nissan-march': 'Nissan March',
    'nissan-note': 'Nissan Note',
    'nissan-cube': 'Nissan Cube',
    'honda-fit-shuttle': 'Honda Fit Shuttle',
    'honda-fit': 'Honda Fit',
    'honda-brio': 'Honda Brio',
    'honda-insight': 'Honda Insight',
    'suzuki-swift': 'Suzuki Swift',
    'suzuki-baleno': 'Suzuki Baleno',
    'suzuki-ignis': 'Suzuki Ignis',
    'suzuki-solio': 'Suzuki Solio',
    'suzuki-splash': 'Suzuki Splash',
    'mitsubishi-mirage': 'Mitsubishi Mirage',
    'mitsubishi-colt': 'Mitsubishi Colt',
    'toyota-platz': 'Toyota Platz',
    'suzuki-sierra': 'Suzuki Sierra',
}

PW_SLUG = dict(KEI_SLUG, **SMALL_SLUG)

# Display names, longest first — used for OLX title-prefix matching.
MODEL_NAMES = sorted(set(PW_SLUG.values()), key=len, reverse=True)
KEI_NAMES = sorted(set(KEI_SLUG.values()), key=len, reverse=True)
SMALL_NAMES = sorted(set(SMALL_SLUG.values()), key=len, reverse=True)

_VANS_LC = [v.lower() for v in VANS]


def is_van(name):
    """True for a commercial van / loader body, which we do not track."""
    n = (name or '').lower()
    return any(v in n for v in _VANS_LC)


def is_van_slug(slug):
    s = (slug or '').lower()
    return any(v in s for v in VAN_SLUGS)


def tier(name):
    """'kei' for 660cc models, 'small' for the 1000/1300cc tier, '' otherwise."""
    if name in KEI_NAMES:
        return 'kei'
    if name in SMALL_NAMES:
        return 'small'
    return ''


# ------------------------------------------------------------------- aliases
# OLX sellers often drop the make: "Passo X 2012", "Mira ES 2019", "Vitz F 2011".
# A full-name prefix match ('Toyota Passo') misses every one of those, so these
# distinctive bare model names are accepted too. Only names that are unambiguous
# on their own belong here — 'Note', 'March', 'Fit', 'Move' and 'Cast' are left
# out on purpose because they are ordinary English words that appear in the
# middle of unrelated ad titles.
ALIASES = {
    'passo': 'Toyota Passo', 'vitz': 'Toyota Vitz', 'belta': 'Toyota Belta',
    'ractis': 'Toyota Ractis', 'porte': 'Toyota Porte', 'raize': 'Toyota Raize',
    'boon': 'Daihatsu Boon', 'sirion': 'Daihatsu Sirion', 'storia': 'Daihatsu Storia',
    'mira es': 'Daihatsu Mira ES', 'mira cocoa': 'Daihatsu Mira Cocoa',
    'mira': 'Daihatsu Mira', 'move conte': 'Daihatsu Move Conte',
    'tanto': 'Daihatsu Tanto', 'esse': 'Daihatsu Esse', 'wake': 'Daihatsu Wake',
    'terios kid': 'Daihatsu Terios Kid', 'copen': 'Daihatsu Copen',
    'alto lapin': 'Suzuki Alto Lapin', 'lapin': 'Suzuki Alto Lapin',
    'wagon r': 'Suzuki Wagon R', 'hustler': 'Suzuki Hustler',
    'spacia': 'Suzuki Spacia', 'mr wagon': 'Suzuki MR Wagon',
    'palette': 'Suzuki Palette', 'cervo': 'Suzuki Cervo', 'jimny': 'Suzuki Jimny',
    'solio': 'Suzuki Solio', 'ignis': 'Suzuki Ignis', 'splash': 'Suzuki Splash',
    'n box': 'Honda N Box', 'n-box': 'Honda N Box', 'n one': 'Honda N One',
    'n-one': 'Honda N One', 'n wgn': 'Honda N Wgn', 'n-wgn': 'Honda N Wgn',
    'zest': 'Honda Zest', 'vamos': 'Honda Vamos',
    'dayz roox': 'Nissan Dayz Roox', 'dayz': 'Nissan Dayz', 'roox': 'Nissan Roox',
    'moco': 'Nissan Moco', 'otti': 'Nissan Otti', 'pino': 'Nissan Pino',
    'ek custom': 'Mitsubishi EK Custom', 'ek wagon': 'Mitsubishi EK Wagon',
    'ek space': 'Mitsubishi EK Space', 'pajero mini': 'Mitsubishi Pajero Mini',
    'toppo': 'Mitsubishi Toppo', 'mirage': 'Mitsubishi Mirage',
    'flair': 'Mazda Flair', 'carol': 'Mazda Carol', 'az wagon': 'Mazda AZ Wagon',
    'laputa': 'Mazda Laputa', 'spiano': 'Mazda Spiano',
    'pleo': 'Subaru Pleo', 'stella': 'Subaru Stella', 'vivio': 'Subaru Vivio',
    'pixis': 'Toyota Pixis',
    'sonica': 'Daihatsu Sonica', 'kix': 'Nissan Kix',
    'az offroad': 'Mazda AZ Offroad', 'az-offroad': 'Mazda AZ Offroad',
    'chiffon': 'Subaru Chiffon', 'platz': 'Toyota Platz',
}
ALIAS_KEYS = sorted(ALIASES, key=len, reverse=True)


def match_title(title):
    """Map an OLX ad title to a tracked model name, or None.

    Tries the full display name first ('Toyota Passo ...'), then the bare-model
    aliases above. Returns None for vans and for anything off the list.
    """
    t = (title or '').strip().lower()
    for name in MODEL_NAMES:
        if t.startswith(name.lower()):
            return None if is_van(name) else name
    for k in ALIAS_KEYS:
        if t.startswith(k) and (len(t) == len(k) or not t[len(k)].isalnum()):
            name = ALIASES[k]
            return None if is_van(name) else name
    return None
