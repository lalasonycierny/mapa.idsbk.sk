#!/usr/bin/env python3
"""
analyza.py - zjednotenie dat z repozitarov lalasonycierny a prvotna analyza vztahov.

Zdroje (len to, co je v repozitaroch):
  idos-meskanie   data/databaza_meskani.csv   meskania vlakov (IDOS)
  idos-grafy      data/obsadenost_data.csv    volne miesta vo vozonoch (ZSSK)
  ba-livetraffic  data/flow.csv, incidents.csv  doprava v Bratislave (TomTom)
  mhd-stats       stats/data/daily_hour.csv   MHD agregaty po hodinach

Co skript robi:
  1. stiahne (git clone/pull) repozitare,
  2. vycisti dáta (vid nizsie "Opravy dat"),
  3. zjednoti vsetko do jednej hodinovej tabulky (casy v UTC, hodina/den v lokalnom case),
  4. spocita krizove korelacie (Spearman) - surove aj po odstraneni dennej periodicity,
  5. spocita oneskorene korelacie (lag) pre vybrane dvojice,
  6. ulozi CSV, grafy a report.md.

Pouzitie:
  python analyza.py                      # stiahne repozitare do ./repos, vystup do ./vystup
  python analyza.py --no-fetch           # pouzije uz stiahnute repozitare
  python analyza.py --repos-dir C:\\git  # ak uz repozitare mas inde (priecinky s ich nazvami)
  python analyza.py --tz Europe/Bratislava --min-n 12

Opravy dat:
  - z databaza_meskani.csv sa vyhodia agregovane riadky ("mesk\u00e1 5-14 min\u00fat; ...; 4.32 %"),
    ktore su omylom ulozene medzi vlakmi,
  - meskanie sa prepocita z textu, pretoze stlpec Meskanie_Minuty pri texte
    "+ 81 min\u00fat= 1 hod, 21 min." pripocita hodiny druhy raz (141 namiesto 81).

Poziadavky: pip install pandas numpy scipy matplotlib
"""
import argparse
import subprocess
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

GH_USER = "lalasonycierny"
REPOS = ["idos-meskanie", "idos-grafy", "ba-livetraffic", "mhd-stats"]

# metriky podla zdroja (podla nich sa urcuju "krizove" dvojice)
SOURCES = {
    "vlaky_meskania": ["delay_median", "delay_mean_cap180", "delay_share_ge15"],
    "vlaky_obsadenost": ["occ_mean", "occ_share_ge80"],
    "doprava_BA": ["traffic_tt_ratio", "traffic_speed_ratio", "traffic_slow_pct",
                   "inc_delay_s", "jam_delay_s", "n_jams"],
    "mhd_BA": ["mhd_delay_min", "mhd_pct_on_time", "mhd_pct_5plus"],
}
COL2SRC = {c: s for s, cols in SOURCES.items() for c in cols}

# dvojice (co predchadza -> co nasleduje) pre oneskorene korelacie
LAG_PAIRS = [
    ("traffic_tt_ratio", "delay_median"),
    ("traffic_slow_pct", "delay_share_ge15"),
    ("jam_delay_s", "delay_median"),
    ("occ_mean", "delay_median"),
    ("traffic_tt_ratio", "mhd_delay_min"),
    ("mhd_delay_min", "delay_median"),
]
LAGS = range(-3, 4)

LABELS = {
    "delay_median": "vlaky: median meskania [min]",
    "delay_mean_cap180": "vlaky: priemer meskania (strop 180) [min]",
    "delay_share_ge15": "vlaky: podiel s meskanim >=15 min [%]",
    "occ_mean": "vozne: priemerna obsadenost [%]",
    "occ_share_ge80": "vozne: podiel vozov >=80 % obsadenych [%]",
    "traffic_tt_ratio": "doprava: cas prejazdu / volny cas",
    "traffic_speed_ratio": "doprava: rychlost / volna rychlost",
    "traffic_slow_pct": "doprava: podiel pomalych bodov [%]",
    "inc_delay_s": "doprava: sucet zdrzania z incidentov [s]",
    "jam_delay_s": "doprava: sucet zdrzania z kolon [s]",
    "n_jams": "doprava: pocet kolon",
    "mhd_delay_min": "MHD: priemerne meskanie [min]",
    "mhd_pct_on_time": "MHD: % spojov nacas",
    "mhd_pct_5plus": "MHD: % spojov s meskanim >=5 min",
}


# --------------------------------------------------------------------------- git
def fetch_repos(base: Path, update: bool) -> None:
    base.mkdir(parents=True, exist_ok=True)
    for name in REPOS:
        path = base / name
        url = f"https://github.com/{GH_USER}/{name}.git"
        if path.exists():
            if update:
                print(f"[git] pull {name}")
                subprocess.run(["git", "-C", str(path), "pull", "--ff-only"], check=False)
        else:
            print(f"[git] clone {name}")
            subprocess.run(["git", "clone", "--depth", "1", url, str(path)], check=True)


# --------------------------------------------------------------------------- casy
def local_naive_to_utc(s: pd.Series, tz: str, log: dict, key: str) -> pd.Series:
    """Naivny lokalny cas -> UTC. Nejednoznacne (zmena casu) a neexistujuce hodiny sa vyhodia."""
    loc = s.dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
    log[f"{key}: riadky vyhodene pre zmenu casu (DST)"] = int(loc.isna().sum() - s.isna().sum())
    return loc.dt.tz_convert("UTC")


# --------------------------------------------------------------------------- nacitanie
def parse_delay_minutes(text: pd.Series) -> pd.Series:
    """Meskanie v minutach z textu ('+ 81 minut= 1 hod, 21 min.', 'v cieli meskal 8 min.', ...)."""
    s = text.fillna("")
    plus = s.str.extract(r"\+\s*(\d+)\s*min")[0]
    other = s.str.extract(r"(\d+)\s*min")[0]
    out = pd.to_numeric(plus.fillna(other), errors="coerce")
    out[s.str.contains("načas", regex=False)] = 0
    return out


def hourly_delays(base: Path, tz: str, log: dict) -> pd.DataFrame:
    df = pd.read_csv(base / "idos-meskanie" / "data" / "databaza_meskani.csv", sep=";",
                     encoding="utf-8-sig")
    log["meskania: riadkov v subore"] = len(df)
    is_agg = df["Vlak"].str.startswith("mešká", na=False) & df["Meskanie_Text"].str.endswith("%", na=False)
    log["meskania: agregovane riadky (vyhodene)"] = int(is_agg.sum())
    df = df[~is_agg].copy()

    df["delay_min"] = parse_delay_minutes(df["Meskanie_Text"])
    log["meskania: riadky, kde sa povodne Meskanie_Minuty lisilo od textu"] = int(
        (df["delay_min"] != pd.to_numeric(df["Meskanie_Minuty"], errors="coerce")).sum())
    df = df.dropna(subset=["delay_min"])

    ts = pd.to_datetime(df["Timestamp"], errors="coerce")
    df["hour_utc"] = local_naive_to_utc(ts, tz, log, "meskania").dt.floor("h")
    df = df.dropna(subset=["hour_utc"])

    g = df.groupby("hour_utc")["delay_min"]
    out = pd.DataFrame({
        "delay_n": g.size(),
        "delay_median": g.median(),
        "delay_mean_cap180": g.apply(lambda x: x.clip(upper=180).mean()),
        "delay_share_ge15": g.apply(lambda x: (x >= 15).mean() * 100),
    })
    return out[out["delay_n"] >= 5]


def hourly_occupancy(base: Path, tz: str, log: dict) -> pd.DataFrame:
    df = pd.read_csv(base / "idos-grafy" / "data" / "obsadenost_data.csv", encoding="utf-8-sig")
    log["obsadenost: riadkov v subore"] = len(df)
    # kapacita vozna = najvyssi pocet volnych miest, aky sme kedy videli (rovnako ako v generate_charts.py)
    cap = df.groupby(["vlak", "vozen"])["volne"].transform("max")
    df = df[cap >= 10].copy()
    cap = cap[df.index]
    df["occ_pct"] = ((cap - df["volne"]) / cap * 100).clip(0, 100)

    ts = pd.to_datetime(df["cas"], errors="coerce")
    df["hour_utc"] = local_naive_to_utc(ts, tz, log, "obsadenost").dt.floor("h")
    df = df.dropna(subset=["hour_utc"])

    g = df.groupby("hour_utc")["occ_pct"]
    out = pd.DataFrame({
        "occ_n": g.size(),
        "occ_mean": g.mean(),
        "occ_share_ge80": g.apply(lambda x: (x >= 80).mean() * 100),
    })
    return out[out["occ_n"] >= 5]


def hourly_traffic(base: Path, log: dict) -> pd.DataFrame:
    d = base / "ba-livetraffic" / "data"
    f = pd.read_csv(d / "flow.csv")
    log["doprava: riadkov flow.csv"] = len(f)
    f = f[f["confidence"] >= 0.5].copy()
    f["ts"] = pd.to_datetime(f["ts"], utc=True)
    f["tt_ratio"] = (f["cur_tt"] / f["free_tt"].replace(0, np.nan)).clip(0.5, 5)
    f["speed_ratio"] = (f["cur"] / f["free"].replace(0, np.nan)).clip(0, 1.5)
    f["slow"] = (f["tt_ratio"] >= 1.25).astype(float) * 100
    snap = f.groupby("ts").agg(traffic_tt_ratio=("tt_ratio", "mean"),
                               traffic_speed_ratio=("speed_ratio", "mean"),
                               traffic_slow_pct=("slow", "mean"))

    inc = pd.read_csv(d / "incidents.csv")
    log["doprava: riadkov incidents.csv"] = len(inc)
    inc["ts"] = pd.to_datetime(inc["ts"], utc=True)
    inc["delay_s"] = pd.to_numeric(inc["delay_s"], errors="coerce").fillna(0)
    inc["is_jam"] = (inc["cat"] == 6)  # TomTom kategoria 6 = Jam (kolona)
    inc_snap = inc.groupby("ts").agg(
        inc_delay_s=("delay_s", "sum"),
        n_jams=("is_jam", "sum"),
    )
    jam_delay = inc[inc["is_jam"]].groupby("ts")["delay_s"].sum().rename("jam_delay_s")
    inc_snap = inc_snap.join(jam_delay)
    # snimky bez incidentov v subore nie su -> doplnit nulami podla snimkov z flow.csv
    snap = snap.join(inc_snap).fillna({"inc_delay_s": 0, "n_jams": 0, "jam_delay_s": 0})
    snap.index = snap.index.floor("h")
    return snap.groupby(level=0).mean()


def hourly_mhd(base: Path, tz: str, log: dict) -> pd.DataFrame:
    df = pd.read_csv(base / "mhd-stats" / "stats" / "data" / "daily_hour.csv")
    log["mhd: riadkov v daily_hour.csv"] = len(df)
    ts = pd.to_datetime(df["date"] + " " + df["hour"].astype(str).str.zfill(2) + ":00")
    df["hour_utc"] = local_naive_to_utc(ts, tz, log, "mhd")
    df = df.dropna(subset=["hour_utc"]).set_index("hour_utc")
    return df.rename(columns={"avg_delay_min": "mhd_delay_min",
                              "pct_on_time": "mhd_pct_on_time",
                              "pct_5plus": "mhd_pct_5plus"})[
        ["mhd_delay_min", "mhd_pct_on_time", "mhd_pct_5plus"]]


# --------------------------------------------------------------------------- zjednotenie
def build_unified(base: Path, tz: str, log: dict) -> pd.DataFrame:
    parts = [hourly_delays(base, tz, log), hourly_occupancy(base, tz, log),
             hourly_traffic(base, log), hourly_mhd(base, tz, log)]
    uni = pd.concat(parts, axis=1, sort=True)
    full = pd.date_range(uni.index.min(), uni.index.max(), freq="h", tz="UTC")
    uni = uni.reindex(full)
    uni.index.name = "hour_utc"
    loc = uni.index.tz_convert(tz)
    uni.insert(0, "hour_local", loc.strftime("%Y-%m-%d %H:%M"))
    uni.insert(1, "hod", loc.hour)
    uni.insert(2, "weekday", loc.dayofweek)
    return uni


def detrend_by_hour(uni: pd.DataFrame, cols) -> pd.DataFrame:
    """Odstrani priemernu dennu periodicitu: hodnota - priemer pre danu hodinu dna."""
    out = pd.DataFrame(index=uni.index)
    for c in cols:
        out[c] = uni[c] - uni.groupby("hod")[c].transform("mean")
    return out


# --------------------------------------------------------------------------- korelacie
def spearman(a: pd.Series, b: pd.Series, min_n: int):
    sub = pd.concat([a, b], axis=1).dropna()
    if len(sub) < min_n or sub.iloc[:, 0].nunique() < 3 or sub.iloc[:, 1].nunique() < 3:
        return len(sub), np.nan, np.nan
    rho, p = stats.spearmanr(sub.iloc[:, 0], sub.iloc[:, 1])
    return len(sub), float(rho), float(p)


def bh_qvalues(p: pd.Series) -> pd.Series:
    """Benjamini-Hochberg: upravene p-hodnoty pri viacnasobnom testovani."""
    s = p.dropna().sort_values()
    m = len(s)
    q = (s * m / np.arange(1, m + 1))[::-1].cummin()[::-1].clip(upper=1)
    return q.reindex(p.index)


def cross_correlations(uni: pd.DataFrame, min_n: int) -> pd.DataFrame:
    cols = [c for c in COL2SRC if c in uni.columns and uni[c].notna().any()]
    resid = detrend_by_hour(uni, cols)
    rows = []
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            if COL2SRC[a] == COL2SRC[b]:
                continue
            n, rho, p = spearman(uni[a], uni[b], min_n)
            n2, rho2, p2 = spearman(resid[a], resid[b], min_n)
            rows.append({"a": a, "b": b, "zdroj_a": COL2SRC[a], "zdroj_b": COL2SRC[b],
                         "n_hodin": n, "rho_surove": rho, "p_surove": p,
                         "n_hodin_bez_dennej_periodicity": n2,
                         "rho_bez_dennej_periodicity": rho2, "p_bez_dennej_periodicity": p2})
    res = pd.DataFrame(rows)
    if res.empty:
        return res
    res["q_surove"] = bh_qvalues(res["p_surove"])
    res["q_bez_dennej_periodicity"] = bh_qvalues(res["p_bez_dennej_periodicity"])
    return res.sort_values("rho_surove", key=lambda x: x.abs(), ascending=False)


def lag_correlations(uni: pd.DataFrame, min_n: int) -> pd.DataFrame:
    cols = sorted({c for pair in LAG_PAIRS for c in pair})
    resid = detrend_by_hour(uni, cols)
    rows = []
    for x, y in LAG_PAIRS:
        for lag in LAGS:
            # lag > 0: hodnota x spred 'lag' hodin sa porovna s y teraz (x predchadza y)
            n, rho, p = spearman(uni[x].shift(lag), uni[y], min_n)
            n2, rho2, p2 = spearman(resid[x].shift(lag), resid[y], min_n)
            rows.append({"x_predchadza": x, "y_nasleduje": y, "lag_hodin": lag,
                         "n": n, "rho_surove": rho, "p_surove": p,
                         "n_bez_periodicity": n2, "rho_bez_periodicity": rho2,
                         "p_bez_periodicity": p2})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- grafy
def plot_heatmap(res: pd.DataFrame, col: str, qcol: str, title: str, path: Path) -> None:
    if res.empty or res[col].notna().sum() == 0:
        return
    mat = res.pivot(index="a", columns="b", values=col)
    qmat = res.pivot(index="a", columns="b", values=qcol)
    mat = mat.dropna(how="all").dropna(axis=1, how="all")
    qmat = qmat.reindex(index=mat.index, columns=mat.columns)
    fig, ax = plt.subplots(figsize=(1.0 + 0.9 * mat.shape[1], 1.0 + 0.5 * mat.shape[0]))
    im = ax.imshow(mat.values, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(mat.shape[1]), [LABELS.get(c, c) for c in mat.columns], rotation=60, ha="right", fontsize=7)
    ax.set_yticks(range(mat.shape[0]), [LABELS.get(c, c) for c in mat.index], fontsize=7)
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            v, q = mat.values[i, j], qmat.values[i, j]
            if not np.isnan(v):
                ax.text(j, i, f"{v:.2f}{'*' if q < 0.05 else ''}", ha="center", va="center", fontsize=7)
    ax.set_title(title + "\n(* = q < 0,05 po oprave na viacnasobne testovanie)", fontsize=9)
    fig.colorbar(im, ax=ax, shrink=0.8, label="Spearman rho")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def plot_timeseries(uni: pd.DataFrame, path: Path) -> None:
    cols = ["delay_median", "occ_mean", "traffic_tt_ratio", "mhd_delay_min"]
    cols = [c for c in cols if c in uni.columns and uni[c].notna().any()]
    if not cols:
        return
    fig, axes = plt.subplots(len(cols), 1, figsize=(11, 2.0 * len(cols)), sharex=True)
    axes = np.atleast_1d(axes)
    x = uni.index.tz_convert(None)
    for ax, c in zip(axes, cols):
        ax.plot(x, uni[c].values, ".-", lw=0.8, ms=3)
        ax.set_ylabel(LABELS.get(c, c), fontsize=7)
        ax.grid(alpha=0.3)
    fig.suptitle("Hodinove rady (cas v UTC)", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


# --------------------------------------------------------------------------- report
def fmt(x, nd=2):
    return "-" if pd.isna(x) else f"{x:.{nd}f}"


def write_report(uni, res, lags, log, tz, min_n, path: Path) -> None:
    L = ["# Zjednotene dáta a prvé korelácie", ""]
    L += ["## Pokrytie dát", "", "| Zdroj | od | do | hodín s dátami |", "|---|---|---|---|"]
    for src, cols in SOURCES.items():
        c = [k for k in cols if k in uni.columns]
        if not c:
            continue
        valid = uni[c].notna().any(axis=1)
        if valid.any():
            idx = uni.index[valid].tz_convert(tz)
            L.append(f"| {src} | {idx.min():%Y-%m-%d %H:%M} | {idx.max():%Y-%m-%d %H:%M} | {int(valid.sum())} |")
    core = [c for c in ["delay_median", "occ_mean", "traffic_tt_ratio"] if c in uni.columns]
    if core:
        L += ["", f"Hodín, kde sú naraz vlaky (meškania), obsadenosť aj doprava: **{int(uni[core].notna().all(axis=1).sum())}**."]
    L += ["", "## Čistenie dát", ""] + [f"- {k}: {v}" for k, v in log.items()]

    L += ["", "## Najsilnejšie vzťahy medzi zdrojmi", "",
          f"Zobrazené sú dvojice s aspoň {min_n} spoločnými hodinami. "
          "`rho` = Spearmanova korelácia; stĺpec „bez denného rytmu“ odstraňuje to, že všetko "
          "sa mení podľa hodiny dňa (špička vs. noc).", ""]
    if res.empty:
        L.append("_Žiadna dvojica nemá dosť spoločných hodín._")
    else:
        L += ["| A | B | n | rho | q | rho bez denného rytmu | q |", "|---|---|---|---|---|---|---|"]
        for _, r in res.dropna(subset=["rho_surove"]).head(15).iterrows():
            L.append(f"| {LABELS.get(r.a, r.a)} | {LABELS.get(r.b, r.b)} | {int(r.n_hodin)} | "
                     f"{fmt(r.rho_surove)} | {fmt(r.q_surove, 3)} | {fmt(r.rho_bez_dennej_periodicity)} | "
                     f"{fmt(r.q_bez_dennej_periodicity, 3)} |")

    L += ["", "## Oneskorené vzťahy (lag)", "",
          "Kladný lag = hodnota X spred *lag* hodín sa porovnáva s Y teraz. Zobrazený je lag s najväčším |rho| "
          "(bez denného rytmu).", "",
          "| X predchádza | Y nasleduje | najlepší lag [h] | n | rho | rho (surové) |", "|---|---|---|---|---|---|"]
    for (x, y), g in lags.groupby(["x_predchadza", "y_nasleduje"], sort=False):
        g = g.dropna(subset=["rho_bez_periodicity"])
        if g.empty:
            L.append(f"| {LABELS.get(x, x)} | {LABELS.get(y, y)} | málo dát | | | |")
            continue
        b = g.loc[g["rho_bez_periodicity"].abs().idxmax()]
        L.append(f"| {LABELS.get(x, x)} | {LABELS.get(y, y)} | {int(b.lag_hodin)} | {int(b.n_bez_periodicity)} | "
                 f"{fmt(b.rho_bez_periodicity)} | {fmt(b.rho_surove)} |")

    L += ["", "## Na čo si dať pozor", "",
          "- Dáta sa prekrývajú len niekoľko dní, takže ide o prieskumné zistenia, nie o dôkaz.",
          "- Korelácia nie je príčinnosť; spoločný vplyv majú hodina dňa, deň v týždni, počasie, sviatky.",
          "- Pri veľa porovnaniach vzniknú niektoré silné vzťahy náhodou, preto sú uvedené q-hodnoty (Benjamini-Hochberg).",
          "- Meškania vlakov sú len vzorka vlakov sledovaných zberačom, nie všetky vlaky; hodinové mediány "
          "sú ovplyvnené tým, ktoré vlaky sa práve sledovali (napr. medzinárodné s veľkým meškaním).",
          "- Obsadenosť je odvodená: kapacita vozňa = najväčší počet voľných miest, aký sa kedy videl; ide o "
          "stav rezervácií v čase zberu pre budúci odchod, nie o skutočnú obsadenosť počas jazdy.",
          "- Čas v `databaza_meskani.csv`, `obsadenost_data.csv` a `daily_hour.csv` sa berie ako lokálny "
          f"({tz}); `flow.csv` a `incidents.csv` sú v UTC. Ak je to inak, zmeň `--tz`.",
          f"- Hodiny okolo zmeny času (25. 10. 2026) sa nejednoznačne prevádzajú a vyraďujú sa."]
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repos-dir", default="repos", help="kde su (alebo budu) repozitare")
    ap.add_argument("--out", default="vystup", help="vystupny priecinok")
    ap.add_argument("--tz", default="Europe/Bratislava", help="casova zona lokalnych casov v datach")
    ap.add_argument("--min-n", type=int, default=12, help="minimum spolocnych hodin pre korelaciu")
    ap.add_argument("--no-fetch", action="store_true", help="nestahovat / neaktualizovat repozitare")
    args = ap.parse_args()

    base, out = Path(args.repos_dir), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if not args.no_fetch:
        fetch_repos(base, update=True)

    log: dict = {}
    uni = build_unified(base, args.tz, log)
    uni.to_csv(out / "zjednotene_hodinove.csv", encoding="utf-8-sig")

    res = cross_correlations(uni, args.min_n)
    res.to_csv(out / "korelacie.csv", index=False, encoding="utf-8-sig")
    lags = lag_correlations(uni, args.min_n)
    lags.to_csv(out / "korelacie_lag.csv", index=False, encoding="utf-8-sig")

    plot_heatmap(res, "rho_surove", "q_surove", "Korelacie medzi zdrojmi (surove)", out / "korelacie_surove.png")
    plot_heatmap(res, "rho_bez_dennej_periodicity", "q_bez_dennej_periodicity",
                 "Korelacie po odstraneni dennej periodicity", out / "korelacie_bez_periodicity.png")
    plot_timeseries(uni, out / "casove_rady.png")
    write_report(uni, res, lags, log, args.tz, args.min_n, out / "report.md")

    print(f"\nHotovo. Vystup je v: {out.resolve()}")
    print((out / "report.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
