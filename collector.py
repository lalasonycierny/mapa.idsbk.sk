"""Zberač polôh vozidiel IDS BK -> lokálna SQLite + hodinový push agregátov na GitHub.

Režimy:
  python collector.py          # beží v slučke (každých INTERVAL s)
  python collector.py --once   # jeden cyklus a koniec (pre Plánovač úloh, napr. každé 2 min)
Log: collector.log vedľa skriptu.
"""
import requests, sqlite3, time, json, os, math, subprocess, sys, logging, datetime as dt
from logging.handlers import RotatingFileHandler
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
LOCK, LAST_PUSH = ROOT / "collector.lock", ROOT / ".last_push"
ONCE = "--once" in sys.argv

log = logging.getLogger("collector")
log.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S")
_fh = RotatingFileHandler(ROOT / "collector.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
_fh.setFormatter(_fmt)
log.addHandler(_fh)
if sys.stdout:                      # pythonw.exe nemá konzolu
    _sh = logging.StreamHandler()
    _sh.setFormatter(_fmt)
    log.addHandler(_sh)

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
    flags = 0x08000000 if os.name == "nt" else 0      # CREATE_NO_WINDOW
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, creationflags=flags)

def publish():
    """Agregácia + commit + pull --rebase + push. Vráti True len pri úspešnom pushi."""
    today = pd.Timestamp.now(TZ).date()
    aggregate_days([today - dt.timedelta(days=1), today])
    git("add", "data")
    if git("diff", "--cached", "--quiet").returncode != 0:
        c = git("commit", "-m", f"data {dt.datetime.now():%Y-%m-%d %H:%M}")
        if c.returncode != 0:
            log.info("git commit zlyhal: %s", (c.stderr or c.stdout).strip()[:300])
            return False
    p = git("pull", "--rebase", "--autostash")
    if p.returncode != 0:
        log.info("git pull zlyhal: %s", (p.stderr or p.stdout).strip()[:300])
        return False
    r = git("push")                      # odošle aj commity, ktoré ostali po skoršom neúspechu
    if r.returncode != 0:
        log.info("git push zlyhal: %s", r.stderr.strip()[:300])
        return False
    log.info("git push: OK")
    return True

def connect():
    db = sqlite3.connect(DB)
    db.execute("""CREATE TABLE IF NOT EXISTS positions (
        ts TEXT, vehicle_id INTEGER, trip_id INTEGER, line TEXT, line_type TEXT,
        destination TEXT, operator TEXT, delay_min INTEGER,
        lat REAL, lon REAL, last_stop_order INTEGER, on_stop INTEGER)""")
    db.execute("CREATE INDEX IF NOT EXISTS ix_ts ON positions(ts)")
    db.execute("CREATE INDEX IF NOT EXISTS ix_line ON positions(line)")
    return db

def push_due():
    if not PUSH_EVERY:
        return False
    try:
        return time.time() - float(LAST_PUSH.read_text()) >= PUSH_EVERY
    except Exception:
        return True

def cycle(db):
    t0 = time.time()
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    cells = json.load(open(CELLS_FILE)) if CELLS_FILE.exists() else initial_cells()
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
    log.info("%d vozidiel, %d buniek, %d s", len(rows), len(cells), round(time.time() - t0))
    if push_due():
        ok = publish()
        # pri chybe skúsiť znova o ~10 minút, nie až o hodinu
        LAST_PUSH.write_text(str(time.time() if ok else time.time() - PUSH_EVERY + 600))

def acquire_lock():
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 600:
        return False                    # iná inštancia práve beží
    LOCK.write_text(str(os.getpid()))
    return True

def main():
    if not acquire_lock():
        log.info("beží iná inštancia, končím")
        return
    try:
        db = connect()
        while True:
            try:
                LOCK.touch()
                cycle(db)
            except Exception as e:
                log.info("chyba: %s", e)
            if ONCE:
                break
            time.sleep(INTERVAL)
    finally:
        LOCK.unlink(missing_ok=True)

if __name__ == "__main__":
    main()