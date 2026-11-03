# mapa.idsbk.sk – štatistiky meškaní IDS BK

Zberač polôh vozidiel z <https://mapa.idsbk.sk/> + automatické štatistiky a heatmapy
(deň / týždeň / mesiac / rok / posledných 7 a 30 dní, každé aj pre **pracovné dni** a **víkendy a sviatky**).

```
collector.py   zbiera polohy (každých ~90 s) do lokálnej SQLite, každú hodinu pushne agregáty
aggregate.py   SQLite -> data/daily/*.csv.gz (malé aditívne denné agregáty)
analyze.py     data/daily -> docs/data/*.json (štatistiky, heatmapy, trend)
docs/          webová stránka (Leaflet heatmapa + grafy)
.github/       workflow: pri každom pushi prepočíta štatistiky a nasadí web na GitHub Pages
```

## Čo sa ukladá kam
- **Surové dáta** (`idsbk.db`, ~30–40 MB denne) ostávajú len lokálne, nepushujú sa (limity GitHubu).
- **Do repozitára** ide len agregát po dňoch (`data/daily/`, rádovo desiatky kB denne, pár MB za rok).
- Štatistiky aj heatmapy sa generujú na GitHube z agregátov, takže dlhodobé obdobia sa vždy skladajú z celej histórie.

## Nastavenie (jednorazovo)
1. Skopírujte tieto súbory do klonu repozitára a pushnite:
   ```
   git clone https://github.com/lalasonycierny/mapa.idsbk.sk
   # skopírovať súbory sem
   git add . && git commit -m "init" && git push
   ```
2. Na GitHube: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. Ak už máte nazbieranú `idsbk.db`, skopírujte ju do priečinka repozitára a spustite `python aggregate.py --all`, potom `git add data && git commit -m "history" && git push`.
4. Na počítači, kde zberač beží, musí fungovať `git push` bez zadávania hesla (Git Credential Manager alebo SSH kľúč).
5. Spustite zber:
   ```
   pip install -r requirements.txt
   python collector.py
   ```
Web bude na `https://lalasonycierny.github.io/mapa.idsbk.sk/`.

## Poznámky
- Zberač musí bežať stále (PC, Raspberry Pi, VPS). GitHub Actions cron na minútový zber nestačí (minimum 5 min, často s oneskorením).
- Meškanie je v celých minútach, záporné hodnoty sa berú ako 0. „Pozorovanie“ = jedno vozidlo v jednom zábere.
- Pracovný deň = nie sobota/nedeľa/slovenský štátny sviatok.
- Nezvyšujte frekvenciu dotazov (`INTERVAL`, `PAUSE`), nejde o oficiálne API.
