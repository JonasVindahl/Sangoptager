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
først når der trykkes Gem. **▶ Lyt** i gem-dialogen afspiller mixet med den
valgte balance, før der gemmes. Var stemmen eller melodien nær-stille under
optagelsen (glemt mikrofon, musikken spillede ikke), eller blev stemmen så
kraftig at lyden blev forvrænget, advarer dialogen, før der gemmes. Alle sange loudness-normaliseres (EBU R128, kan slås fra i ⚙),
så afspilningslisten ikke hopper i lydstyrke. Crasher noget undervejs, ligger
de rå spor stadig klar, og appen tilbyder at gemme dem næste gang den åbnes.
Mellemrumstasten starter/stopper også optagelsen. Titel-feltet foreslår
eksisterende sangtitler fra biblioteket, mens der skrives. Forsvinder
mikrofonen midt i en optagelse (USB/Bluetooth), advarer statuslinjen efter
2 sekunder; forsvinder **melodien** undervejs — typisk fordi lyden skiftede
til en anden højttaler — nævner gem-dialogen det bagefter. Er der under
500 MB fri diskplads, spørger appen inden optagelsen, og svigter disken
midt i det hele, siger statuslinjen til med det samme. Appen kan kun køre i
én instans — endnu et dobbeltklik fronter bare det åbne vindue.
Fejlsøgning: `%APPDATA%\Sangoptager\app.log`.

**Niveaumetrene** er inddelt i dB som et studiemeter, ikke i lineær amplitude.
Bunden ligger ved -60 dBFS, og der er lige langt mellem hver 6 dB hele vejen,
så en aflæsning betyder det samme uanset hvor på bjælken den står: er stregen
på -6, er der 6 dB tilbage til loftet. To streger på den tomme del af bjælken
markerer farveskiftene, så skalaen også kan aflæses, når udslaget er lille.

Hver bjælke viser to ting på én gang. **Fyldet** er RMS — den oplevede
lydstyrke. Den **hvide streg** er sporets højeste sampleværdi (peak), og det er
den, farverne og dB-udlæsningen hører til: grønt indtil -12 dB, gult derfra,
rødt fra -6 dB. Afstanden mellem fyld og streg er stemmens dynamik, typisk
12-15 dB — derfor kan et pænt RMS sagtens dække over toppe der rammer loftet.

Sker det, er det ikke længere en farve, men en **rød blok yderst på bjælken**
og teksten **KLIP** i stedet for dB-tallet. Først dér er lyden reelt
forvrænget, og hverken lydbalancen eller normaliseringen kan rette op på det
bagefter. Klipper stemmen i mere end et sekund i alt, siger gem-dialogen til
med råd om at synge længere fra mikrofonen eller skrue ned for mikrofonens
niveau i Windows.

**Synkronisering:** Melodisporet skal dække nøjagtig samme tidsrum som
mikrofonen — fra Optag til Stop. Det kræver to modtræk, fordi WASAPI-loopback
kun leverer data, mens der faktisk afspilles lyd.

For det første afspiller appen selv konstant, uhørlig stilhed på melodikildens
enhed, så længe der optages. Så har Windows altid noget at rendere, endpointet
sover ikke, og loopbacken leverer uafbrudt.

For det andet — som sikkerhedsnet, hvis det skulle svigte — sammenlignes ved
hver buffer den forløbne tid med hvor meget sporet har modtaget. Mangler der
over et halvt sekund, fyldes hullet med stilhed. Tærsklen er sat højt med
vilje: buffere leveres nogle gange i små bundter, og en lavere grænse ville
indsætte stilhed, der aldrig var der. Uden dette forsvandt sekunderne fra en
pause i videoen helt ud af sporet, og resten af sangen lå for tidligt — hvilket
ramte omkring hver tiende optagelse.

Mixet forskyder aldrig noget. Tre tidligere forsøg på at rette timingen dér
gjorde det kun værre — ADC-tidsstempler uden fælles nulpunkt (v1.3.0–v1.6.0),
sporlængde-differencen (v1.9.0, som også indeholdt pausen efter musikken
stoppede) og en målt forskydning med for lavt loft (v1.10.0). Problemet hørte
hjemme i optagelsen. Disk-skrivning sker i en
separat tråd, og tabte buffere (overbelastet PC) udløser en advarsel i
gem-dialogen. De rå spor arkiveres desuden i
`%APPDATA%\Sangoptager\raa_spor\` (seneste 10 optagelser / 14 dage), så en
skæv optagelse kan re-mixes i stedet for at skulle synges om.

**Selv-opdatering:** Appen tjekker GitHub Releases ved opstart. Er der en ny
version, vises et banner med "Opdatér nu" — appen downloader, verificerer
zippens SHA256 mod releasens `.sha256`-fil, udskifter sig selv og genstarter.

Den installerede version står i **øverste venstre hjørne** ved siden af navnet,
så man altid kan se, om en opdatering rent faktisk er slået igennem. Vil man
ikke vente på næste opstart, ligger der et **"Søg nu"** under ⚙ →
*Opdatering*, som svarer med det samme — også når alt er opdateret. Skete
udskiftningen ikke — updateren fejlede i tavshed — opdager appen det ved næste
opstart og skifter banneret ud med "Hent manuelt", der åbner download-siden,
i stedet for at tilbyde den samme opdatering igen og igen.

Opdateringen rammer **den mappe appen faktisk kører fra** (`sys.executable`),
ikke en hardkodet sti — så en genvej på skrivebordet peger på samme exe
bagefter og bliver ved med at virke, uanset hvor mappen ligger. Kun appens
egen `_internal\`-mappe spejles (`/MIR`); selve programmappen kopieres
additivt, så filer man selv har lagt der ikke slettes. Kan mappen ikke skrives
(fx `C:\Program Files`), siger banneret det i stedet for at hente forgæves.
Selve udskiftningen sker i **appens egen proces**. Tidligere klarede en
bat-fil det med robocopy, men den fejlede hver eneste gang hos brugeren,
selvom appens egen skrivetest i samme mappe lykkedes — netop mønstret for
Windows' Kontrolleret mappeadgang, der beskytter bl.a. Dokumenter mod
fremmede processer. Filer i brug kan ikke overskrives, men de kan omdøbes:
den gamle udgave flyttes til `.gammel` og ryddes ved næste opstart. Fejler
noget undervejs, rulles alt tilbage, så installationen aldrig efterlades
halvfærdig, og appen kører videre som før.

Indstillinger (⚙): valg af **mikrofon** og **melodikilde** (hvilken højttaler
melodien afspilles på), sync-mappe, kunstnernavn og manuelt opdaterings-tjek. Gemmes i
`%APPDATA%\Sangoptager\config.json`. Skift til Syncthing = peg bare
sync-mappen et andet sted hen.

## Filformat (kompatibelt med det gamle bibliotek)

- Filnavn: `INVDATO_INVTID_Titel.mp3` — dato/tid er "inverteret" (9999-år osv.)
  så nyeste optagelse sorterer først alfabetisk.
- Mappe pr. måned = album: `2026-07\`
- ID3-tags: titel, album=`ÅÅÅÅ-MM`, kunstner (standard "Far"), dato,
  tracknummer hvor **nyeste = 1**. Hele månedsmappen gennemgås ved hvert gem,
  men kun filer med forkerte tags skrives — så Syncthing og Navidrome ikke
  skal re-synkronisere hele måneden, hver gang der gemmes én sang.
- Syncthings konfliktkopier (`…sync-conflict-….mp3`) ignoreres, så de hverken
  får tracknumre eller dukker op som sange i biblioteket.

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
