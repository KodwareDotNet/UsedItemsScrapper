#!/usr/bin/env python3
"""Rebuild the kei-car tracker outputs from this run's raw dumps.
Inputs  : ../raw/pw_rows.txt   (id|title|pkr|ago|loc|spec|badge|rating|pics|cacheN|slug|path)
          ../raw/olx_rows.txt  (id|model|variant|price_lacs|year|km|searched|area|ago|region|picId)
State   : ../state/snapshot.csv  (previous run; created if missing)
Outputs : ~/Downloads/kei_cars_islamabad_rawalpindi.html
          ~/Downloads/660cc_kei_cars_isb_rwp.xlsx
          ../state/summary.md   (short text summary for chat)
          ../state/snapshot.csv (updated)
"""
import pandas as pd, numpy as np, re, json, os, sys, datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from models import PW_SLUG, VANS, is_van, tier

HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
RAW=os.path.join(ROOT,'raw'); ST=os.path.join(ROOT,'state'); OUT=os.path.dirname(ROOT)
NOW=dt.datetime.now()
# capture time = when the browser scrape actually ran (scraper writes raw/captured_at.txt);
# every "x ago" in the dumps is relative to THIS, not to build time.
def _cap():
    p=os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','raw','captured_at.txt')
    try:
        t=dt.datetime.fromisoformat(open(p).read().strip())
        return t.replace(tzinfo=None) if t.tzinfo else t
    except Exception:
        return dt.datetime.now()
CAP=_cap()
def rel2days(s):
    if not isinstance(s,str): return None
    m=re.match(r'^(\d+)(m|h|d|w|mo|y)$',s.strip().lower())
    if not m: return None
    return int(m.group(1))*{'m':1/1440,'h':1/24,'d':1,'w':7,'mo':30.4,'y':365}[m.group(2)]

def read_pipe(path,names):
    """Read one of the pipe-delimited raw dumps, tolerating malformed lines.

    pd.read_csv aborts the entire build on the first row with the wrong field
    count ("Expected 11 fields in line 129, saw 13"), which is a terrible
    trade: one seller who typed a '|' in their ad title took down a run that
    had several hundred perfectly good listings in it. The scrapers now scrub
    delimiters out of every field, so this is a safety net — a bad row is
    dropped and reported rather than fatal. Dropping is deliberate: folding the
    extra pieces back into some guessed column would silently misalign price,
    year and mileage, and a wrong price on a car is worse than a missing car.
    """
    rows,bad=[],[]
    with open(path,encoding='utf-8') as fh:
        for n,line in enumerate(fh,1):
            line=line.rstrip('\n').rstrip('\r')
            if not line.strip(): continue
            parts=line.split('|')
            if len(parts)==len(names):
                rows.append(parts)
            elif len(parts)<len(names):
                # short rows are safe to pad: the missing fields are trailing
                rows.append(parts+['']*(len(names)-len(parts)))
            else:
                bad.append((n,line))
    if bad:
        print(f'WARNING: skipped {len(bad)} malformed line(s) in {os.path.basename(path)} '
              f'(stray "|" in a field). First: line {bad[0][0]}: {bad[0][1][:160]}')
    return pd.DataFrame(rows,columns=names,dtype=str)

# Junk that PakWheels tags as 600-660cc but is not a kei car, plus the
# commercial vans Atif asked to drop. Model names themselves live in models.py.
NOT_WANTED=['mehran','bolan','ravi','carry','kix','mitsubishi-i-','cuore','charade']
MODEL_FIX=PW_SLUG

# ---------- PakWheels ----------
COLS=['id','title','pkr','ago','loc','spec','badge','rating','pics','cache','slug','path']
STATIC=['id','title','loc','spec','badge','rating','pics','cache','slug','path']
stat_path=os.path.join(ST,'pw_static.csv')
if os.path.exists(os.path.join(RAW,'pw_rows.txt')) and os.path.getsize(os.path.join(RAW,'pw_rows.txt'))>0:
    # full dump mode
    pw=read_pipe(os.path.join(RAW,'pw_rows.txt'),COLS)
    stat=pw[STATIC]
else:
    # delta mode: pw_core.txt = id|pkr|ago for every live ad, pw_new.txt = full rows for ads we have not seen
    core=read_pipe(os.path.join(RAW,'pw_core.txt'),['id','pkr','ago'])
    stat=pd.read_csv(stat_path,dtype=str) if os.path.exists(stat_path) else pd.DataFrame(columns=STATIC)
    np_=os.path.join(RAW,'pw_new.txt')
    if os.path.exists(np_) and os.path.getsize(np_):
        fresh=read_pipe(np_,COLS)
        stat=pd.concat([stat,fresh[STATIC]],ignore_index=True).drop_duplicates(subset=['id'],keep='last')
    pw=core.merge(stat,on='id',how='inner')
    missing=len(core)-len(pw)
    if missing: print(f'WARNING: {missing} live ads had no static record and were skipped')
stat.drop_duplicates(subset=['id'],keep='last').to_csv(stat_path,index=False)
pw['source']='PakWheels'
pw['url']='https://www.pakwheels.com'+pw['path']
pw['price_lacs']=pd.to_numeric(pw['pkr'],errors='coerce')/100000
sp=pw['spec'].fillna('')
pw['year']=pd.to_numeric(sp.str.extract(r'^(\d{4})')[0],errors='coerce')
pw['mileage_km']=pd.to_numeric(sp.str.extract(r'([\d,]+)\s*km')[0].str.replace(',',''),errors='coerce')
pw['transmission']=sp.str.extract(r'\b(Automatic|Manual)\b')[0].fillna('')
pw['city']=pw['loc'].fillna('').str.strip(); pw['area']=pw['city']
pw['badge']=pw['badge'].map({'M':'Managed by PakWheels','C':'PakWheels Certified','I':'PakWheels Inspected','A':'Auction Sheet Verified'}).fillna('')
pw['rating']=pd.to_numeric(pw['rating'],errors='coerce'); pw['pics']=pd.to_numeric(pw['pics'],errors='coerce')
pw['region']='core'
pw=pw[~pw['slug'].fillna('').apply(lambda s: any(b in s for b in NOT_WANTED))]
def from_slug(s,field):
    if not isinstance(s,str): return None
    base=re.sub(r'-\d{4}-\d+$','',s)
    for k in sorted(MODEL_FIX,key=len,reverse=True):
        if base.startswith(k):
            if field=='m': return MODEL_FIX[k]
            v=re.sub(r'-\d+$','',base[len(k):].strip('-'))
            return ' '.join(w.upper() if len(w)<=3 else w.capitalize() for w in v.split('-')) if v else ''
    return None
pw['model_full']=pw['slug'].apply(lambda s: from_slug(s,'m'))
pw['variant']=pw['slug'].apply(lambda s: from_slug(s,'v'))
miss=pw['model_full'].isna()
pw.loc[miss,'model_full']=pw.loc[miss,'title'].str.replace(r'\s*\d(\.\d)?/10','',regex=True).str.extract(r'^(\w+\s+[\w\- ]+?)\s+(?:19|20)\d{2}')[0]
pw['variant']=pw['variant'].fillna('').replace({'nan':'','None':''})
def pwimg(r):
    if isinstance(r['slug'],str) and r['slug']:
        pic=r['slug'].rsplit('-',1)[-1]
        return f"https://cache{r['cache'] or 1}.pakwheels.com/ad_pictures/{pic[:4]}/{r['slug']}.jpg"
    return ''
pw['img']=pw.apply(pwimg,axis=1)
# Engine size comes free on the search card ("2014 126,876 km Petrol 660 cc Automatic").
pw['engine_cc']=pd.to_numeric(sp.str.extract(r'(\d{3,4})\s*cc')[0],errors='coerce')

# Colour / registered-city / assembly are only on the ad's own page, so the
# scraper caches them in raw/pw_detail.csv (one fetch per ad, ever) and we merge
# by id here. Missing rows just mean that ad has not been enriched yet — the
# card renders without those lines rather than the build failing.
det_path=os.path.join(RAW,'pw_detail.csv')
DET=['color','reg_city','assembly','cc','body']
if os.path.exists(det_path) and os.path.getsize(det_path)>0:
    det=pd.read_csv(det_path,dtype=str).drop_duplicates(subset=['id'],keep='last')
    for c in DET:
        if c not in det.columns: det[c]=''
    pw=pw.merge(det[['id']+DET],on='id',how='left',suffixes=('','_det'))
    # Prefer the detail page's engine size where the card did not carry one.
    fallback=pd.to_numeric(pw['cc'].fillna('').str.extract(r'(\d{3,4})')[0],errors='coerce')
    pw['engine_cc']=pw['engine_cc'].fillna(fallback)
    print(f"PakWheels detail: {pw['color'].notna().sum()} of {len(pw)} ads have colour/registration")
else:
    for c in ['color','reg_city','assembly','body']: pw[c]=''
    print('PakWheels detail: raw/pw_detail.csv missing — colour and registration city '
          'will be blank until the scraper has run once')
pw['pw_body']=pw.get('body','')

# ---------- OLX ----------
# ---------- OLX ----------
olx_path = os.path.join(RAW,'olx_rows.txt')
if os.path.exists(olx_path) and os.path.getsize(olx_path) > 0:
    ol=read_pipe(olx_path,['id','model_full','variant','price_lacs','year','mileage_km',
                           'searched','area','ago','region','pic'])
    ol['source']='OLX'
    ol['url']='https://www.olx.com.pk/item/-iid-'+ol['id']
    for c in ['price_lacs','year','mileage_km']: ol[c]=pd.to_numeric(ol[c],errors='coerce')
    ol['city']=ol['area'].fillna('').str.split(',').str[-1].str.strip()
    ol['transmission']=''; ol['badge']=''; ol['rating']=np.nan; ol['pics']=np.nan
    ol['img']=ol['pic'].fillna('').apply(lambda p: f"https://images.olx.com.pk/thumbnails/{p}-600x450.jpeg" if p else '')
    # OLX listing cards carry neither colour nor registration city, and its
    # detail pages are far too rate-limited to fetch one-by-one, so these stay
    # blank rather than being guessed from the seller's free text.
    ol['color']=''; ol['reg_city']=''; ol['assembly']=''; ol['pw_body']=''
    ol['engine_cc']=np.nan
else:
    print("WARNING: olx_rows.txt not found or empty (--skip-olx mode?). Building with PakWheels data only.")
    ol=pd.DataFrame(columns=['id','model_full','variant','price_lacs','year','mileage_km','searched','area','ago','region','pic','source','url','city','transmission','badge','rating','pics','img','color','reg_city','assembly','pw_body','engine_cc'])

cols=['id','source','model_full','variant','year','price_lacs','mileage_km','transmission','city','area',
      'badge','rating','pics','ago','region','url','img','color','reg_city','assembly','pw_body','engine_cc']
df=pd.concat([pw[cols],ol[cols]],ignore_index=True)
df['model_full']=df['model_full'].fillna('Unknown').astype(str).str.strip()
for c in ['variant','transmission','city','area','badge','ago','region','url','img','source',
          'color','reg_city','assembly','pw_body']:
    df[c]=df[c].fillna('').astype(str).replace({'nan':'','None':'','<NA>':''})
# MIN_YEAR is a sanity floor against a mis-parsed year, NOT a shopping filter.
# There used to be a hard year>=2010 cut here; it silently hid every older car
# (2004-2009 Passos among them). Age is now filtered on the page instead, where
# it can be changed without a re-scrape.
MIN_YEAR=1980
df=df[(df['price_lacs']>0)&(df['price_lacs']<=30.01)
      &(df['year']>=MIN_YEAR)&(df['year']<=NOW.year)]
df=df.drop_duplicates(subset=['id'])
df['days']=df['ago'].apply(rel2days)

# Commercial vans are dropped outright (Hijet, Every, Carry, Scrum, Minicab,
# Clipper, Acty, Vamos, Town Box, Sambar, Dias, Atrai). The scrapers already
# filter them, but a keyword-fallback OLX row can still smuggle one in, so this
# is the last gate before the page.
vans_out=df['model_full'].apply(is_van)
if vans_out.any(): print(f'Dropped {int(vans_out.sum())} commercial van listing(s)')
df=df[~vans_out]

# PakWheels' own body type is used only as an extra van gate — it catches
# "Micro Van"/"Van" bodies on models the name-based list does not know about.
# It is deliberately NOT used as the displayed category: PakWheels has its own
# taxonomy (Station Wagon, MPV, Crossover) and OLX has none at all, so mixing
# them would make the Body filter mean two different things per row.
pwvan=df['pw_body'].str.contains(r'\bvan\b',case=False,na=False)
if pwvan.any(): print(f'Dropped {int(pwvan.sum())} listing(s) PakWheels classifies as a van')
df=df[~pwvan]

TALL=['N Box','N One','N Wgn','Spacia','Flair','Tanto','Roox','EK Space','Palette','Hustler','Wagon R','Move','Cast','Wake','Terios Kid','Pajero Mini','Zest','Thor','Roomy','Tank','Solio','Porte','Spade','Cube']
df['body']=df['model_full'].apply(lambda m: 'Tall-boy' if any(v.lower() in str(m).lower() for v in TALL) else 'Hatchback')

# Engine tier: which of Atif's two shopping lists a car belongs to. Derived from
# the model, not the cc reading, because OLX never states engine size — a Passo
# with no cc on the ad still needs to land in the 1000-1300cc bucket.
def _tier(r):
    t=tier(r['model_full'])
    if t: return '660cc kei' if t=='kei' else '1000-1300cc'
    cc=r['engine_cc']
    if pd.notna(cc): return '660cc kei' if cc<=680 else '1000-1300cc'
    # Keyword-fallback OLX rows ('Other JDM') match no model and state no cc.
    # Labelling them rather than leaving the field blank keeps them reachable
    # from the Engine dropdown instead of silently visible only under "All".
    return 'engine unstated'
df['tier']=df.apply(_tier,axis=1)
df['posted_date']=df['days'].apply(lambda d: (CAP-dt.timedelta(days=d)).date().isoformat() if pd.notna(d) else '')
df['dupkey']=(df['model_full'].str.lower()+'|'+df['year'].astype('Int64').astype(str)+'|'
              +df['price_lacs'].round(2).astype(str)+'|'+df['mileage_km'].fillna(-1).astype(int).astype(str))
df['dup']=df.duplicated(subset=['dupkey'],keep=False)

# ---------- diff against previous snapshot ----------
snap_path=os.path.join(ST,'snapshot.csv')
last_run_path=os.path.join(ST,'last_run.txt')
if os.path.exists(snap_path):
    old=pd.read_csv(snap_path,dtype={'id':str})
else:
    old=pd.DataFrame(columns=['id','price_lacs','first_seen','high_price_seen'])
# the stamp of the PREVIOUS run; anything first seen at or after it counts as new.
# Stored separately so re-running the build twice does not silently clear the NEW badges.
prev_run=open(last_run_path).read().strip() if os.path.exists(last_run_path) else ''
oldmap=old.set_index('id') if len(old) else old
def _first_seen(i):
    try: return str(oldmap.loc[i,'first_seen'])
    except Exception: return None
df['first_seen']=df['id'].apply(_first_seen)
STAMP=NOW.strftime('%Y-%m-%dT%H:%M')
df['first_seen']=df['first_seen'].fillna(STAMP)
df['is_new']=df['first_seen']>prev_run if prev_run else True
def _high(i):
    try: return float(oldmap.loc[i,'high_price_seen'])
    except Exception: return np.nan
df['prev_high']=df['id'].apply(_high)
df['price_drop']=np.where(df['prev_high'].notna()&(df['price_lacs']<df['prev_high']-0.01),
                          (df['prev_high']-df['price_lacs']).round(2),np.nan)
gone=set(old['id'])-set(df['id']) if len(old) else set()

def flags(r):
    # "Too cheap" and "too many km" are only meaningful relative to a car's age.
    # These were flat thresholds set back when the search itself was capped at
    # 2010+; with that cap gone they fire on perfectly ordinary old cars, and a
    # flagged row is hidden by default, so the effect was to re-hide exactly the
    # cars the year filter used to exclude.
    f=[]
    age=NOW.year-r['year'] if pd.notna(r['year']) else 0
    # A 1992 Alto at 3 lacs is just a 1992 Alto. A 2018 one at 3 lacs is an
    # instalment ad or a typo.
    if r['price_lacs'] < (8 if age<=20 else 2): f.append('price too low - verify')
    if r['year']>=NOW.year-4 and r['price_lacs']<22: f.append('too cheap for year - likely instalment ad')
    if pd.notna(r['mileage_km']):
        # Low mileage stays suspicious at any age — more so on an old car.
        if r['mileage_km']<5000: f.append('mileage implausibly low')
        # High mileage needs both a high total AND a high yearly rate, so a
        # 33-year-old car with 346,000 km (10.5k/yr) reads as normal while a
        # 6-year-old one with 320,000 km (53k/yr) does not.
        elif r['mileage_km']>300000 and r['mileage_km']/max(age,1)>20000:
            f.append('very high mileage')
    if r['dup']: f.append('duplicate listing')
    if r['region']=='near': f.append('outside city - Wah/Taxila/Attock/Jhelum area')
    return '; '.join(f)
df['flags']=df.apply(flags,axis=1)
df['price_pkr']=(df['price_lacs']*100000).round(0)
df=df.sort_values(['days','price_lacs'])

# ---------- update snapshot ----------
new_snap=df[['id','source','model_full','year','price_lacs','url','first_seen']].copy()
new_snap['last_seen']=STAMP
hi=dict(zip(old['id'],old['high_price_seen'])) if 'high_price_seen' in old.columns else {}
new_snap['high_price_seen']=[max(p,hi.get(i,p)) for i,p in zip(new_snap['id'],new_snap['price_lacs'])]
new_snap.to_csv(snap_path,index=False)
open(last_run_path,'w').write(STAMP)

# ---------- JSON for the page ----------
def _uid(u):
    """Stable per-listing id extracted from the URL.
    PakWheels: https://www.pakwheels.com/used-cars/suzuki-wagon-r-2015-for-sale-in-islamabad-11901671
               -> 'pw_11901671'   (the id is the TRAILING number; the one before
               "-for-sale-in-" is the model year, which is shared by hundreds of
               ads and made every car of the same year look like the same listing)
    OLX:       https://www.olx.com.pk/item/-iid-678901  ->  'olx_678901'      """
    u = (u or '').lower()
    if 'pakwheels.com' in u:
        m = re.search(r'-(\d+)$', u)
        return f'pw_{m.group(1)}' if m else ''
    if 'olx.com.pk' in u:
        m = re.search(r'-iid-(\d+)', u)
        return f'olx_{m.group(1)}' if m else ''
    return ''

recs=[]
for _,r in df.iterrows():
    recs.append({'id':_uid(r['url']),'s':r['source'][:2],'m':r['model_full'],'v':(r['variant'] or '')[:34],'y':int(r['year']),
      'p':round(float(r['price_lacs']),2),
      'k':None if pd.isna(r['mileage_km']) else int(r['mileage_km']),
      't':(r['transmission'] or '')[:1],'c':r['city'],'a':r['area'],'d':r['posted_date'],
      'g':r['ago'] if isinstance(r['ago'],str) else '','b':r['body'],'bd':r['badge'],
      'r':None if pd.isna(r['rating']) else float(r['rating']),
      'n':None if pd.isna(r['pics']) else int(r['pics']),
      'f':r['flags'],'u':r['url'],'i':r['img'],
      'cl':r['color'],'rg':r['reg_city'],'asm':r['assembly'],'tg':r['tier'],
      'cc':None if pd.isna(r['engine_cc']) else int(r['engine_cc']),
      'nw':bool(r['is_new']),'pd':None if pd.isna(r['price_drop']) else float(r['price_drop'])})

tpl=open(os.path.join(HERE,'page_template.html')).read()
html=(tpl.replace('DATA_HERE',json.dumps(recs,separators=(',',':')))
         .replace('COUNT_HERE',str(len(recs)))
         .replace('STAMP_HERE',CAP.strftime('%d %B %Y, %-I:%M %p'))
         .replace('CAPTURE_ISO',CAP.astimezone().isoformat(timespec='seconds'))
         .replace('NEW_HERE',str(int(df['is_new'].sum())))
         .replace('DROP_HERE',str(int(df['price_drop'].notna().sum()))))
open(os.path.join(OUT,'kei_cars_islamabad_rawalpindi.html'),'w').write(html)

df.to_csv(os.path.join(ST,'combined3.csv'),index=False)

# ---------- summary ----------
new=df[df['is_new']]
drops=df[df['price_drop'].notna()].sort_values('price_drop',ascending=False)
L=[f"**Kei tracker — {NOW.strftime('%d %b, %-I:%M %p')}**",
   f"{len(df)} live listings ({(df['source']=='PakWheels').sum()} PakWheels, {(df['source']=='OLX').sum()} OLX)",
   f"**{len(new)} new** since last run · **{len(drops)} price drops** · {len(gone)} ads disappeared"]
if len(new):
    L.append("")
    L.append("New:")
    for _,r in new.sort_values('price_lacs').head(8).iterrows():
        L.append(f"- {r['model_full']} {int(r['year'])} {r['variant']} — {r['price_lacs']:.2f} lacs, "
                 f"{'n/a' if pd.isna(r['mileage_km']) else format(int(r['mileage_km']),',')} km, {r['area']}")
if len(drops):
    L.append("")
    L.append("Price drops:")
    for _,r in drops.head(6).iterrows():
        L.append(f"- {r['model_full']} {int(r['year'])} — down {r['price_drop']:.2f} to {r['price_lacs']:.2f} lacs, {r['area']}")
open(os.path.join(ST,'summary.md'),'w').write('\n'.join(L))
print('\n'.join(L))
print(f"\nWROTE {os.path.join(OUT,'kei_cars_islamabad_rawalpindi.html')}")

# ---------- spreadsheet ----------
# sys.executable, not 'python3': the scripts run under scripts/.venv, and the
# system python3 has no pandas, so the hard-coded interpreter made every run
# report "xlsx build FAILED" while the spreadsheet built fine by hand.
import subprocess
r=subprocess.run([sys.executable,os.path.join(HERE,'build_xlsx.py')],
                 capture_output=True,text=True)
if r.returncode==0:
    print('xlsx rebuilt')
else:
    print(f'xlsx build FAILED (rc={r.returncode})')
    print((r.stderr or r.stdout).strip()[-800:])
