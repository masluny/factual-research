## Research with factual-research

When asked to research a topic, write anything that cites studies, build evidence tables, or check
citations, use the `factual-research` CLI (installed in this environment; `factual-research --help`).

1. `factual-research init "<question>" --dir research/<slug> --online`; edit `plan.toml` (sub-questions,
   keyword queries, PubMed/ELI native queries, criteria).
2. `factual-research search` → `factual-research screen list` → `screen include/exclude KEYS --reason ...`
   (include syntheses, key primary studies and credible contrary evidence).
3. `factual-research enrich` → `factual-research fetch`.
4. Claims + evidence with verbatim quotes: `claim add`, `evidence add C1 KEY --stance for|against --quote "..."`,
   `matrix`. Evidence table: `extract template` → fill → `extract import`.
5. Write `draft.md` citing `[@key, p. N :: "exact source words"]`; insert tables with
   `<!-- fr:table extraction -->` / `<!-- fr:table sof -->`; no reference list.
6. `factual-research verify draft.md --json`; fix issues until `pass` (max 5 rounds).
7. `factual-research render draft.md` (writes `draft.final.md` and the finished article `draft.final.html`
   from the built-in template; do not design your own page) and `factual-research report --draft draft.md`;
   report the verify score.

Never cite a key that is not in the project, never invent numbers, never remove true caveats to raise the score.
Full rules: `factual_research/skill/WORKFLOW.md` in the factual-research package.
