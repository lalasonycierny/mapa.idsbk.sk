"""Zberač polôh vozidiel IDS BK -> lokálna SQLite + hodinový push agregátov na GitHub."""
import requests, sqlite3, time, json, os, math, subprocess, datetime as dt
from pathlib import Path
import pandas as pd
from aggregate import aggregate_days, TZ

ROOT = Path(__file__).resolve().parent
URL = "https://mapa.idsbk.sk/navigation/vehicles/nearby"
H = {"User-Agent": "Mozilla/5.0", "Referer": "https://mapa.idsbk.sk/mhd-ids-bk"}
CAP = 100              # strop servera na počet vozidiel v odpovedi
MIN_SIDE_KM = 0.5
INTERVAL = 90          # sekundy medzi cyklami
PAUSE = 0.25           # pauza medzi dotazmi
PUSH_EVERY = 3600      # ako často agregovať a pushovať (sekundy); 0 = nepushovať
BBOX = (47.80, 16.70, 48.55, 17.65)
START_SIDE_KM = 20
DB, CELLS_FILE = ROOT / "idsbk.db", ROOT / "cells.json"

KM_LAT = 111.0
def km_lon(lat): return 111.32 * math.cos(math.radians(lat))

def initial_cells():
    lat0, lon0, lat1, lon1 = BBOX
    dlat = START_SIDE_KM / KM_LAT
    dlon = START_SIDE_KM / km_lon((lat0 + lat1) / 2)
    cells, la = [], lat0
    while la < lat1:
        lo = lon0
        while lo < lon1:
            cells.append([la, lo, la + dlat, lo + dlon])
            lo += dlon
        la += dlat
    return cells

def cell_query(c):
    lat, lon = (c[0] + c[2]) / 2, (c[1] + c[3]) / 2
    h = (c[2] - c[0]) * KM_LAT / 2
    w = (c[3] - c[1]) * km_lon(lat) / 2
    radius = math.hypot(h, w) + 0.05
    r = requests.get(URL, params={"lat": lat, "lng": lon, "radius": round(radius, 3), "cityID": -1},
                     headers=H, timeout=30)
    r.raise_for_status()
    return r.json().get("vehicles", []), (c[2] - c[0]) * KM_LAT

def split(c):
    mla, mlo = (c[0] + c[2]) / 2, (c[1] + c[3]) / 2
    return [[c[0], c[1], mla, mlo], [c[0], mlo, mla, c[3]],
            [mla, c[1], c[2], mlo], [mla, mlo, c[2], c[3]]]

def collect(cells):
    seen, new_cells, work = {}, [], list(cells)
    while work:
        c = work.pop()
        vs, side = cell_query(c)
        time.sleep(PAUSE)
        if len(vs) >= CAP and side / 2 >= MIN_SIDE_KM:
            work.extend(split(c))
            continue
        new_cells.append(c)
        for v in vs:
            seen[v["vehicleID"]] = v
    return seen.values(), new_cells

def git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)

def publish():
    today = pd.Timestamp.now(TZ).date()
    aggregate_days([today - dt.timedelta(days=1), today])
    git("add", "data")
    if git("diff", "--cached", "--quiet").returncode == 0:
        print("git: nič nové"); return
    git("commit", "-m", f"data {dt.datetime.now():%Y-%m-%d %H:%M}")
    git("pull", "--rebase", "--autostash")
    r = git("push")
    print("git push:", "OK" if r.returncode == 0 else r.stderr.strip()[:300])

db = sqlite3.connect(DB)
db.execute("""CREATE TABLE IF NOT EXISTS positions (
    ts TEXT, vehicle_id INTEGER, trip_id INTEGER, line TEXT, line_type TEXT,
    destination TEXT, operator TEXT, delay_min INTEGER,
    lat REAL, lon REAL, last_stop_order INTEGER, on_stop INTEGER)""")
db.execute("CREATE INDEX IF NOT EXISTS ix_ts ON positions(ts)")
db.execute("CREATE INDEX IF NOT EXISTS ix_line ON positions(line)")

cells = json.load(open(CELLS_FILE)) if CELLS_FILE.exists() else initial_cells()
last_push = 0

while True:
    try:
        t0 = time.time()
        ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        vehicles, cells = collect(cells)
        json.dump(cells, open(CELLS_FILE, "w"))
        rows = []
        for v in vehicles:
            t = v.get("timeTableTrip") or {}
            l = t.get("timeTableLine") or {}
            rows.append((ts, v["vehicleID"], t.get("tripID"), l.get("line"), l.get("ezLineType"),
                         t.get("destination"), t.get("operatorName"), v.get("delayMinutes"),
                         v.get("latitude"), v.get("longitude"), v.get("lastStopOrder"),
                         int(bool(v.get("isOnStop")))))
        db.executemany("INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.commit()
        print(ts, len(rows), "vozidiel,", len(cells), "buniek,", round(time.time() - t0), "s")
        if PUSH_EVERY and time.time() - last_push >= PUSH_EVERY:
            publish()
            last_push = time.time()
    except Exception as e:
        print("chyba:", e)
    time.sleep(INTERVAL)
