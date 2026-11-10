# NET: predani na novy pocitac

Tento dokument je startovni bod pro pokracovani vyvoje NET na novem PC.

## Co uz je bezpecne v Gitu

- Backend: `https://github.com/Darkyyy351/NET-server.git`
- Frontend: `https://github.com/Darkyyy351/NET-frontend.git`
- Deployment a CM5 helpery: `https://github.com/Darkyyy351/NET-deploy.git`

Pro bezny vyvoj klonuj vetve `main`. Obsahuji release `0.2.0-dev.7`, ktery
pridal perzistentni historii telemetrie zarizeni.

```powershell
$netWorkspace = Join-Path $HOME 'Documents\NET'
New-Item -ItemType Directory -Force -Path $netWorkspace
Set-Location $netWorkspace

git clone https://github.com/Darkyyy351/NET-server.git
git clone https://github.com/Darkyyy351/NET-frontend.git
git clone https://github.com/Darkyyy351/NET-deploy.git
```

Potom v backendu a frontendu spust:

```powershell
npm install
```

## Co Git neumyslne neprenese

Tyto soubory nepatri do repozitare. Prenes je oddelene, idealne pres sifrovany
USB disk nebo spravce hesel. Nikdy je neposilej do verejneho repozitare.

- `NET-server\.env` - hlavne `API_TOKEN`, CORS a lokalni nastaveni backendu.
- `NET-frontend\.env` - lokalni API URL pro Vite.
- Volitelne `NET-server\data\` - lokalni zarizeni, logy, notifikace a historie
  telemetrie. CM5 ma vlastni produkcni data, takze toto kopiruj jen pokud chces
  zachovat uplne stejny lokalni testovaci stav.

Po kopii nastav prava tak, aby `.env` nevidel nikdo dalsi na pocitaci.

## ESP firmware

Slozka `NET-esp8266` v okamziku sepsani tohoto dokumentu nema GitHub remote.
Je nutne ji prenest jako celou slozku na novy pocitac. Obsahuje PlatformIO
projekt a `platformio.ini`.

Doporuceni: az bude novy pocitac funkcni, zaloz pro firmware samostatne soukrome
repozitar. Pak se bude stejne snadno obnovovat jako backend a frontend.

## Codex a kontext projektu

1. Na novem PC se prihlas do stejneho ChatGPT/Codex uctu.
2. Otevri tento chat, pokud je v historii dostupny.
3. Kdyz zacinas novy chat, vloz shrnuti: `NET 0.2`, CM5 s Dockerem, tri GitHub
   repozitare vyse a tento dokument.
4. Nikdy nevkladej API token do chatu ani do GitHub issue/commitu.

## Aktualni technicky stav

- CM5 obsluhuje Docker backend a frontend; aktualizace se ridi repozitarem
  `NET-deploy` a release manifestem `release.json`.
- Dashboard umi prijimat ESP heartbeat s RSSI, uptime a volnou pameti.
- Backend uklada vzorek telemetrie jednou za minutu, uchovava 24 hodin a zapisuje
  prechody offline/online.
- Monitoring zobrazuje historii RSSI a volne pameti pro 1, 6 nebo 24 hodin.
- CM5 host control, fan control a bezpecne podrzeni kritickych akci jsou soucasti
  aktualni vetve `main`.

## Kontrola po presunu

```powershell
git -C "$netWorkspace\NET-server" status --short --branch
git -C "$netWorkspace\NET-frontend" status --short --branch
git -C "$netWorkspace\NET-deploy" status --short --branch
```

Vsechny tri prikazy maji ukazat `main` bez lokalnich zmen. Pak lze spustit
lokalni backend a frontend podle jejich README. Na CM5 neni potreba nic kopirovat
z noveho PC, dokud neni pripravena dalsi GitHub release aktualizace.
