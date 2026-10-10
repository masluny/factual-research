# factual-research

Research with your AI agent where the sources can't be made up. factual-research
searches the sources each field actually trusts (PubMed for vitamins, NASA
NTRS for composites, arXiv for LLMs, ISAP/SAOS for Polish law…), keeps a
PRISMA-style record of what was found and why things were excluded, and then
**verifies every citation, quote, number and table cell** in the text your
agent writes, before you ever read it.

Same idea as [write-as-me](https://github.com/masluny/write-as-me): the LLM you
already use (Claude Code, Codex, the Claude app via MCP) does the reading and
writing, and a small, deterministic Python tool does the measuring. You get a
0–100 score with ranked, fixable notes, and the agent loops until the score passes.

No API keys needed for the core sources. Everything is cached locally, and
nothing is uploaded anywhere.

```
question ──init──▶ plan.toml (sub-questions, PICO, native queries, domain packs)
          search ─▶ 17 connectors in parallel → dedupe (DOI/PMID/arXiv/title) → auto-screen
          screen ─▶ agent includes/excludes with reasons (→ PRISMA)
          enrich ─▶ retractions (Retraction Watch via Crossref), preprint→published,
                    study design, venue signals, funding/COI
          fetch  ─▶ open-access full text (PMC XML, publisher PDFs, arXiv, NTRS, SAOS, ISAP)
          matrix ─▶ claims × sources, stance + verbatim quote → consensus + GRADE-style certainty
          tables ─▶ evidence table / summary of findings / custom tables, every cell quote-verified
          draft  ─▶ [@key, p. 4 :: "exact source words"]
          verify ─▶ score + fixes  ⟲ until pass
          render ─▶ final text + tables + references (APA, Vancouver, IEEE, …, .docx, HTML article)
          report ─▶ HTML: PRISMA flow, findings, matrix, forest plot, verified text, sources
```

## Install

```bash
pip install -e ".[all]"        # core is stdlib-only; [all] = PyMuPDF, python-docx, openpyxl
factual-research doctor --online     # optional deps, API keys, live check of every source
factual-research install-skill       # Claude Code skill  (or: --codex for AGENTS.md)
```

Then in Claude Code: *"research whether vitamin D prevents respiratory infections, with factual-research"*.

## Quickstart (by hand)

```bash
factual-research init "Does vitamin D supplementation reduce acute respiratory infections?" --dir vitd --online
cd vitd && $EDITOR plan.toml          # sub-questions, PubMed MeSH query, criteria
factual-research search
factual-research screen list                 # then: screen include K1 K2 --reason "..." / exclude K3 --reason "..."
factual-research enrich && factual-research fetch
factual-research find "bolus doses protective effect"
factual-research claim add "Vitamin D reduces ARI risk" --sq SQ1
factual-research evidence add C1 martineau2017vitamin --stance for --quote "reduced the risk of acute respiratory tract infection among all participants"
factual-research matrix
factual-research extract template --out extraction.json   # fill, then: extract import extraction.json
factual-research verify draft.md
factual-research render draft.md --docx && factual-research report --draft draft.md --open
```

`factual-research status` always prints the next step. A complete worked example
(plan, evidence, tables, draft, rendered text, `.docx`, report, lock file) is in
[`examples/vitamin-d/`](examples/vitamin-d/).

## Domain packs: where each field looks for truth

| pack | sources | evidence hierarchy |
|---|---|---|
| `medicine` | PubMed, Europe PMC, OpenAlex, ClinicalTrials.gov, Crossref | guideline ≈ SR/MA > RCT > cohort > case-control > case report > animal > in vitro |
| `engineering` | OpenAlex, Crossref, NASA NTRS, arXiv (+IEEE, CORE) | standards > SR > journal > tech report > conference > preprint |
| `cs_ai` | OpenAlex, arXiv, Crossref (+Semantic Scholar, dblp) | SR > top-venue papers (NeurIPS, ACL, …) > journal/conference > preprint |
| `physics` | INSPIRE-HEP, arXiv, OpenAlex (+NASA ADS) | reviews > refereed > preprint |
| `chemistry` | PubChem facts, Europe PMC, OpenAlex | datasets/SR > journal |
| `economics` | OpenAlex, Crossref, World Bank (+GUS BDL) | SR > official statistics > journal > working paper |
| `law_pl_eu` | Sejm ELI (ISAP), SAOS judgments, OpenAlex | legislation > case law > doctrine |
| `psychology` | OpenAlex, PubMed, Europe PMC | SR/MA > RCT > longitudinal > cross-sectional (replication-aware) |
| `climate` | OpenAlex, Crossref, NTRS | IPCC-type assessments > SR > journal |
| `general` | OpenAlex, Crossref, Europe PMC | fallback |

Packs are TOML files (sources, keyword detection EN+PL, evidence weights,
GRADE start levels, which designs may carry causal wording, and the columns of
the evidence table). You can add your own in `~/.config/factual-research/packs/`.
Questions that span fields use several packs at once.

## What `verify` checks

| check | catches |
|---|---|
| integrity | citations of sources that aren't in the library (fabricated), retracted, excluded or unscreened; preprints that now have a peer-reviewed version |
| quotes | anchor quotes that aren't verbatim in the source or sit at a different page/section |
| support | sentences whose claim isn't in the cited source (with the closest passage as the fix) |
| numbers | any decimal, %, or count that doesn't occur in the cited source (EN and PL notation, rounding detected) |
| coverage | factual sentences and numbers with no citation |
| calibration | causal wording backed only by observational evidence, "proves/definitely/always", mouse studies stated as human facts |
| balance | contrary evidence in your matrix that the text never mentions |
| scope | sub-questions of the plan that the text never answers |
| tables | table cells/rows whose numbers aren't in their source |
| primary | a systematic review cited only through subgroup, sensitivity or fixed-effects numbers, without its main pooled estimate |
| evidence table | a draft citing 3+ studies without the cell-by-cell verified evidence table |

Anchor quotes (`[@key, p. 4 :: "exact words"]`) make verification work across
languages: you can write in Polish about English papers, and render strips them.
`verify --style-scorer <write-as-me score.py>` adds a style score for your own voice.

## Tables

- Evidence table: `extract template`, fill value + verbatim quote per cell,
  then `extract import`. Columns come from the pack (PICO for medicine,
  method/result for engineering, task/dataset/metric for AI…).
- Summary of findings (consensus + certainty per claim), claim × source
  matrix, sources table, and a forest plot of the extracted effect sizes.
- Custom tables from a small JSON spec. Every sourced cell is checked against
  its paper.
- Output as md, csv, html, LaTeX (booktabs), xlsx (quotes as cell comments) or
  json. They can also go straight into drafts with
  `<!-- fr:table sof -->`-style placeholders.

## Outputs

`render` (APA, Harvard, Chicago, Vancouver, IEEE, ACS; md, docx and a standalone HTML article:
serif reading layout, verification badge, contents, citation popovers showing the verbatim source quote,
references, strength of evidence, light/dark, print to PDF; no external requests) ·
`export bibtex|ris|csl|obsidian` · `report` (single-file HTML) · `prisma --out flow.svg` ·
`research.lock.json` (every query, date, count and screening decision, for reproducibility) ·
`watch [--notify]` replays all searches and tells you about new papers (`--print-cron` for weekly runs).

## Agents

- **Claude Code**: `factual-research install-skill` (SKILL.md + WORKFLOW.md in `~/.claude/skills/factual-research`).
- **Codex / other agents**: `factual-research install-skill --codex` appends instructions to `AGENTS.md`.
- **Claude desktop app / any MCP client**: `factual-research --project <dir> mcp` runs a stdio MCP server
  (search, screen, find, evidence, extract, table, verify, render, report).

## API keys (all optional)

`FACTUAL_RESEARCH_MAILTO` (polite pools of OpenAlex/Crossref), `NCBI_API_KEY` (PubMed 10 req/s),
`OPENALEX_API_KEY`, `S2_API_KEY` (Semantic Scholar is rate-limited without one), `ADS_API_TOKEN`,
`IEEE_API_KEY`, `CORE_API_KEY`.

## Limits, on purpose

- Full text only comes from legal open-access sources. Paywalled papers stay
  abstract-only until you put the PDF in `sources/<key>.pdf`. Sites with bot
  checks (e.g. dblp at times) are skipped, not circumvented.
- The support check is lexical (plus anchor quotes). `--semantic` adds
  multilingual embeddings via `sentence-transformers` (downloads a model on
  first use).
- Consensus and certainty are transparent heuristics (weights and downgrades
  are printed), not a substitute for a formal GRADE assessment or for reading
  the key papers yourself.
- It's for writing well-sourced text, not for passing off AI-written work
  where that isn't allowed.

## Layout

| path | what |
|---|---|
| `factual_research/connectors/` | one module per source (PubMed, Europe PMC, OpenAlex, Crossref, Semantic Scholar, arXiv, ClinicalTrials.gov, dblp, INSPIRE, NASA ADS, NTRS, IEEE, CORE, Sejm ELI, SAOS, PubChem, World Bank, GUS BDL) |
| `factual_research/packs/*.toml` | domain packs |
| `factual_research/search.py` | parallel search, auto-screening, enrichment, snowballing, watch, lock file |
| `factual_research/fulltext.py` | OA full text, JATS/PDF parsing, reference-list and watermark stripping, funding/COI |
| `factual_research/verify.py` | the scorer |
| `factual_research/tables.py` | extraction, built-in and custom tables, forest plot, renderers |
| `factual_research/evidence.py` | weights, consensus, GRADE-style certainty, flags |
| `factual_research/export.py` | citation styles, render, BibTeX/RIS/CSL/Obsidian, docx |
| `factual_research/article.py`, `templates/article.html` | finished text as an HTML article (one fixed template) |
| `factual_research/report.py`, `prisma.py` | HTML report, PRISMA flow |
| `factual_research/mcp_server.py` | MCP server |
| `factual_research/skill/` | SKILL.md, WORKFLOW.md, AGENTS.md |
| `tests/` | offline tests (`python3 -m unittest discover tests`) |

MIT licensed.
