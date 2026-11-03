# Zjednotene dáta a prvé korelácie

## Pokrytie dát

| Zdroj | od | do | hodín s dátami |
|---|---|---|---|
| vlaky_meskania | 2026-10-23 08:00 | 2026-11-03 12:00 | 115 |
| vlaky_obsadenost | 2026-10-20 13:00 | 2026-11-01 14:00 | 243 |
| doprava_BA | 2026-10-25 17:00 | 2026-11-03 12:00 | 206 |
| mhd_BA | 2026-10-25 05:00 | 2026-10-27 20:00 | 51 |

Hodín, kde sú naraz vlaky (meškania), obsadenosť aj doprava: **45**.

## Čistenie dát

- meskania: riadkov v subore: 18256
- meskania: agregovane riadky (vyhodene): 3840
- meskania: riadky, kde sa povodne Meskanie_Minuty lisilo od textu: 1961
- meskania: riadky vyhodene pre zmenu casu (DST): 80
- obsadenost: riadkov v subore: 14577
- obsadenost: riadky vyhodene pre zmenu casu (DST): 77
- doprava: riadkov flow.csv: 1971
- doprava: riadkov incidents.csv: 6871
- mhd: riadkov v daily_hour.csv: 51
- mhd: riadky vyhodene pre zmenu casu (DST): 0

## Najsilnejšie vzťahy medzi zdrojmi

Zobrazené sú dvojice s aspoň 12 spoločnými hodinami. `rho` = Spearmanova korelácia; stĺpec „bez denného rytmu“ odstraňuje to, že všetko sa mení podľa hodiny dňa (špička vs. noc).

| A | B | n | rho | q | rho bez denného rytmu | q |
|---|---|---|---|---|---|---|
| vlaky: priemer meskania (strop 180) [min] | MHD: % spojov nacas | 45 | -0.77 | 0.000 | 0.09 | 0.948 |
| doprava: sucet zdrzania z incidentov [s] | MHD: priemerne meskanie [min] | 37 | -0.65 | 0.000 | 0.00 | 0.996 |
| vlaky: priemer meskania (strop 180) [min] | MHD: % spojov s meskanim >=5 min | 45 | 0.64 | 0.000 | -0.13 | 0.882 |
| doprava: sucet zdrzania z incidentov [s] | MHD: % spojov s meskanim >=5 min | 37 | -0.64 | 0.000 | 0.05 | 0.991 |
| vlaky: priemer meskania (strop 180) [min] | MHD: priemerne meskanie [min] | 45 | 0.64 | 0.000 | -0.13 | 0.882 |
| vlaky: median meskania [min] | MHD: % spojov nacas | 45 | -0.55 | 0.000 | -0.20 | 0.689 |
| doprava: pocet kolon | MHD: priemerne meskanie [min] | 37 | -0.54 | 0.002 | -0.02 | 0.991 |
| doprava: sucet zdrzania z kolon [s] | MHD: priemerne meskanie [min] | 37 | -0.54 | 0.002 | 0.00 | 0.996 |
| doprava: sucet zdrzania z kolon [s] | MHD: % spojov s meskanim >=5 min | 37 | -0.53 | 0.003 | 0.05 | 0.991 |
| doprava: pocet kolon | MHD: % spojov s meskanim >=5 min | 37 | -0.53 | 0.003 | 0.03 | 0.991 |
| vozne: priemerna obsadenost [%] | doprava: podiel pomalych bodov [%] | 141 | 0.48 | 0.000 | 0.32 | 0.003 |
| vozne: podiel vozov >=80 % obsadenych [%] | doprava: podiel pomalych bodov [%] | 141 | 0.48 | 0.000 | 0.32 | 0.003 |
| vozne: priemerna obsadenost [%] | doprava: rychlost / volna rychlost | 141 | -0.48 | 0.000 | -0.28 | 0.015 |
| vozne: priemerna obsadenost [%] | doprava: cas prejazdu / volny cas | 141 | 0.48 | 0.000 | 0.23 | 0.064 |
| vozne: podiel vozov >=80 % obsadenych [%] | doprava: rychlost / volna rychlost | 141 | -0.45 | 0.000 | -0.26 | 0.023 |

## Oneskorené vzťahy (lag)

Kladný lag = hodnota X spred *lag* hodín sa porovnáva s Y teraz. Zobrazený je lag s najväčším |rho| (bez denného rytmu).

| X predchádza | Y nasleduje | najlepší lag [h] | n | rho | rho (surové) |
|---|---|---|---|---|---|
| doprava: cas prejazdu / volny cas | vlaky: median meskania [min] | 3 | 53 | 0.28 | 0.44 |
| doprava: podiel pomalych bodov [%] | vlaky: podiel s meskanim >=15 min [%] | -3 | 57 | -0.25 | -0.15 |
| doprava: sucet zdrzania z kolon [s] | vlaky: median meskania [min] | -3 | 57 | -0.26 | -0.42 |
| vozne: priemerna obsadenost [%] | vlaky: median meskania [min] | 0 | 99 | 0.12 | -0.08 |
| doprava: cas prejazdu / volny cas | MHD: priemerne meskanie [min] | -3 | 40 | 0.15 | -0.36 |
| MHD: priemerne meskanie [min] | vlaky: median meskania [min] | -3 | 47 | 0.16 | 0.41 |

## Na čo si dať pozor

- Dáta sa prekrývajú len niekoľko dní, takže ide o prieskumné zistenia, nie o dôkaz.
- Korelácia nie je príčinnosť; spoločný vplyv majú hodina dňa, deň v týždni, počasie, sviatky.
- Pri veľa porovnaniach vzniknú niektoré silné vzťahy náhodou, preto sú uvedené q-hodnoty (Benjamini-Hochberg).
- Meškania vlakov sú len vzorka vlakov sledovaných zberačom, nie všetky vlaky; hodinové mediány sú ovplyvnené tým, ktoré vlaky sa práve sledovali (napr. medzinárodné s veľkým meškaním).
- Obsadenosť je odvodená: kapacita vozňa = najväčší počet voľných miest, aký sa kedy videl; ide o stav rezervácií v čase zberu pre budúci odchod, nie o skutočnú obsadenosť počas jazdy.
- Čas v `databaza_meskani.csv`, `obsadenost_data.csv` a `daily_hour.csv` sa berie ako lokálny (Europe/Bratislava); `flow.csv` a `incidents.csv` sú v UTC. Ak je to inak, zmeň `--tz`.
- Hodiny okolo zmeny času (25. 10. 2026) sa nejednoznačne prevádzajú a vyraďujú sa.
