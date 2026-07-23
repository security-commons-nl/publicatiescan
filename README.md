# publicatiescan

Vind persoonsgegevens die per ongeluk in je eigen publicaties staan — BSN, IBAN,
NAW, e-mail — in PDF, DOCX, XLSX en PPTX.

Gemeenten publiceren duizenden documenten: bekendmakingen, raadsstukken, ingekomen
brieven, Woo-besluiten, vergunningen. Daar zitten met enige regelmaat
persoonsgegevens in die er niet in horen. Handmatig controleren is geen optie — het
zijn er te veel. Deze tool doet wat een journalist ook zou doen: de publicatiekanalen
uitlezen, de tekstlaag uit de documenten halen, en zoeken naar patronen die alleen een
persoonsgegeven kúnnen zijn (een 9-cijferige reeks die de elfproef doorstaat, een IBAN
dat de mod-97-controle doorstaat).

Het verschil is dat jij het als eerste weet.

> ## Scope
>
> **Richt deze tool uitsluitend op de publicatiekanalen van je eigen organisatie.**
> Het scannen van portalen van een andere organisatie is scannen zonder grondslag,
> hoe goed je bedoelingen ook zijn.
>
> De crawler respecteert `robots.txt`, houdt een pauze tussen requests aan, en
> identificeert zich met een user-agent waarin jouw organisatie en een contactadres
> staan. Laat dat zo.

---

## Handleiding

### 1. Installeren

Je hebt Python 3.10 of nieuwer nodig.

```bash
git clone https://github.com/security-commons-nl/publicatiescan.git
cd publicatiescan

python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # macOS / Linux

pip install -r requirements.txt
```

### 2. Config invullen

```bash
copy config.example.yaml config.yaml     # Windows
# cp config.example.yaml config.yaml     # macOS / Linux
```

Open `config.yaml`. Vier dingen moet je zelf invullen; de rest kun je laten staan.

**`eigen_domeinen`** — je eigen e-maildomeinen.

```yaml
eigen_domeinen:
  - jouwgemeente.nl
```

Dit is belangrijker dan het lijkt. Een e-mailadres van een medewerker op een
werkadres is geen datalek; een privé-adres van een inwoner in een ingekomen brief
wél. De scanner gebruikt dit veld om het verschil te maken — vul je het niet in, dan
komt élk e-mailadres als bevinding terug en verdrinkt het echte signaal in je eigen
organisatie-adressen.

**`gemeenten`** — je organisatienaam zoals die in de officiële bekendmakingen staat.

```yaml
gemeenten:
  - Jouwgemeente
```

Dat is het veld `dt.creator` in de landelijke bekendmakingen-API, en dat is voor
gemeenten vrijwel altijd gewoon de gemeentenaam, zonder "gemeente" ervoor. Werk je
voor een samenwerkingsverband, zet ze dan allemaal in de lijst — dan scan je in één
run voor alle deelnemers.

**`output_dir`** — waar de downloads en het rapport landen.

```yaml
output_dir: "D:/scan-output"
```

**Zet dit buiten je OneDrive, SharePoint of Dropbox.** Het kunnen duizenden bestanden
worden, en ze bevatten per definitie mogelijk persoonsgegevens. Die wil je niet laten
synchroniseren naar een gedeelde map.

**`politeness.user_agent`** — je organisatie en een echt contactadres.

```yaml
user_agent: "Gemeente Jouwgemeente publicatiescan (interne AVG-controle; contact: informatiebeveiliging@jouwgemeente.nl)"
```

Een beheerder die dit verkeer in zijn logs ziet, moet binnen een minuut kunnen zien
wie je bent en je kunnen bellen. Dat is niet alleen netjes, het voorkomt ook dat je
eigen SOC een incident opent op jouw scan.

### 3. Draaien — begin met de bekendmakingen

Er zijn twee manieren om aan documenten te komen, en je begint met de makkelijke.

**De API-route (`--sru`).** Alle officiële bekendmakingen van alle Nederlandse
overheden — Gemeenteblad, Staatscourant, Waterschapsblad — staan op
`officielebekendmakingen.nl` en zijn volledig op te halen via de landelijke KOOP
SRU-API. Dat is een echte API met directe PDF-links: geen crawlen, geen HTML parsen,
geen belasting van je eigen webserver. Overheid.nl ontraadt scrapen van dat kanaal
expliciet en wijst deze API aan als de norm. Voor de meeste gemeenten is dit meteen
het grootste kanaal in aantallen documenten — vergunning-kennisgevingen met adressen
in de titel zitten hier allemaal in.

De scanner haalt per bekendmaking twee dingen op: de **kennisgevingstekst** zelf én
de **externe bijlagen** — het onderliggende besluit, de brief aan de aanvrager. Dat
onderscheid is belangrijker dan het lijkt. De bijlage is het document waar de
persoonsgegevens in staan (naam en woonadres van de aanvrager), maar hij is in de
API vrijwel onzichtbaar: geen eigen record, geen directe link, alleen een
metadataveld aan het moederrecord. In een steekproef had ruim een derde van de
gemeentelijke bekendmakingen zo'n bijlage.

> **Draaide je een eerdere versie van deze scanner?** Versies van vóór 22-07-2026
> scanden alleen de kennisgevingsteksten en sloegen de externe bijlagen stil over.
> Een schone uitkomst uit zo'n run zegt dus niets over de bijlagen — draai het
> kanaal opnieuw.

Begin met een korte proefrun:

```bash
python avg_scan.py --config config.yaml --sru --sru-max 50
```

Dat haalt de 50 meest recente bekendmakingen op, scant ze, en schrijft een rapport.
Duurt een paar minuten. Werkt dat, dan de volle run:

```bash
python avg_scan.py --config config.yaml --sru --sru-max 5000
```

Wil je alleen recent materiaal, gebruik dan een datumgrens:

```bash
python avg_scan.py --config config.yaml --sru --sru-since 2026-01-01
```

**De crawl-route.** Voor kanalen zonder API — je Woo-portaal, je eigen website.
Vul `seeds` in `config.yaml` en draai zonder `--sru`:

```bash
python avg_scan.py --config config.yaml --max-pages 200      # proefrun
python avg_scan.py --config config.yaml                      # volle run
```

### Meerdere bronnen tegelijk (`bronnen:`)

Naast de losse `--sru`- en crawl-route kun je in `config.yaml` een lijst `bronnen`
zetten. Elke bron heeft een `type` dat naar een connector wijst, plus zijn eigen
parameters. Staat er een `bronnen:`-lijst, dan draait één run ze allemaal achter elkaar
en schrijft één gecombineerd rapport. Zo scan je bekendmakingen, raadsinformatie en je
website in één keer.

```yaml
bronnen:
  - { naam: bekendmakingen, type: sru, gemeenten: [Jouwgemeente], vanaf: "2014-01-01" }
  - { naam: raad, type: openraadsinformatie, gemeenten: [Jouwgemeente] }
```

De beschikbare types:

| type | wat | status |
|---|---|---|
| `sru` | officiële bekendmakingen (KOOP-API) | werkend |
| `crawl` | HTML-crawl van je eigen website/portalen | werkend |
| `openraadsinformatie` | raadsinformatie via de landelijke Elasticsearch-API; **de tekst is daar al geëxtraheerd**, dus geen download en snel. Dezelfde bron die de VNG voor haar tweede lijst gebruikt. Dekking en actualiteit verschillen per gemeente | werkend |
| `parlaeus` / `qualigraf` | raadsinformatie Qualigraf/Parlaeus (**zelfde platform**, twee domeinen). Enumereert de publieke modules (ingekomen stukken, moties, verordeningen, ...) en haalt de bijlagen op. Met `van`/`tot` scan je de **volledige historie**. `robots.txt` = `Disallow: /`, dus draaien mag pas ná crawl-akkoord + SOC/leverancier informeren | werkend |
| `mijnpublicaties` | terinzageleggingen op [mijnpublicaties.nl](https://mijnpublicaties.nl) (TerInzageLeggingPortaal), **23 gemeenten aangesloten** waaronder Amsterdam, Haarlem, Breda, Zwolle en Zoetermeer. Geen sleutel, geen login, geen `robots.txt`. Zet `organisatie_naam` (exacte naam op de hoofdpagina) of `organisatie` (GUID uit de portaal-URL) | werkend |
| `notubiz` | raadsinformatie Notubiz | nog niet af — faalt luid ([bouwplan](#bouwen-aan-notubiz-en-ibabs)) |
| `ibabs` | raadsinformatie iBabs; vereist `sitename` + `api_key` (geen open route) | skelet — faalt luid ([bouwplan](#bouwen-aan-notubiz-en-ibabs)) |

Over `mijnpublicaties` twee dingen die je moet weten voordat je de uitkomst leest. Het portaal
toont alleen wat **op dit moment ter inzage ligt**, geen archief: een lege of kleine uitkomst
betekent "weinig lopende terinzageleggingen", niet "schoon verleden". En veel organisaties
publiceren er enkel een **samenvatting**; de onderliggende dossierstukken gaan op verzoek en
zijn dus niet te scannen. Voor historie is `sru` de aangewezen bron. De kracht van deze bron is
juist preventief: draai hem periodiek en je ziet een probleem *tijdens* de inzagetermijn,
niet jaren later via een datalekmelding.

Let op: **de risico's zitten in de bijlagen, niet in de hoofddocumenten.** In het
raadsinformatiesysteem zijn dat ingekomen brieven van inwoners met hun bijlagen; bij de
bekendmakingen zijn het de externe bijlagen met het onderliggende besluit. In beide
gevallen is het hoofddocument (agenda, kennisgeving) vrijwel altijd schoon en zit het
persoonsgegeven één laag dieper. Elk RIS-product werkt anders, en sommige zijn
JavaScript-apps waar de crawler niet doorheen komt — daarvoor zijn deze connectors. Een
connector die niet kan draaien **faalt luid** en wordt als 'niet uitgevoerd' gemeld; een
lege uitkomst betekent hier dus nooit vanzelf 'schoon'.

### Bouwen aan Notubiz en iBabs

Twee connectors zijn nog niet af. Gebruikt jouw gemeente Notubiz of iBabs, dan is dit
je startpunt — het patroon staat in `avgscan/bronnen.py`: een connector is een generator
die `Document`-objecten `yield`t (elk met een `url` om te downloaden, óf directe `text`/
`chunks`), en die **luid faalt** (`raise BronNietGereed(...)`) zodra hij niet kan draaien.
Kijk naar `_parlaeus` als werkend voorbeeld: eerst de lijst-endpoint enumereren, dan per
item de bijlagen ophalen. Een PR is welkom.

**Notubiz** — het dichtst bij af. Het downloaden van één document is al geverifieerd
(`GET https://api.notubiz.nl/document/<id>/1` → `application/pdf`). Wat nog ontbreekt is
de **enumeratie** van alle document-id's per gemeente:

1. Zoek de organisatie-id van je gemeente op (`api.notubiz.nl/organisations`).
2. Loop de events/agenda's af (`/events?organisation_id=<id>`, gepagineerd op datum) en
   verzamel per event de gekoppelde document-id's uit de agendapunten en bijlagen.
3. `yield Document(bron=naam, url="https://api.notubiz.nl/document/<id>/1", ext="pdf")`.
4. Voeg een `van`/`tot`-datumvenster toe, net als bij `_parlaeus`, voor de volledige historie.

> Sneller alternatief: **Open Raadsinformatie ingest Notubiz al**. Probeer eerst het type
> `openraadsinformatie` — dan hoef je deze connector misschien niet te bouwen.

**iBabs** — geen open publieke route. Toegang loopt via de iBabs-API (`api.ibabs.eu`) met
een **sitename + API-sleutel per gemeente**, die je bij je iBabs-beheerder opvraagt. De
skelet-connector faalt luid zolang die credentials ontbreken. Bouwstappen zodra je ze hebt:

1. Authenticeer met `sitename` + `api_key` uit de bronconfig.
2. Enumereer de vergaderingen (`GetMeetings`, op datumvenster) en per vergadering de
   documenten/bijlagen (`GetMeeting` → documentreferenties).
3. `yield Document(...)` met de download-URL of, als de API bytes teruggeeft, met `text`.

Voor beide geldt: een module/endpoint die niets teruggeeft mag je stil overslaan
('niet aanwezig'), maar een connector die zijn werk niet kán doen moet **luid falen** —
nooit stil een lege lijst, want dat leest als 'schoon'.

Overige vlaggen:

| Vlag | Doet |
|---|---|
| `--sru` | Ingest via de bekendmakingen-API in plaats van crawlen |
| `--sru-creators Naam1,Naam2` | Overschrijft `gemeenten` uit de config |
| `--sru-max N` | Maximaal N records per organisatie (standaard 150) |
| `--sru-since JJJJ-MM-DD` | Alleen bekendmakingen vanaf deze datum |
| `--max-pages N` | Crawl-limiet, handig voor een proefrun |
| `--report-only` | Bouwt alleen het rapport opnieuw uit de bestaande status |

Onderbreken met Ctrl-C mag: de status staat in een SQLite-database in je output-map,
en de volgende run pakt de wachtrij weer op waar hij gebleven was. Identieke bestanden
worden op sha256 gededupliceerd, dus dezelfde bijlage op vijf plekken wordt één keer
gescand.

### 4. Het rapport lezen

Je krijgt `rapport.html` (printbaar) en `rapport.xlsx` in je output-map, gesorteerd op
ernst. **Alle gevonden waarden zijn gemaskeerd** — ook de andere persoonsgegevens die
toevallig in hetzelfde tekstfragment stonden. Het rapport is dus zelf geen datalek.

| Ernst | Wat |
|---|---|
| **Kritiek** | BSN (elfproef geldig) · paspoort-/rijbewijsnummer nabij een ID-term |
| **Hoog** | IBAN (mod-97 geldig) · geboortedatum die expliciet zo benoemd wordt · **persoonsnaam met een woonadres er direct bij** |
| **Middel** | E-mail op een vreemd domein · mobiel nummer · **afwijkend adres** · persoonsnaam in een aanhef · verborgen Excel-tabblad |
| **Laag** | E-mail op je eigen domein · vast nummer · het onderwerp-adres van een besluit |

Over die naam-adres-combinatie: dit is precies hoe een publicatielek er in de praktijk
uitziet. Een besluitbrief hoort niet gepubliceerd te worden mét de aanhef en het
woonadres van de aanvrager erin — maar het adres alléén is als onderwerp-adres van het
besluit juist verwacht (ernst Laag), en een naam alléén kan ook een wethouder zijn
(ernst Middel). Pas de combinatie maakt het een vrijwel zeker lek, en die krijgt dus
Hoog.

---

## Je hebt een hit. Wat nu?

**Het rapport ordent en maskeert. Het oordeelt niet.** Elke bevinding vraagt menselijke
beoordeling, en de meeste zijn geen lek.

**Begin bij Kritiek.** Een BSN in een gepubliceerd document is vrijwel nooit legitiem.
De elfproef geeft weinig vals-positieven, maar hij is niet waterdicht: een zaaknummer
van negen cijfers kan hem toevallig doorstaan. Open het document en kijk.

**Let op de adres-triage.** De scanner scheidt het *onderwerp-adres* van een *afwijkend
adres*. Een verleende omgevingsvergunning hóórt het bouwadres te noemen — dat is geen
lek, en die hits worden naar Laag gezet. Maar staat er een postcode in het document die
niet het onderwerp-adres is, dan blijft die op Middel staan met de opmerking *"adres
niet op pagina 1 — mogelijk niet het onderwerp-adres"*. **Dat is je signaal.** Dat is
het adres van de bezwaarmaker, de buurman, de briefschrijver. Kijk daar het eerst.

**Bij een echt lek: niet stil verwijderen.** Dit is de fout die het vaakst gemaakt
wordt. Raadsstukken, Woo-besluiten en bekendmakingen kennen een publicatieplicht — je
mag ze niet zomaar offline halen. De juiste handeling is het document **vervangen door
een correct geanonimiseerde versie**.

**Leg de AVG-afweging bij je FG.** Of dit een datalek is dat gemeld moet worden bij de
Autoriteit Persoonsgegevens, en of betrokkenen geïnformeerd moeten worden, is niet aan
de scanner en niet aan de scanner-bediener. Dat is het werk van je Functionaris
Gegevensbescherming of privacy-officer. Lever de bevinding, lever het document, laat de
afweging daar.

**Deel je bevindingen niet in een issue.** Zie [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Wat het niet doet

**Gescande PDF's zonder tekstlaag.** Een ingescande brief is voor deze tool standaard
een plaatje. Ze worden geteld en gemarkeerd als OCR-kandidaat. Wil je ze wél lezen, zet
dan **OCR** aan (`ocr.enabled: true` in de config, of de vlag `--ocr`). Dat gebeurt
on-prem via rapidocr — geen externe aanroep, dus de persoonsgegevens blijven op je eigen
machine. Installeer daarvoor de extra dependency:

```bash
pip install rapidocr-onnxruntime
```

Let op: OCR is **traag** (seconden per pagina) en niet bit-identiek reproduceerbaar zoals
de gewone tekstlaag. Bevindingen uit OCR krijgen de locatie `"pagina N (OCR)"`, zodat de
triage ziet dat zo'n hit een extra kritische blik verdient. Juist hier zit vaak het risico:
een gescand, ondertekend formulier met een BSN erop.

**Documenten die niet online staan.** Vergunningdossiers met situatietekeningen en
ondertekende formulieren staan bij veel gemeenten niet in een portaal maar worden op
verzoek gemaild. Die krijg je hiermee niet te pakken; dat moet je organisatorisch
borgen.

**Een zwarte balk over een leesbare tekstlaag** wordt wél gevonden — de tekst zit er
immers nog. Dat is een van de drie klassiekers, samen met verborgen Excel-tabbladen
(worden gemeld) en persoonsgegevens in documentmetadata (worden gescand).

## Structuur

```
avg_scan.py            CLI + orkestratie (ingest → analyse → rapport)
config.example.yaml    voorbeeldconfig
avgscan/
  config.py            config laden/normaliseren
  bronnen.py           connector-registry (sru/crawl/openraadsinformatie/notubiz/parlaeus/
                       ibabs/mijnpublicaties)
  sru.py               ingest via de KOOP SRU-API (bekendmakingen)
  crawl.py             beleefde crawler (robots.txt, rate limit, domeinfilter)
  fetch.py             download + sha256 (dedup, groottelimiet)
  extract.py           tekstlaag + metadata per bestandstype
  detect.py            detectors + validators (elfproef, mod-97) + maskering
  report.py            HTML- en Excel-rapport
  state.py             SQLite-status (hervatten + dedup + findings)
```

## Licentie

[EUPL-1.2](LICENSE) — Europese open-sourcelicentie, dezelfde als de rest van
[security-commons-nl](https://github.com/security-commons-nl).
