# Sangoptager

Én Windows-app der erstatter hele det gamle setup (OBS + VLC + script):
den optager **mikrofon + det PC'en afspiller** (melodien) som to separate spor,
mixer dem med justerbar **lydbalance** og gemmer direkte som MP3 (320 kbps) i
sync-mappen — med præcis samme navngivning og MP3-tags som det gamle system,
så det eksisterende bibliotek ikke knækker.

## Sådan bruges den

1. Åbn **Sangoptager** — vinduet ligger altid øverst.
2. Start melodien på PC'en, tryk **● Optag** og syng.
   De to niveaumetre (*Stemme* og *Melodi*) viser at begge kilder har signal.
3. Tryk **■ Stop** → skriv sangens navn → **Gem** (eller **Slet optagelse**).
4. Filen lander i `<sync-mappe>\ÅÅÅÅ-MM\` og synkroniseres af Nextcloud/Syncthing.

Lydbalancen kan justeres både under optagelsen og i gem-dialogen — mixet sker
først når der trykkes Gem. Crasher noget undervejs, ligger de rå spor stadig
klar, og appen tilbyder at gemme dem næste gang den åbnes.
Mellemrumstasten starter/stopper også optagelsen.

Indstillinger (⚙): valg af **mikrofon** og **melodikilde** (hvilken højttaler
melodien afspilles på), sync-mappe og kunstnernavn. Gemmes i
`%APPDATA%\Sangoptager\config.json`. Skift til Syncthing = peg bare
sync-mappen et andet sted hen. Niveaumetrene viser RMS med peak-hold og
dB-udlæsning, så man kan se at begge kilder har signal, før man synger løs.

## Filformat (kompatibelt med det gamle bibliotek)

- Filnavn: `INVDATO_INVTID_Titel.mp3` — dato/tid er "inverteret" (9999-år osv.)
  så nyeste optagelse sorterer først alfabetisk.
- Mappe pr. måned = album: `2026-07\`
- ID3-tags: titel, album=`ÅÅÅÅ-MM`, kunstner (standard "Far"), dato,
  tracknummer hvor nyeste = 1. Hele månedsmappen re-tagges ved hvert gem.

## Udvikling (macOS/Linux)

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest tests/
.venv/bin/python run.py   # GUI — kun mikrofon; systemlyd-optagelse er Windows-only
```

## Bygning til Windows

### Automatisk (GitHub Actions — anbefalet)

Hvert push til `main` bygger appen på en Windows-runner i skyen: tests køres,
ffmpeg hentes og bundles, og den færdige app uploades som artifact
(**Actions-fanen → seneste kørsel → "Sangoptager-windows"**).
Zippen indeholder ALT — Python, Qt, lydbiblioteker og ffmpeg — så på
mål-PC'en er det bare: pak ud → dobbeltklik `Sangoptager.exe`.

Tag en version (fx `git tag v1.0.0 && git push --tags`) for at få en
GitHub Release med en færdig `Sangoptager-windows.zip`.

Bemærk: Første gang appen startes, kan Windows SmartScreen advare, fordi
exe'en ikke er kodesigneret — vælg "Flere oplysninger" → "Kør alligevel".

### Manuelt

På en Windows-maskine med Python 3.11+:

```bat
py -m venv .venv
.venv\Scripts\pip install -e .[build]
:: Hent ffmpeg (essentials-build) fra https://www.gyan.dev/ffmpeg/builds/
:: og læg ffmpeg.exe i resources\
.venv\Scripts\pyinstaller build.spec
```

Resultatet ligger i `dist\Sangoptager\` — kopiér mappen til fars PC og lav en
skrivebordsgenvej til `Sangoptager.exe`. Ingen OBS, VLC eller Python-installation
er nødvendig på hans maskine.

## Arkitektur

| Modul | Ansvar |
|---|---|
| `sangoptager/audio/capture.py` | Optager mic + WASAPI-loopback til to WAV-filer (PyAudioWPatch på Windows) |
| `sangoptager/audio/mixdown.py` | ffmpeg: equal-power balance-mix + limiter → MP3 320k |
| `sangoptager/library.py` | Navngivning (inverteret dato), parsing og ID3-retagging — porteret 1:1 fra det gamle script |
| `sangoptager/ui/` | PySide6-GUI: hovedvindue, gem-dialog, indstillinger |
| `sangoptager/settings.py` | `config.json` + temp-mappe til rå spor |

Det gamle system ligger i `V1_OBS.zip` som reference.
