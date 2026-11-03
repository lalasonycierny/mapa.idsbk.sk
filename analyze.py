"""Z denných agregátov (data/daily) vyrobí štatistiky a dáta heatmáp pre web (docs/data)."""
import json, datetime as dt
from pathlib import Path
import pandas as pd, holidays

ROOT = Path(__file__).resolve().parent
DAILY, OUT = ROOT / "data" / "daily", ROOT / "docs" / "data"
GRID = 0.004
MAX_DAYS = 90          # koľko posledných jednotlivých dní sa generuje
MIN_LINE_N = 20        # min. počet pozorovaní, aby linka bola v rebríčku
MIN_CELL_N = 3         # min. počet pozorovaní v bunke heatmapy
SK = holidays.country_holidays("SK")
DAYTYPES = {"all": "Všetky dni", "workdays": "Pracovné dni", "weekend": "Víkendy a sviatky"}
KINDS = {"all": "Celé obdobie", "last7": "Posledných 7 dní", "last30": "Posledných 30 dní",
         "day": "Deň", "week": "Týždeň", "month": "Mesiac", "year": "Rok"}


def load(kind):
    parts = []
    for f in sorted(DAILY.glob(f"*_{kind}.csv.gz")):
        x = pd.read_csv(f)
        x["date"] = f.name.split("_")[0]
        parts.append(x)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def decorate(df):
    d = pd.to_datetime(df["date"])
    df["weekday"] = d.dt.weekday
    off = {s: (pd.Timestamp(s).weekday() >= 5 or pd.Timestamp(s).date() in SK) for s in df["date"].unique()}
    df["is_off"] = df["date"].map(off)
    iso = d.dt.isocalendar()
    df["week"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    df["month"] = d.dt.strftime("%Y-%m")
    df["year"] = d.dt.strftime("%Y")
    return df


def rec(n, s, d3, d5, mx=None):
    n = int(n)
    r = {"n": n, "avg": round(float(s) / n, 2) if n else 0,
         "p3": round(100 * float(d3) / n, 2) if n else 0,
         "p5": round(100 * float(d5) / n, 2) if n else 0}
    if mx is not None:
        r["max"] = int(mx)
    return r


SUM = ["n", "sum_delay", "d3", "d5"]


def summarize(L, G):
    if L.empty:
        return None
    t = L[SUM].sum()
    out = {"summary": rec(t.n, t.sum_delay, t.d3, t.d5, L["max_delay"].max()),
           "days": int(L["date"].nunique())}
    h = L.groupby("hour")[SUM].sum().reindex(range(24), fill_value=0)
    out["by_hour"] = [rec(r.n, r.sum_delay, r.d3, r.d5) for r in h.itertuples()]
    w = L.groupby("weekday")[SUM].sum().reindex(range(7), fill_value=0)
    out["by_weekday"] = [rec(r.n, r.sum_delay, r.d3, r.d5) for r in w.itertuples()]
    wh = L.groupby(["weekday", "hour"])[["n", "sum_delay"]].sum()
    mat = [[None] * 24 for _ in range(7)]
    for (a, b), r in wh.iterrows():
        if r.n > 0:
            mat[int(a)][int(b)] = round(r.sum_delay / r.n, 2)
    out["weekday_hour"] = mat
    ty = L.groupby("line_type").agg(n=("n", "sum"), s=("sum_delay", "sum"), d3=("d3", "sum"),
                                    d5=("d5", "sum"), mx=("max_delay", "max"))
    out["by_type"] = [{"type": k, **rec(r.n, r.s, r.d3, r.d5, r.mx)} for k, r in ty.iterrows()]
    li = L.groupby(["line", "line_type"]).agg(n=("n", "sum"), s=("sum_delay", "sum"), d3=("d3", "sum"),
                                              d5=("d5", "sum"), mx=("max_delay", "max")).reset_index()
    li = li[li["n"] >= MIN_LINE_N].sort_values("n", ascending=False)
    out["by_line"] = [{"line": r.line, "type": r.line_type, **rec(r.n, r.s, r.d3, r.d5, r.mx)}
                      for r in li.itertuples()]
    grid = []
    if not G.empty:
        g = G.groupby(["gy", "gx"])[["n", "sum_delay", "d3"]].sum().reset_index()
        g = g[g["n"] >= MIN_CELL_N]
        grid = [[round(r.gy * GRID, 5), round(r.gx * GRID, 5), int(r.n), int(r.sum_delay), int(r.d3)]
                for r in g.itertuples()]
    out["grid"] = grid
    return out


def main():
    L = load("lines")
    if L.empty:
        print("Žiadne dáta v data/daily"); return
    G = load("grid")
    L = decorate(L)
    if not G.empty:
        G = decorate(G)
    else:
        G = pd.DataFrame(columns=L.columns)

    dates = sorted(L["date"].unique())
    last = pd.Timestamp(dates[-1])
    periods = [("all", "all", "Celé obdobie", lambda df: pd.Series(True, index=df.index))]
    for k, n in (("last7", 7), ("last30", 30)):
        c = (last - pd.Timedelta(days=n - 1)).strftime("%Y-%m-%d")
        periods.append((k, k, KINDS[k], lambda df, c=c: df["date"] >= c))
    for d in dates[-MAX_DAYS:]:
        periods.append(("day", d, d, lambda df, v=d: df["date"] == v))
    for col in ("week", "month", "year"):
        for v in sorted(L[col].unique()):
            periods.append((col, v, v, lambda df, c=col, v=v: df[c] == v))

    def dtmask(df, dtp):
        if dtp == "workdays": return ~df["is_off"].astype(bool)
        if dtp == "weekend": return df["is_off"].astype(bool)
        return pd.Series(True, index=df.index)

    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.glob("*.json"):
        f.unlink()
    index = {"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             "daytypes": DAYTYPES, "kinds": {}}
    count = 0
    for kind, value, label, fn in periods:
        avail = []
        for dtp in (["all"] if kind in ("day",) else DAYTYPES):
            s = summarize(L[fn(L) & dtmask(L, dtp)],
                          G[fn(G) & dtmask(G, dtp)] if not G.empty else G)
            if s is None:
                continue
            s.update(kind=kind, value=value, daytype=dtp)
            (OUT / f"{kind}_{value}_{dtp}.json").write_text(
                json.dumps(s, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            avail.append(dtp); count += 1
        if avail:
            index["kinds"].setdefault(kind, {"label": KINDS[kind], "values": []})["values"].append(
                {"value": value, "label": label, "daytypes": avail})
    for k, v in index["kinds"].items():
        if k not in ("all", "last7", "last30"):
            v["values"].reverse()      # najnovšie obdobie ako prvé

    d = L.groupby("date")[SUM].sum()
    off = L.groupby("date")["is_off"].first()
    index_trend = [{"d": k, **rec(r.n, r.sum_delay, r.d3, r.d5), "off": bool(off[k])} for k, r in d.iterrows()]
    (OUT / "trend.json").write_text(json.dumps(index_trend, separators=(",", ":")), encoding="utf-8")
    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Hotovo: {count} súborov so štatistikami, {len(dates)} dní dát")


if __name__ == "__main__":
    main()
