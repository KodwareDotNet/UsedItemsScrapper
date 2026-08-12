# Kei tracker — automatic 3-hourly publish

Scrapes PakWheels and OLX for 660cc Japanese kei cars in Islamabad / Rawalpindi
under PKR 30 lacs, rebuilds the HTML gallery and the spreadsheet, and pushes the
page to SmarterASP.NET by FTP. Runs on GitHub Actions, so **nothing needs to be
running on your laptop.**

## What changed vs. the laptop version

The scraping used to happen inside Chrome, driven by the Claude extension. That
is the one part that needed your Mac on. It has been ported to two plain Python
scripts that fetch the same URLs directly:

| Old (laptop)                  | New (GitHub Actions)          |
| ----------------------------- | ----------------------------- |
| `pakwheels_scrape.js` in Chrome | `scripts/scrape_pakwheels.py` |
| `olx_scrape.js` in Chrome       | `scripts/scrape_olx.py`       |
| manual DOM dump + paste         | written straight to `raw/`    |
| you open the file locally       | `scripts/deploy_ftp.py` → your site |

`build_all.py`, `build_xlsx.py` and `page_template.html` are **unchanged** — the
same builder, the same page, including the mobile layout.

## One-time setup

### 1. Push this to a GitHub repo

```bash
git init
git add .
git commit -m "kei tracker"
git branch -M main
git remote add origin https://github.com/<you>/kei-tracker.git
git push -u origin main
```

A **private** repo is fine and is what I would use — Actions minutes are free
for public repos and generous for private ones, and this job uses roughly
3–5 minutes per run.

### 2. Add your FTP credentials as repository secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**.
Get the values from the SmarterASP.NET control panel under **FTP Manager**.

| Secret     | Value                                    |
| ---------- | ---------------------------------------- |
| `FTP_HOST` | e.g. `ftp.yoursite.com`                  |
| `FTP_USER` | your FTP username                        |
| `FTP_PASS` | your FTP password                        |
| `FTP_DIR`  | `/wwwroot` (leave unset to use this default) |

Secrets are write-only — nobody, including you, can read them back afterwards,
and they never appear in the repo or in the logs.

### 3. Run it once by hand

Actions tab → **Refresh kei tracker** → **Run workflow**. Watch the log. The
first run scrapes both sites and takes the longest, roughly 5–8 minutes.

Then load your domain. `index.html` will be sitting in `wwwroot`.

After that it runs itself every 3 hours.

## How the schedule works

`.github/workflows/refresh.yml` fires at `0 0,3,6,9,12,15,18,21 * * *` **UTC**,
which is 05:00, 08:00, 11:00, 14:00, 17:00, 20:00, 23:00 and 02:00 Pakistan
time. To change it, edit that cron line — remember GitHub cron is always UTC.

Two things worth knowing about GitHub's scheduler:

- Scheduled runs are **best-effort**, not to-the-minute. At busy times they can
  start 5–20 minutes late. For a used-car tracker that is irrelevant, but don't
  be surprised by it.
- On a **public** repo, GitHub disables scheduled workflows after 60 days of no
  repository activity. This workflow commits its own state on every run, which
  counts as activity, so it will not go to sleep. Private repos are not subject
  to this at all.

## Where the state lives

`kei-tracker/state/` is committed back to the repo after every run:

- `snapshot.csv` — what was live last time, plus `first_seen` and
  `high_price_seen` per ad. This is what makes the **NEW** badges and the
  **price drop** badges work.
- `pw_static.csv` — title, specs, photo count and image slug per PakWheels ad.
  These never change, so they are fetched once and carried forward. It is why
  each run only needs to pull prices and ages for ~700 ads instead of everything.
- `last_run.txt` — the stamp new ads are compared against.

Because it is all in git, you get a full history and can roll back a bad run
with `git revert`.

## Guard rails

The job deliberately fails loudly rather than publishing garbage:

- If PakWheels returns fewer than 200 ads (blocked, or the markup changed), the
  scraper aborts and the build never runs, so **the page already on your site
  stays untouched.**
- Same for OLX below 80 surviving rows — but there it keeps going with the
  previous OLX rows, since PakWheels alone is still worth publishing.
- The FTP upload writes to `index.html.uploading` and renames it into place, so
  nobody can load a half-written page.
- Every run also attaches the built HTML and xlsx as a downloadable artifact,
  kept 7 days — handy for checking what a run actually produced.

## OLX is not scraped every time

OLX moves slowly and the keyword sweep is ~350 requests, so it is only
re-scraped when its data is more than 20 hours old (`OLX_MAX_AGE_HOURS`).
PakWheels, which is where the churn is, runs every single time.

## Running it locally

```bash
pip install -r requirements.txt
python kei-tracker/scripts/refresh.py            # scrape + build, no upload
python kei-tracker/scripts/refresh.py --force-olx
python kei-tracker/scripts/deploy_ftp.py         # needs the FTP_* env vars
```

Outputs land one level above `kei-tracker/` — i.e. in the repo root — as
`kei_cars_islamabad_rawalpindi.html` and `660cc_kei_cars_isb_rwp.xlsx`.

## If it breaks

**Everything empty / "ABORT: only N ads scraped".** The sites are refusing
datacenter traffic, or their markup moved. Check a failing run's log — the
scraper prints per-page counts. Both sites served GitHub-style requests fine
when this was built, but that can change; the fallback is running the same
scripts from a cheap VPS on a residential-ish IP, or adding a proxy.

**Page updates but shows nothing new.** Check the `TZ: Asia/Karachi` line in the
workflow is still there. `build_all.py` compares timestamps in local time, and a
UTC runner clock silently marks every genuinely-new ad as old.

**FTP step fails.** Try `FTP_TLS: "0"` in the workflow env if SmarterASP.NET
rejects FTPS on your plan. Confirm `FTP_DIR` — files must land in `wwwroot`,
not the folder above it that also holds `log` and `temp`.
