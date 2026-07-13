# Bijdragen aan publicatiescan

Iets delen of verbeteren? Drie manieren, van makkelijk naar technisch.

## 1. Iets aanbieden of melden — geen Git-ervaring nodig

→ [**Fout of verbetering**](https://github.com/security-commons-nl/publicatiescan/issues/new)
  Een detector die mist, een vals-positief, iets dat niet klopt of beter kan.

→ [**Ervaring delen**](https://github.com/security-commons-nl/publicatiescan/issues/new)
  Heb je de scan bij je eigen organisatie gedraaid? Wat kwam eruit, wat werkte niet?

Vul alleen in wat voor jou relevant is — we helpen je met de rest.
**Deel geen echte vondsten.** Geen BSN's, geen documenten, geen URL's naar een pagina
waar nog persoonsgegevens staan. Beschrijf het patroon, niet de gegevens.

**Geen GitHub-account?** [Maak er gratis een](https://github.com/signup) (2 minuten), of vraag iemand in je netwerk om namens jou te posten.

## 2. Meediscussiëren

→ [**Discussions**](../../discussions)

Voor vragen, ervaringen en ideeën zonder directe actie.

## 3. Voor ontwikkelaars — code aanleveren

### Meest waardevol: een detector die scherper wordt

De winst zit niet in méér detectoren, maar in minder ruis. Een 9-cijferreeks die de
elfproef doorstaat is niet per se een BSN — het kan een zaaknummer zijn. Elke PR die
vals-positieven omlaag brengt zonder echte hits te missen, is er een die telt.

### Nieuw of gewijzigd detectiepatroon

Wijzigingen in `avgscan/detect.py` vereisen een unit test in `tests/`, met minstens:
één geldige waarde die gevonden moet worden, en één plausibele bijna-waarde die
genegeerd moet worden.

Gebruik **nooit** een echt BSN of IBAN in een test. Genereer een syntactisch geldige
maar niet-uitgegeven waarde.

### Nieuw bestandstype

Extractors leven in `avgscan/extract.py`. Een nieuw type levert tekst én metadata op,
of het is niet af — de metadata (auteur, laatst-gewijzigd-door, verborgen tabbladen)
is waar de vondsten zitten die niemand verwacht.

### Lokale setup

```bash
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt pytest
pytest tests/ -v
```

## Scope

Deze tool is bedoeld om je **eigen** publicatiekanalen te controleren. PR's die
gericht scannen van andere organisaties makkelijker maken — omzeilen van `robots.txt`,
hogere snelheid, verhullen van de user-agent — worden niet gemerged.

---

**Organisatiebrede richtlijnen**: [security-commons-nl/.github](https://github.com/security-commons-nl/.github/blob/main/CONTRIBUTING.md)
