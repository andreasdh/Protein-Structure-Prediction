# Container

## Hva det løser, og hva det ikke løser

Containeren får ikke Boltz til å virke. Det gjør pip allerede. Den løser tre
andre ting.

**Reproduserbarhet.** Et pip-miljø drifter. Om to år gir `pip install boltz`
en annen versjon, mot en annen CUDA, med andre avhengigheter. En `.sif`-fil er
frosset. Forskjellen er mellom å skrive «vi brukte Boltz 2.2.1» og å kunne
levere nøyaktig det som ble kjørt.

**Inoder.** Miljøet er titusenvis av filer. Containeren er én.

**Overføring.** Studenten, en samarbeidspartner eller en fagfelle kjører samme
stack ved å hente én fil.

Det er altså ikke et verktøy for å få noe til å virke, men for å kunne vise
hva som virket.

## Hvorfor du ikke bygger den på Fox

Å kjøre en container krever ingen spesielle rettigheter. Å bygge en krever
root eller fakeroot, som du ikke har på en klynge. Apptainer kjører heller
ikke på macOS.

Løsningen er at GitHub bygger den. Workflow-filen ligger i
`.github/workflows/container.yml`.

## Bygge

Commit, tagg og push:

```bash
git tag v1
git push origin v1
```

Følg med under Actions-fanen på GitHub. Den bygger et Docker-image og
publiserer det til GitHub Container Registry. Tar typisk 10 til 20 minutter,
mest fordi PyTorch og CUDA er store.

Pakken er privat som standard. Gjør den offentlig under Packages, eller
autentiser på Fox med et personal access token.

## Bruke den på Fox

```bash
apptainer pull boltz.sif docker://ghcr.io/<eier>/<repo>/boltz:v1
```

Apptainer henter Docker-laget og konverterer til én `.sif`-fil. Så:

```bash
apptainer exec --nv \
    --bind $HOME/boltz_cache:/opt/boltz_cache \
    boltz.sif boltz predict inputs/full.yaml --out_dir results/
```

Flaggene som betyr noe:

| Flagg | Hva det gjør |
| --- | --- |
| `--nv` | eksponerer vertens NVIDIA-drivere inn i containeren. Uten dette ser programmet ingen GPU |
| `--bind A:B` | gjør mappe `A` på verten tilgjengelig som `B` inne i containeren |
| `exec` | kjører en kommando du oppgir |
| `run` | kjører det `%runscript` definerer |
| `shell` | gir deg et interaktivt skall inne i containeren, nyttig for feilsøking |

Hjemmeområdet og arbeidsmappa bindes inn automatisk, så filene dine er der.
Modellvektene må du binde eksplisitt, som over, siden de ligger utenfor
imaget.

I et Slurm-skript:

```bash
#SBATCH --partition=accel
#SBATCH --gpus=1
#SBATCH --mem=64G

apptainer exec --nv \
    --bind $HOME/boltz_cache:/opt/boltz_cache \
    $HOME/images/boltz.sif \
    boltz predict inputs/full.yaml --out_dir results/
```

Ingen `module load`, ingen `source activate`. Alt ligger i imaget.

## Sjekke hva som er inni

```bash
apptainer exec boltz.sif cat /opt/versions.txt
```

Det er den linjen som hører hjemme i metodedelen.

## Hvorfor vektene ikke ligger i imaget

Modellvektene er flere GB og ville gjort imaget uhåndterlig. De lastes ned
separat og bindes inn. Noter sjekksummene deres i `PROVENANCE.txt`, som
`provenance.sh` gjør automatisk.

## Når det er verdt å gjøre dette

Ikke nå, hvis målet bare er å komme i gang. Pip-miljøet holder.

Gjør det før produksjonskjøringene som skal inn i rapporten, og tagg en
versjon. Da kan metodedelen peke på et image som faktisk finnes, og tallene
kan etterprøves.
