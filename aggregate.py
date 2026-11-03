"""Denné agregáty z lokálnej SQLite databázy -> data/daily/*.csv.gz

Agregáty sú aditívne (počty a súčty), takže z nich analyze.py vie poskladať
ľubovoľné obdobie (deň, týždeň, mesiac, rok, pracovné dni, víkendy).

Použitie:
  python aggregate.py              # včera + dnes
  python aggregate.py 2026-11-03   # konkrétny deň
  python aggregate.py --all        # všetky dni v databáze
"""
import sqlite3, sys, datetime as dt
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent
DB = ROOT / "idsbk.db"
OUT = ROOT / "data" / "daily"
TZ = "Europe/Bratislava"
GRID = 0.004          # veľkosť bunky heatmapy v stupňoch (~450 m x 300 m)


def _utc(day):
    return pd.Timestamp(day).tz_localize(TZ).tz_convert("UTC").isoformat(timespec="seconds")


def aggregate_day(con, day):
    df = pd.read_sql(
        "SELECT ts,line,line_type,delay_min,lat,lon FROM positions WHERE ts>=? AND ts<?",
        con, params=(_utc(day), _utc(day + dt.timedelta(days=1))))
    if df.empty:
        return False
    df["hour"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(TZ).dt.hour
    df["line"] = df["line"].fillna("?").astype(str)
    df["line_type"] = df["line_type"].fillna("?").astype(str)
    df["delay_min"] = pd.to_numeric(df["delay_min"], errors="coerce").fillna(0).clip(lower=0)
    df["d3"] = (df["delay_min"] >= 3).astype(int)
    df["d5"] = (df["delay_min"] >= 5).astype(int)
    OUT.mkdir(parents=True, exist_ok=True)

    (df.groupby(["hour", "line", "line_type"])
       .agg(n=("delay_min", "size"), sum_delay=("delay_min", "sum"), d3=("d3", "sum"),
            d5=("d5", "sum"), max_delay=("delay_min", "max"))
       .reset_index().to_csv(OUT / f"{day}_lines.csv.gz", index=False))

    g = df.dropna(subset=["lat", "lon"]).copy()
    g["gy"] = (g["lat"] / GRID).round().astype(int)
    g["gx"] = (g["lon"] / GRID).round().astype(int)
    (g.groupby(["hour", "gy", "gx"])
      .agg(n=("delay_min", "size"), sum_delay=("delay_min", "sum"), d3=("d3", "sum"))
      .reset_index().to_csv(OUT / f"{day}_grid.csv.gz", index=False))
    return True


def aggregate_days(days):
    con = sqlite3.connect(DB)
    try:
        for d in days:
            ok = aggregate_day(con, d)
            print(f"agregát {d}:", "OK" if ok else "žiadne dáta")
    finally:
        con.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    today = pd.Timestamp.now(TZ).date()
    if args == ["--all"]:
        con = sqlite3.connect(DB)
        lo, hi = con.execute("SELECT MIN(ts), MAX(ts) FROM positions").fetchone()
        con.close()
        a = pd.Timestamp(lo).tz_convert(TZ).date()
        b = pd.Timestamp(hi).tz_convert(TZ).date()
        days = [a + dt.timedelta(days=i) for i in range((b - a).days + 1)]
    elif args:
        days = [dt.date.fromisoformat(x) for x in args]
    else:
        days = [today - dt.timedelta(days=1), today]
    aggregate_days(days)
