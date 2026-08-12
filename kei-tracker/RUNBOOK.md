# Kei car tracker — refresh runbook

Rebuilds `~/Downloads/kei_cars_islamabad_rawalpindi.html` and `~/Downloads/660cc_kei_cars_isb_rwp.xlsx`
from live PakWheels + OLX data, marking what is new and what dropped in price since the last run.

Target: Japanese 660cc kei cars, Islamabad + Rawalpindi, PKR ≤ 3,000,000, model year 2010+, any mileage, any body type.

## Steps

1. **Connect Chrome.** `mcp__claude-in-chrome__list_connected_browsers`, then `select_browser`.
   If more than one browser is connected, pick the local macOS one.

2. **PakWheels.** Open a tab at
   `https://www.pakwheels.com/used-cars/search/-/ct_islamabad/ct_rawalpindi/pr_0_3000000/ec_600_660/yr_2010_2026/`
   Run `scripts/pakwheels_scrape.js` via `javascript_tool`, then:
   - `await window.__scrape(1,14)` — wait, then `await window.__scrape(15,30)`
   - Calls can exceed the 45s tool timeout. Fire with `.then(r=>window.__l=r)` and poll `window.__l`.
   - **Dump in delta form** (much cheaper than dumping all ~660 full rows every run):
     * `raw/pw_core.txt` — `id|pkr|ago` for **every** live ad. Prices and ad ages change constantly, so this must be complete.
     * `raw/pw_new.txt` — the full 12-field row, but only for ids **not already in** `state/pw_static.csv`
       (read that file first to get the known ids). Usually only 10-40 rows.
     * Delete any stale `raw/pw_rows.txt` before building, or it will be used instead.
     Title, specs, badge, inspection score, photo count and image slug never change for a given ad,
     so they are carried forward from `state/pw_static.csv`.
   - If anything looks off, you can fall back to dumping all 12 fields for every ad to `raw/pw_rows.txt`.

3. **OLX.** Open a tab at `https://www.olx.com.pk/cars_c84/q-alto`.
   Run `scripts/olx_scrape.js`, then `window.__go(window.__kw.slice(0,6),3)` and continue in batches of 6–10 keywords.
   **Critical:** OLX ignores the city path filter when a keyword has few local matches, so raw results include
   Karachi/Lahore/Sialkot cars. Keep only rows whose area ends in `, Islamabad` or `, Rawalpindi` (region `core`),
   or Wah / Taxila / Attock / Murree / Jhelum / Gujar Khan / Hasan Abdal / Dina / Sarai Alamgir / Kahuta / Mandra / Hazro
   (region `near`). Everything else is dropped. Expect roughly 250–300 rows to survive out of ~1,100.
   Dump in the same way to `raw/olx_rows.txt` as
   `id|model|variant|price_lacs|year|km|searchedCity|area|ago|region|picId`.

4. **Record the capture time.** The moment the scrapes finish, write the current local time to
   `raw/captured_at.txt` in ISO form, e.g. `2026-07-31T09:04:00`. Every "x ago" in the dumps is relative to
   this instant, not to build time — get it wrong and every posted-time on the page is wrong.

5. **Build.** `python3 scripts/build_all.py`
   It diffs against `state/snapshot.csv`, writes both output files, updates the snapshot,
   and prints a short summary (also saved to `state/summary.md`).

6. **Report.** Post the contents of `state/summary.md` to the user. Keep it to a few lines.
   If nothing is new and nothing dropped, say exactly that in one sentence — do not pad it.

## Notes
- Chrome may block automatic downloads. Do not rely on them; use the DOM-dump + `get_page_text` route above.
- `ago` values are compressed: `45m`, `3h`, `2d`, `1w`, `2mo`.
- Model names and variants come from the PakWheels image slug, which is more reliable than the ad title.
- Seven models PakWheels mislabels as 660cc (Mehran, Bolan, Ravi, Carry, Cuore, Kix, Jimny) are auto-excluded.
- Pakistani-assembled Suzuki Wagon R is 1000cc — on OLX keep Wagon R only if the ad says Stingray / FX / FZ / Hybrid.

## Deep OLX keyword set (run this in addition to `window.__kw`)

The model-name searches miss sellers who title their ad "660cc", "JDM import", or by trim only.
Running this second set at 3 pages each reliably finds 30-40 extra genuine Isb/Rwp cars:

```
['660cc','kei','jdm','japanese','imported','n-box','n-wgn','wagonr','mira es','mira cocoa',
 'move conte','every wagon','hijet cargo','clipper rio','pixis epoch','alto lapin','ek custom',
 'spacia custom','tanto custom','minicab bravo','wagon r stingray','dayz highway star']
```

Same location rule applies - discard anything whose area is not Islamabad/Rawalpindi or a listed nearby town.

## Delta dumps (cheaper than full dumps, use these)

- PakWheels: `raw/pw_core.txt` (`id|pkr|ago`, every live ad) + `raw/pw_new.txt` (full 12-field rows for ids
  missing from `state/pw_static.csv`). Leave `raw/pw_rows.txt` empty.
- OLX: `raw/olx_core.txt` (`id|price_lacs|ago`, every kept ad) + `raw/olx_new.txt` (full 11-field rows for ids
  missing from the previous `raw/olx_rows.txt`), then merge into `raw/olx_rows.txt` keeping the new price/ago.

## Page features to preserve

Header shows a live "captured X ago" counter and a red staleness banner past 4 hours.
Filters: search, model, site, body, city, price, year, max km, posted-within, sort, Hide-words blocklist,
New only, Price drops, Hide flagged, shortlist, Copy links. All filter state is mirrored into the URL hash,
so a bookmarked URL keeps the user's blocklist across rebuilds.
