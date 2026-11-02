# Å gi containeren til studenten

## Du gjør dette én gang

1. Legg `container/` og `.github/workflows/container.yml` i repoet.
2. `git tag v1 && git push origin v1`
3. Vent på at Actions blir grønn.
4. Gjør pakken offentlig under Packages på GitHub, eller gi studenten et
   token.
5. På Fox, hent den ned én gang til et sted dere begge når:

```bash
mkdir -p $HOME/images
apptainer pull $HOME/images/boltz.sif docker://ghcr.io/<eier>/<repo>/boltz:v1
```

Imaget er en vanlig fil. Studenten kan kopiere den, eller hente sin egen med
samme kommando.

## Hva studenten må vite

Dette:

```bash
./boltz_run.sh predict inputs/full.yaml --out_dir results/ --seed 1
```

Og ingenting mer. Scriptet finner imaget hvis det finnes, og faller tilbake
på pip-miljøet hvis ikke. Kommandoen er identisk begge veier, og scriptet
skriver ut hvilken det brukte.

Han trenger ikke lære Apptainer for å jobbe i prosjektet. Han bør lære det
før han skriver metodedelen, men det er et annet tidspunkt.

## Kan han vente med containeren til slutt?

Ja, men ikke uten å ta en forholdsregel.

Problemet med å bytte til slutt er at pip-miljøet og containeren kan være
ulike. Da dokumenterer containeren et miljø som ikke produserte resultatene,
og det er verre enn ingen container.

Tre ting fjerner problemet:

**Frys pip-miljøet mot imaget.** Ett oppslag:

```bash
apptainer exec $HOME/images/boltz.sif cat /opt/versions.txt > container_versions.txt
pip list --format=freeze > venv_versions.txt
diff <(sort container_versions.txt) <(sort venv_versions.txt)
```

Er diffen tom for de pakkene som betyr noe, altså boltz, torch og
cuequivariance, er de to miljøene samme stack. Da er det likegyldig hvilket
som kjørte hva.

**Kjør én sammenligning.** Samme input, samme seed, begge veier:

```bash
BOLTZ_IMAGE=/dev/null ./boltz_run.sh predict inputs/full.yaml \
    --out_dir cmp_venv/ --seed 1 --diffusion_samples 1
./boltz_run.sh predict inputs/full.yaml \
    --out_dir cmp_container/ --seed 1 --diffusion_samples 1
```

Så `compare.py` mellom de to. Legg tallet i loggboka. Det er en billig
forsikring, og det er noe en fagfelle vil spørre om.

Merk at identiske tall ikke er å forvente uansett. GPU-beregninger er ikke
bitreproduserbare på tvers av kort og drivere. Det du ser etter er at
strukturene er praktisk talt like, ikke at filene er identiske.

**Bruk containeren for alt som skal rapporteres.** Utforskning i pip er
greit. Enhver kjøring som gir et tall i rapporten bør gå gjennom imaget, og
`provenance.sh` fanger hvilket image det var.

## Min anbefaling

Bygg imaget nå, mens du har hodet i det. Det koster deg et kvarter.

La studenten bruke `boltz_run.sh` fra første dag. Da får han containeren
gratis uten å tenke på den, og dere unngår hele spørsmålet om å bytte
underveis.

Det eneste tidspunktet å unngå er å bygge imaget *etter* at resultatene er
produsert. Da dokumenterer det noe annet enn det som faktisk skjedde.
