---
name: factual-research
description: Evidence-based research with verified citations. Use whenever the user asks to research a topic, do a literature review, find studies/papers/evidence, answer a question "with sources", write a report/essay/article that cites research, build an evidence table or summary of findings, or check/verify the citations, quotes and numbers in an existing text. Searches the sources each field trusts (PubMed for health, IEEE/NTRS for engineering, arXiv for AI, ISAP/SAOS for Polish law...), then verifies every claim, quote and number against the source text before delivering.
---

# factual-research: research that cannot hallucinate its sources

You (the model) do the reading, judging and writing. The `factual-research` CLI does
everything that must be objective: searching trusted sources, deduplication,
retraction checks, full-text retrieval, and **verification** of every quote,
number and citation in what you write. Never cite anything that is not in the
project library; never state a number you did not copy from a source.

Check the CLI is installed: `factual-research --version` (if missing:
`pip install -e <path-to-factual-research>` or `pip install factual-research`).
Full command reference and rules: `WORKFLOW.md` next to this file; read it
before the first run in a session.

## The loop

Do every step; keep working until `verify` passes, and only stop to ask when you
cannot go on without the user. Never report a score you did not get from a
`verify` run in this session; quote it from that output.

1. **Plan.** `factual-research init "<question>" --dir <folder> --online`, then `cd` there.
   Edit `plan.toml`: split the question into 2–4 sub-questions, write short
   keyword `queries`, add native `connector_queries` where it helps (PubMed
   MeSH/[pt] syntax, Sejm ELI short title words), set inclusion/exclusion
   criteria (PICO for health). Check the detected packs; add a second pack if
   the question spans fields.
2. **Search.** `factual-research search` (add `--all` for optional sources). Then
   `factual-research screen list` and decide every record:
   `factual-research screen include K1 K2 --reason "..."` /
   `factual-research screen exclude K3 --reason "wrong population"`.
   Prefer syntheses (guidelines, systematic reviews) plus the key primary
   studies, and **deliberately include credible evidence against** the
   expected answer. Optionally `factual-research snowball` for citation chasing.
3. **Harden.** `factual-research enrich` (retractions, preprint→published,
   designs) then `factual-research fetch` (open-access full texts). Tell the user which
   key papers are paywalled; they can drop PDFs into `sources/<key>.pdf`.
4. **Evidence matrix.** For each sub-question write claims
   (`factual-research claim add "..." --sq SQ1`) and attach evidence with verbatim
   quotes found via `factual-research find "..."` / `factual-research show KEY --text --grep ...`:
   `factual-research evidence add C1 KEY --stance for|against|mixed --quote "exact words" --effect "OR 0.88 (0.81–0.96)"`.
   Every quote is verified on write; fix any ⚠ immediately.
   `factual-research matrix` gives consensus + GRADE-style certainty per claim.
5. **Evidence table (required).** `factual-research extract template --out extraction.json`,
   fill value + verbatim quote (+ loc) per cell for every study you will cite,
   then `factual-research extract import extraction.json` and fix any ⚠ cell.
   The draft's evidence table must be the `<!-- fr:table extraction ... -->`
   placeholder: a hand-made markdown table is not checked cell by cell, and
   `verify` scores its absence as 0 on "evidence table".
   For any other table write a spec in `tables/<name>.json` (see WORKFLOW.md)
   and check it with `factual-research table custom --spec tables/<name>.json`.
6. **Draft** `draft.md` (markdown) in the user's language:
   - cite as `[@key]`, `[@key, p. 4]`, `[@key, sec. Results]`;
   - add an **anchor quote** for every key claim: `[@key, p. 4 :: "exact source words"]`
     (mandatory when the draft language differs from the source language);
   - copy numbers exactly; report effect sizes with CIs; mention contrary evidence;
   - when you cite numbers from a systematic review, **state its main pooled
     estimate first** (the overall result in its abstract), then any subgroup,
     sensitivity or fixed-effects result; `verify` warns when the main one is missing;
   - calibrate wording to the certainty from `factual-research matrix`
     ("is associated with" for observational evidence);
   - insert tables with placeholders: `<!-- fr:table extraction design,n,effect -->`,
     `<!-- fr:table sof -->`, `<!-- fr:table custom tables/x.json -->`, `<!-- fr:table forest -->`;
   - no reference list (it is generated).
7. **Verify.** `factual-research verify draft.md --json`. Fix the ranked issues
   (errors first), re-run. Stop when `pass` is true (default target 80, max
   5 passes); never "fix" a finding by deleting a true caveat.
8. **Deliver.** `factual-research render draft.md` (→ `draft.final.md` plus the
   finished article `draft.final.html`, `--docx` for Word,
   `--style apa|vancouver|ieee|harvard|chicago|acs`). The HTML uses the built-in
   template: never design your own page for the text. Start the draft with a
   `# Title`, optionally one `> lead sentence` line under it, and use a
   `## Wnioski` / `## Conclusions` section (it is highlighted). Write claim texts
   in the draft language (they appear in the article). Then
   `factual-research report --draft draft.md` (HTML: PRISMA, findings, tables,
   verified text). Give the user the final text plus one line:
   `Verified: <score>/100, <n> sources (<m> full text), certainty: <per main claim>`,
   and name any claim resting on abstracts only.

## Checking an existing text

When the user only wants citations checked: `init` a project, `add --doi` /
`add file.pdf` each cited source (or search for them), rewrite their citations
as `[@key]`, run `verify`, and report the findings without rewriting their prose
unless asked.

## Style

To also match the user's own voice, pass `--style-scorer <path to write-as-me score.py
or skill folder>` to `verify`; it reports a style score next to the evidence score.
