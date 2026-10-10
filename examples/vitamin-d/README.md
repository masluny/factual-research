# Worked example: vitamin D and acute respiratory infections

Produced end to end with factual-research on 2026-10-06 (an agent following `factual_research/skill/SKILL.md`):

- `plan.toml`: question, PICO, 2 sub-questions, PubMed MeSH queries
- `research.lock.json`: every query (12 across 5 sources), counts and screening decision
- `evidence.json` / `extraction.json`: claim evidence and evidence-table cells, each with a verbatim quote
- `tables/dosing.json`: a custom table spec
- `draft.md`: the verified draft (score 96/100)
- `draft.final.md` / `draft.final.docx`: rendered text (Vancouver)
- `out/report.html`: report with PRISMA flow, findings, matrix, forest plot, tables, verified text
- `out/prisma.svg`, `out/forest.svg`

The first draft scored 65/100. The scorer caught a fabricated source, an
uncited statistic, overclaiming ("definitely") and missing contrary evidence.
It also flagged that the 2025 meta-analysis did **not** confirm the benefit
(OR 0.94, 95% CI 0.88–1.00), contrary to what the first draft said.
