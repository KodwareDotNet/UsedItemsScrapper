#!/usr/bin/env python3
"""One full refresh cycle: scrape -> stamp capture time -> build.

Run from anywhere:  python3 kei-tracker/scripts/refresh.py

OLX is only re-scraped when its data is older than OLX_MAX_AGE_HOURS (default 36),
because OLX moves far more slowly than PakWheels and the sweep is ~350 requests.
Pass --force-olx to override, or --skip-olx to never touch it.
"""
import argparse, os, subprocess, sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(ROOT, 'raw')
TZ = ZoneInfo(os.environ.get('TZ', 'Asia/Karachi'))
OLX_MAX_AGE_HOURS = float(os.environ.get('OLX_MAX_AGE_HOURS', '36'))


def run(script, label):
    print(f'\n=== {label} ===', flush=True)
    rc = subprocess.call([sys.executable, os.path.join(HERE, script)])
    print(f'=== {label}: {"ok" if rc == 0 else f"FAILED (rc={rc})"} ===', flush=True)
    return rc


def olx_age_hours():
    """Age of the OLX data, read from a stamp file.

    Deliberately not os.path.getmtime(): git does not preserve mtimes, so after
    a fresh checkout every file looks seconds old and OLX would never re-scrape.
    """
    rows = os.path.join(RAW, 'olx_rows.txt')
    if not os.path.exists(rows) or os.path.getsize(rows) == 0:
        return float('inf')
    stamp = os.path.join(RAW, 'olx_captured_at.txt')
    if not os.path.exists(stamp):
        return float('inf')
    try:
        when = datetime.fromisoformat(open(stamp).read().strip())
    except ValueError:
        return float('inf')
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds() / 3600


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force-olx', action='store_true')
    ap.add_argument('--skip-olx', action='store_true')
    args = ap.parse_args()

    if run('scrape_pakwheels.py', 'PakWheels') != 0:
        print('PakWheels scrape failed — not rebuilding, '
              'the previous page stays live.', file=sys.stderr)
        return 1

    age = olx_age_hours()
    if args.skip_olx:
        print(f'\nOLX: skipped by request (data is {age:.1f}h old)')
    elif args.force_olx or age > OLX_MAX_AGE_HOURS:
        if run('scrape_olx.py', 'OLX') != 0:
            print('OLX scrape failed — continuing with the previous OLX rows.',
                  file=sys.stderr)
    else:
        print(f'\nOLX: skipped, data is only {age:.1f}h old '
              f'(threshold {OLX_MAX_AGE_HOURS}h)')

    # Save scraped raw data to git immediately (before build/upload can fail)
    print('\n=== Saving scraped data to git ===', flush=True)
    subprocess.call(['git', 'add', 'kei-tracker/raw'], cwd=os.path.dirname(ROOT))
    result = subprocess.call(['git', 'commit', '-m', f'scrape: {datetime.now(TZ).strftime("%Y-%m-%d %H:%M")}'], cwd=os.path.dirname(ROOT))
    if result == 0:
        subprocess.call(['git', 'push'], cwd=os.path.dirname(ROOT))
        print('=== Scraped data committed and pushed ===', flush=True)
    else:
        print('=== No changes to commit (already up to date) ===', flush=True)

    # Every "x ago" in the raw dumps is relative to THIS instant, not to build
    # time. Written in Pakistan time because that is what the page renders in.
    stamp = datetime.now(TZ).strftime('%Y-%m-%dT%H:%M:%S')
    with open(os.path.join(RAW, 'captured_at.txt'), 'w') as fh:
        fh.write(stamp)
    print(f'\ncaptured_at = {stamp}')

    return run('build_all.py', 'Build')


if __name__ == '__main__':
    sys.exit(main())
