# Viola Stats – guida completa (senza bisogno di AI)

Questo repository (`ulisse035-blip/viola-stats-data`, **pubblico**) contiene i dati dell'app Android **Viola Stats** e gli script che li aggiornano da soli con **GitHub Actions** (gratis per i repository pubblici, nessun segreto o token da configurare).

## 1. Come funziona

```
FotMob (dati Opta) ──┐                     ┌── raw.githubusercontent.com ──► app Viola Stats
                     ├─► scripts/viola.py ─┤   (l'app scarica i JSON all'apertura e col tasto ⟳,
ESPN (controllo e    ┘    (GitHub Actions)  └   li salva in memoria e funziona anche offline)
riserva)
```

* Fonte principale: **FotMob** (pagine pubbliche + statistiche stagionali dei giocatori).
* **ESPN** serve per controllare risultato, marcatori e statistiche principali di ogni partita, ed è la **riserva** se FotMob non risponde o blocca i server di GitHub (403/429). Con ESPN si salvano solo i dati base (niente voti, heatmap e xG): lo script lo segnala nel log e alla prima occasione rimpiazza i dati con quelli completi di FotMob.
* L'app **non** aggiorna in tempo reale: i dati cambiano **all'intervallo**, **a fine partita** e **il giorno dopo** (heatmap e posizioni medie, che FotMob pubblica in ritardo).

## 2. Struttura dei file

| File | Contenuto |
|---|---|
| `seasons.json` | stagione corrente e le 2 stagioni tenute (es. `["2026-27","2025-26"]`). L'app legge da qui l'elenco delle stagioni: al cambio stagione si adatta **senza reinstallare**. |
| `calendario.json` | prossime partite della Fiorentina (data UTC, competizione, avversario, id FotMob/ESPN). |
| `s/<stagione>/stagione.json` | classifica Serie A, marcatori/assist/G+A, rosa per ruolo (solo giocatori ancora in prima squadra: FotMob incrociato con il sito ufficiale acffiorentina.com; i partiti sono in `usciti`), formazione tipo, foto squadra, verifiche. |
| `s/<stagione>/squadra.json` | statistiche di squadra della stagione (somma e media delle statistiche di ogni partita, per competizione) + PPDA/deep da Understat quando disponibile. |
| `s/<stagione>/partite.json` | elenco partite della stagione (risultato, stato `HT`/`FT`, livello dei dati). |
| `s/<stagione>/p/AAAAMMGG.json` | una partita: eventi, statistiche squadra (totale / 1º / 2º tempo), tiri, momentum, formazioni, statistiche individuali **di entrambe le squadre**, heatmap e posizione media. Ogni tiro è una lista con le chiavi in `tiriK`; dalla v1.4 in coda ci sono anche `gy`/`gz` (punto in cui il tiro attraversa la linea di porta: y del campo 0–68, porta tra 30,34 e 37,66; altezza in metri), `bx`/`by` (punto del muro/parata), `linea` (salvato sulla linea) e `area` (tiro da dentro l'area). |
| `g/<id FotMob>.json` | un giocatore della Fiorentina: info, carriera, statistiche stagionali per squadra e competizione (con per 90' e percentili), heatmap stagionale, ultime partite. |
| `scripts/viola.py` | lo script di aggiornamento (solo Python 3 standard, nessuna libreria da installare). |
| `scripts/pubblica.sh` | fa commit e push delle modifiche (solo se ci sono). |
| `.github/workflows/*.yml` | le automazioni. |
| `manifest.json` | (non versionato) riepilogo dell'ultima esecuzione. |

## 3. Le modalità dello script

```
python3 scripts/viola.py <modo> [--forza] [--stagione 2026-27] [--root cartella]
```

| Modo | Cosa fa |
|---|---|
| `auto` | usato ogni 15 minuti: se non c'è una partita della Fiorentina in corso (da 10' prima a 200' dopo il calcio d'inizio) esce subito. All'intervallo lancia `intervallo`, a fine gara `fine`. |
| `intervallo` | salva le statistiche del **1º tempo** (squadre e giocatori). L'app mostra il badge **"Primo tempo"**. |
| `fine` | salva le statistiche **complete** a fine partita, aggiorna classifica, marcatori, rosa e calendario. |
| `heatmap` | aggiunge heatmap e posizioni medie appena FotMob le pubblica, e ricalcola le statistiche stagionali dei giocatori (heatmap stagionale, posizione media stagionale, per 90', percentili). |
| `season-rollover` | dal 1º luglio: passa alla nuova stagione, tiene solo la nuova e la precedente e **cancella la più vecchia** (dal repository e, al successivo aggiornamento, dall'app). |
| `backfill` | riscarica tutte le partite delle 2 stagioni (usare con `--forza` per sovrascrivere). |
| `giocatori` | ricalcola solo i file dei giocatori. |

Regola di sicurezza: un dato non viene mai sostituito da uno "peggiore" (es. una partita finale con heatmap non viene sovrascritta da dati solo-ESPN o di metà gara).

## 4. Le automazioni (GitHub Actions)

| Workflow | Quando | Cosa |
|---|---|---|
| `partita.yml` | ogni 15 minuti | `viola.py auto` (intervallo / fine) |
| `giornaliero.yml` | 06:20 e 18:20 ora italiana d'inverno (05:20 e 17:20 UTC) | controllo cambio stagione + `heatmap` |
| `stagione.yml` | 1º luglio, 06:00 ora italiana | `season-rollover` |

Ogni workflow si può lanciare a mano: tab **Actions** → scegli il workflow → **Run workflow** (si può scegliere il modo, es. `fine`, `backfill`, e `forza`).

Limiti da conoscere:
* GitHub può ritardare le esecuzioni programmate di 5–20 minuti: se l'intervallo "salta", lo script salva comunque le statistiche del 1º tempo appena vede la partita oltre il 60' (dati del periodo "FirstHalf"), poi a fine gara quelle complete.
* GitHub **disattiva le automazioni dopo 60 giorni senza commit**. Durante la stagione non succede (i dati cambiano ogni settimana); d'estate, se succede, basta andare su Actions e premere **Enable workflow**.
* raw.githubusercontent.com tiene i file in cache ~5 minuti: il tasto ⟳ dell'app aggira la cache.

### Prima configurazione (una volta sola)
1. Crea il repository **pubblico** `viola-stats-data` sull'account `ulisse035-blip` e carica questi file (la cartella `.github` compresa).
2. **Settings → Actions → General → Workflow permissions**: seleziona **Read and write permissions** → Save.
3. Tab **Actions**: se richiesto, premi "I understand my workflows, enable them".
4. Lancia a mano `Giornaliero` con modo `heatmap` per verificare: nel log deve comparire `FILE MODIFICATI` oppure `nessuna partita da aggiornare`. Se compare `FotMob blocca le richieste`, i dati arrivano da ESPN (base).

### Esecuzione sul tuo computer (alternativa)
```
git clone https://github.com/ulisse035-blip/viola-stats-data
cd viola-stats-data
python3 scripts/viola.py fine        # oppure heatmap, backfill...
git add -A && git commit -m "aggiornamento" && git push
```
Serve solo Python 3.9 o superiore.

## 5. Ricostruire l'APK senza AI

La cartella del progetto app (`app-fiorentina/`) contiene:
* `www/` – l'app vera e propria (HTML/CSS/JavaScript): `index.html`, `app.js` (classifica, marcatori, rosa, formazione), `partite.js` (partite, giocatori), `data.js` (download da GitHub + cache), `img/` (loghi e foto), `seed/` (copia dei dati inclusa nell'APK per il primo avvio offline).
* `android/` – il guscio Android (WebView): `AndroidManifest.xml`, `src/.../MainActivity.java`, `res/` (icone), `build.sh`.
* `keystore/fiorentina.jks` – **la chiave di firma: conservala!** Senza la stessa chiave Android non permette di aggiornare l'app sopra la versione installata (alias `fiorentina`; la password sta solo in `android/build.sh` in locale, non in questo archivio).

Passi (Linux o macOS; su Windows usa WSL):
1. Installa JDK 17 e gli Android SDK command-line tools; con `sdkmanager` installa `build-tools;35.0.0` e `platforms;android-35`.
2. In `android/build.sh` controlla i percorsi in alto (`SDK=$HOME/android`, `JDK=...`) e adattali al tuo computer.
3. Per una nuova versione aumenta `--version-code` (numero intero, es. 4) e `--version-name` (es. 1.3) in `build.sh` **e** in `AndroidManifest.xml`.
4. (Facoltativo) aggiorna la copia offline dei dati: `python3 build_seed.py` (copia i JSON di questo repository in `www/seed/`).
5. Esegui `bash android/build.sh`: crea `Fiorentina-v1.2.apk` firmato (aapt2 → javac → d8 → zipalign → apksigner).
6. Copia l'APK sul telefono e installalo (consenti "installa app da origini sconosciute").

Cosa modificare senza toccare l'APK: tutto ciò che sta in questo repository (dati, script, orari) viene letto dall'app via internet, quindi **non serve ricompilare** l'app per nuovi dati o per il cambio stagione.

## 6. Cosa NON è disponibile (fonti gratuite)
Passaggi in avanti/laterali/indietro, mappa dei passaggi e mappa delle azioni difensive richiedono le coordinate di ogni evento (dati "event-level" Opta/StatsBomb). Nessuna fonte gratuita le fornisce per la Fiorentina 2025-26/2026-27: FotMob pubblica solo tiri e heatmap, WhoScored e Sofascore bloccano l'accesso automatico, StatsBomb Open Data non copre queste stagioni, ESPN non ha coordinate. Per questo l'app non li mostra (nessun dato inventato o stimato).
