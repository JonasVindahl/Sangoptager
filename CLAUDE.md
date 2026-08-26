# Sangoptager — guide til agenter

Windows-app der optager mikrofon + systemlyd til to spor og gemmer dem som MP3
i en sync-mappe. Se `README.md` for hvordan den virker og hvorfor.

**Det vigtigste at vide først:** appen opdaterer sig selv. Den kører på en
rigtig persons PC og henter nye versioner fra GitHub Releases ved opstart. En
udgivelse er derfor ikke en neutral handling — den lander hos brugeren af sig
selv, uden at nogen trykker på noget.

## Udgivelse sker automatisk ved merge til main

Der er ingen manuel udgivelses-knap. `.github/workflows/build.yml` kører ved
push til `main` og gør følgende:

1. Kører `pytest tests/ -q`
2. Bygger exe'en med PyInstaller og bundler ffmpeg
3. **Aflæser `__version__` fra `sangoptager/__init__.py`**
4. Findes der ikke allerede et release med tagget `v<version>`, oprettes tag,
   release, `Sangoptager-windows.zip` og `.sha256`-filen som appens
   selv-opdatering verificerer imod

Konsekvensen: **merge til main + en bumpet version = udgivelse til brugeren.**
Er versionen ikke bumpet, bygger workflow'en stadig, men springer releaset over
(det findes jo) — det er sådan en ren oprydning kan lande uden at udgive noget.

### Regler

1. **Arbejd på en gren, aldrig direkte på `main`.** Åbn en PR, og lad
   mennesket merge. Merge er udgivelsen — det er ikke agentens beslutning.
2. **Kør testene lokalt, før du beder om merge.** Workflow'en har *ingen*
   `pull_request`-trigger; testene kører først efter merge. En PR med røde
   tests ser grøn ud, indtil den er landet på main.
3. **Lav aldrig tags eller releases i hånden.** Workflow'en ejer dem. Et
   håndlavet `v…`-tag får den til at springe releaset over, og så bygges der
   aldrig en zip til det tag — brugeren får tilbudt en opdatering, der ikke
   findes.
4. **Fejler build eller tests på main, er der ikke udgivet noget** (release-
   trinnet nås aldrig), men koden *er* landet. Ret fejlen og bump igen.
5. Rør ikke ved `.sha256`-filen eller zip-navnet. `update.py` slår dem op på
   navn (`ASSET_NAME`, `CHECKSUM_NAME`) og nægter at installere, hvis
   checksummen ikke passer.

## Versionering

`sangoptager/__init__.py` er eneste sandhed:

```python
__version__ = "1.16.0"
```

`pyproject.toml` læser den dynamisk, appen viser den øverst til venstre i
vinduet, og updateren sammenligner den med releasens tag. Den skal aldrig
skrives to steder.

### Format

Kun tal adskilt af punktummer — `1.16.0`. `update._parse_version` laver
`int()` på hvert led, og alt andet (`1.16.0rc1`, `1.16.0-beta`) kaster
`ValueError`, som `is_newer` fanger og oversætter til "ikke nyere". En
præ-release-version ville altså aldrig blive tilbudt til nogen.

Der sammenlignes som talrække, ikke som tekst, så `1.9.0 < 1.10.0` er rigtigt
af sig selv. Ingen nulpolstring.

**Altid tre led** — `major.minor.patch`. Python lader den korteste tuple tabe,
så `(1, 16)` er *mindre* end `(1, 16, 0)`. Stod der `"1.16"` i koden, mens
releaset hed `v1.16.0`, ville appen regne sig selv for ældre og tilbyde den
samme opdatering ved hver eneste opstart — og blive ved, for opdateringen
ville jo aldrig ændre noget.

### Hvornår bumpes hvad

| Led | Hvornår |
|---|---|
| Patch (`1.16.0` → `1.16.1`) | Rettelser, der ikke ændrer hvad brugeren ser eller gør |
| Minor (`1.16.0` → `1.17.0`) | Nye funktioner, ændret opførsel, nyt i UI'et |
| Major | Reserveret — noget der bryder biblioteket eller filformatet |

Filnavne og ID3-tags skal blive ved med at passe til det eksisterende
bibliotek. Ændres de, er det et major-bump og skal vendes med et menneske
først.

### Regler

1. **Bump i samme commit som ændringen**, der berettiger det, og skriv
   versionen forrest i commit-emnet: `v1.16.0: niveaumeter i dB med ægte peak`.
   Sådan ser resten af historikken ud — hold den linje.
2. **Bump kun, når det skal udgives.** Ren oprydning, tests eller kommentarer
   uden ændret opførsel: lad versionen stå, og lad workflow'en springe
   releaset over.
3. **Genbrug eller sænk aldrig en version.** Findes releaset, springer
   workflow'en udgivelsen over uden at fejle — ændringen lander på main og
   bliver aldrig udgivet, og intet siger fra. Updateren regner desuden med, at
   versioner kun går opad: `update_took_effect` afgør, om en opdatering slog
   igennem, ved at se om den kørende version er nået op på den ventede.
4. **Én version = én udgivelse.** Skal der rettes noget efter merge, så bump
   igen frem for at flytte et tag.
5. Bumper du versionen, så skriv også hvad der er nyt i `README.md`. Den er
   projektets faktiske dokumentation, ikke et appendiks.

## At arbejde i repoet

- **Sproget er dansk** — kode-kommentarer, docstrings, UI-tekster, commit-
  beskeder, logmeddelelser. Variabel- og funktionsnavne er engelske.
  Kommentarer forklarer *hvorfor*, ikke hvad linjen gør; flere steder står der
  hvilke tidligere forsøg der slog fejl, og hvorfor. Slet ikke den slags —
  det er dyrekøbt viden.
- **Tests:** `python -m pytest tests/ -q`. Kræver `pip install -e '.[dev]'`.
  Uden ffmpeg installeret springes mixdown-testene over — installér det, hvis
  du rører ved `audio/mixdown.py`, ellers tester du ikke det, du ændrer.
- **Optagelse er Windows-only.** `audio/capture.py` har to backends:
  PyAudioWPatch (mikrofon + WASAPI-loopback) på Windows og sounddevice (kun
  mikrofon) til udvikling på macOS/Linux. Ændrer du den ene, så tjek den anden
  — de deler `_Backend` og `_WavWriter`.
- **Lyd-callbacken må ikke blokere.** `_WavWriter.write` kalder ingen disk-I/O
  og allokerer ikke; skrivningen sker i en writer-tråd. Hold det sådan, ellers
  tabes samples.
- **Synkroniseringen mellem sporene er løst i optagelsen, ikke i mixet.** Fire
  tidligere forsøg på at kompensere i ffmpeg-filteret gjorde det værre. Læs
  `build_filter`s docstring, før du overvejer et femte.
