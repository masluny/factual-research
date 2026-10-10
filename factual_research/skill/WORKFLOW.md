# factual-research: workflow reference for agents

`factual-research status` always prints the project state and the next command.
All commands accept `--project <dir>`; otherwise the nearest folder with `.fr/` is used.

## Domain packs

`factual-research packs` lists them; `factual-research packs --show medicine` prints one.

| pack | trusted sources | evidence hierarchy (high → low) |
|---|---|---|
| medicine | PubMed, Europe PMC, OpenAlex, ClinicalTrials.gov, Crossref | guideline ≈ SR/MA > RCT > cohort > case-control > cross-sectional > case report > animal > in vitro |
| engineering | OpenAlex, Crossref, NASA NTRS, arXiv (+IEEE, CORE with keys) | standard > SR > journal > tech report > conference > preprint |
| cs_ai | OpenAlex, arXiv, Crossref (+Semantic Scholar, dblp) | SR > top-venue paper > journal/conference > preprint |
| physics | INSPIRE-HEP, arXiv, OpenAlex (+NASA ADS) | review > refereed journal > conference > preprint |
| chemistry | PubChem facts, Europe PMC, OpenAlex | datasets/SR > journal > preprint |
| economics | OpenAlex, Crossref, World Bank data | SR > official statistics > journal > working paper |
| law_pl_eu | Sejm ELI (ISAP), SAOS judgments, OpenAlex | legislation > case law > doctrine |
| psychology | OpenAlex, PubMed, Europe PMC | SR/MA > (pre-registered) RCT > longitudinal > cross-sectional |
| climate | OpenAlex, Crossref, NTRS | IPCC-type assessments > SR > journal > reports |
| general | OpenAlex, Crossref, Europe PMC | fallback |

Users can add packs as TOML in `~/.config/factual-research/packs/` or `<project>/packs/`.

## plan.toml tips

- `queries` are short keyword strings (no full questions); one per angle.
- `connector_queries.pubmed`: use MeSH + `[tiab]` + `[pt]` filters.
- `connector_queries.sejm_eli`: 1–2 words from act titles (e.g. `'ochronie konkurencji'`).
- `connector_queries.worldbank`: `INDICATOR@PL;DE:2015-2024` (free text lists indicator codes).
- `connector_queries.pubchem`: a compound name.
- Search adds an extra high-evidence query for PubMed (SR/MA/RCT/guideline filter) unless `--no-tiers`.

## Screening

- `screen list [--status new,maybe] [--sort relevance|weight|year] [--limit 25 --offset 0] [--brief] [--json]`
- `screen include|exclude|maybe KEYS... --reason "..." [--stage ta|ft]` (exclusions need a reason for PRISMA).
- `screen import decisions.csv` (columns key,decision,reason[,stage]).
- Records auto-excluded by rules: retracted, before `since`, wrong language, non-research items,
  preprints whose published version is in the library.
- Flags shown in lists: RETRACTED, expression of concern, corrected, preprint, not human / in vitro,
  industry funding/COI, registration only, low-visibility venue.

## Reading sources

- `show KEY` metadata, design (and why), weight, flags, funding/COI, notices.
- `show KEY --text [--loc "p. 4"|Results|"table 2"] [--grep word]`: the exact text used for verification.
- `find "query" [--keys a,b] [--everything]`: best passages with their location; copy quotes from here.
- Locations: PDF page `p. N` (PDF page index, not the printed page), `sec. <title>` for PMC XML,
  `table N`, `figure N`, `abstract`.
- Paywalled: save the PDF as `sources/<key>.pdf`, then `fetch --retry`. `add file.pdf` creates a record
  from a PDF (reads the DOI on page 1).

## Evidence matrix

```
factual-research claim add "Vitamin D reduces ARI risk" --sq SQ1 --id C1
factual-research evidence add C1 martineau2017vitamin --stance for --quote "reduced the risk of acute respiratory tract infection among all participants" --effect "OR 0.88 (95% CI 0.81–0.96)"
factual-research evidence import evidence.json     # [{claim, key, stance, quote, loc?, effect?, note?, claim_text?}]
factual-research matrix [--json]
```
Quotes shorter than 6 words are rejected as proof. Numbers in `--effect` must occur in the quote.

## Tables

- Extraction (evidence table): `extract template [--keys] --out f.json` → fill → `extract import f.json`;
  single cell: `extract set KEY FIELD "value" --quote "..." [--loc]`; `extract check`.
  Write `NR` for not reported. Fields come from the pack (PICO for medicine, method/result for engineering…).
- Built-in: `table sources | extraction [--fields a,b] | sof | matrix | forest`.
- Custom: `tables/<name>.json`
  ```json
  {"title": "Dosing and effect", "columns": ["Study", "Dose", "Effect"],
   "rows": [{"key": "martineau2017vitamin",
             "cells": ["@martineau2017vitamin", "daily/weekly",
                       {"value": "OR 0.81 (0.72–0.91)", "quote": "adjusted odds ratio 0.81, 0.72 to 0.91"}]}],
   "notes": ["Not pooled."]}
  ```
  `"@key"` cells render as citations; numbers in plain cells are checked against the row's `key`.
- Formats: `--format md|csv|html|latex|xlsx|json --out file`.
- In drafts: `<!-- fr:table extraction design,n,effect -->`, `<!-- fr:table sof -->`,
  `<!-- fr:table matrix -->`, `<!-- fr:table sources -->`, `<!-- fr:table custom tables/x.json -->`,
  `<!-- fr:table forest -->`.

## Draft rules (what verify checks)

| check | rule |
|---|---|
| integrity | every `[@key]` exists, was screened in, is not retracted/excluded; preprints with a published version → cite the published one |
| quotes | anchor quotes (`:: "..."`) occur verbatim (≥90%) in the source, at the stated location |
| support | the sentence is backed by the cited source (lexical overlap with the best passage, or its anchor quote) |
| numbers | every significant number (decimals, %, values >10, not years) occurs in a cited source (rounding noted) |
| coverage | factual sentences and all numbers carry a citation (author conclusions under a "Conclusion"/"Wnioski" heading are exempt) |
| calibration | causal wording needs RCT/SR/guideline; no "proves/definitely/always"; animal/in-vitro results must say so |
| balance | if the matrix has evidence against a claim you cite, mention it |
| primary | a systematic review cited with numbers must be represented by its main pooled estimate (read from the results/findings part of its abstract), not only by subgroup, sensitivity or fixed-effects results |
| evidence table | with 3+ cited studies the draft needs the verified extraction table (`<!-- fr:table extraction -->`); coverage of half the cited studies gives full marks |
| scope | each sub-question is addressed |
| tables | placeholder tables and markdown tables trace to sources |

Score = weighted pass rates (support 30, numbers 20, coverage 20, quotes 10, calibration 10, balance/scope/primary 10,
evidence table 5)
minus 15 per integrity error. `pass` = score ≥ target and no errors.

## Output

- `render draft.md [--style ...] [--docx] [--out] [--open]`: in-text citations, tables, reference list,
  plus the finished article `<draft>.final.html` from the built-in template (title from the first `#`,
  `> lead` under it, score badge, contents, citation popovers with the verbatim anchor quote, references,
  strength of evidence, "how this was verified", light/dark, print to PDF). `--no-html`, `--no-evidence`.
- `export bibtex|ris|csl [--cited draft.md] [--out]`.
- `report [--draft draft.md] [--open]`: `out/report.html`.
- `prisma --out out/prisma.svg`, `lock` → `research.lock.json` (queries, dates, counts, decisions).
- `watch [--notify]` re-runs all searches without cache and lists new relevant papers;
  `watch --print-cron` prints a weekly cron line.
