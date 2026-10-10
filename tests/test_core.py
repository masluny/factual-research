"""Offline tests: run with `python3 -m unittest discover tests`."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["FACTUAL_RESEARCH_CACHE"] = tempfile.mkdtemp()

from factual_research import textutil as T  # noqa: E402
from factual_research.citations import parse_cites, quote_location, strip_cites  # noqa: E402
from factual_research.model import Work, infer_design, make_key, norm_doi, norm_id  # noqa: E402


class TextUtil(unittest.TestCase):
    def test_numbers_pl_en(self):
        vals = lambda s: [sorted(n.values) for n in T.extract_numbers(s)]  # noqa: E731
        self.assertEqual(vals("OR 0,88; 95% CI 0,81–0,96"), [[0.88], [95.0], [0.81], [0.96]])
        self.assertIn(10933.0, vals("10 933 participants")[0])
        self.assertIn(31521.0, vals("31,521 participants")[0])
        self.assertEqual(vals("0·92")[0], [0.92])            # Lancet decimal point

    def test_number_matching_and_rounding(self):
        idx = T.number_index("rate was 8.34% (n=1,515)")
        vs = {v for v, _ in idx}
        n = T.extract_numbers("8.3%")[0]
        self.assertEqual(T.number_in(n, vs, idx), "rounded")
        self.assertEqual(T.number_in(T.extract_numbers("1515")[0], vs, idx), "exact")
        self.assertEqual(T.number_in(T.extract_numbers("1616")[0], vs, idx), "missing")

    def test_significance(self):
        sig = [T.is_significant(n) for n in T.extract_numbers("In 2020, 3 trials and 45 people, 0.5 mg, 12%")]
        self.assertEqual(sig, [False, False, True, True, True])

    def test_sentences(self):
        s = T.split_sentences("Smith et al. found an effect (p. 4) [@a]. Next one, e.g. this. Value 0.5 was seen. Done.")
        self.assertEqual(len(s), 4)
        self.assertTrue(s[0].endswith("[@a]."))

    def test_find_quote(self):
        src = "Results: Vitamin D supplementation reduced the risk of acute respiratory tract infec-\ntion among all participants."
        self.assertEqual(T.find_quote("reduced the risk of acute respiratory tract infection", src)[0], 1.0)
        self.assertGreater(T.find_quote("reduced the risk of acute respiratory infection among all participants", src)[0], 0.88)
        self.assertLess(T.find_quote("increased mortality in elderly patients", src)[0], 0.6)
        self.assertEqual(T.find_quote("Vitamin D supplementation ... among all participants", src)[0], 1.0)

    def test_overlap_and_lang(self):
        self.assertGreater(T.overlap("vitamin D reduced infections", "Vitamin D supplementation reduced the infection risk"), 0.6)
        self.assertEqual(T.detect_lang("Suplementacja witaminy D zmniejsza ryzyko infekcji u dzieci i dorosłych"), "pl")


class Citations(unittest.TestCase):
    def test_parse(self):
        cs = parse_cites('Text [@a, p. 4 :: "exact; words"; @b] and [@c].')
        self.assertEqual([(c.key, c.locator, c.quote) for c in cs],
                         [("a", "p. 4", "exact; words"), ("b", "", ""), ("c", "", "")])
        self.assertEqual(strip_cites("Text [@a] here."), "Text here.")

    def test_location(self):
        pages = [{"loc": "p. 1", "text": "alpha beta gamma delta epsilon zeta"},
                 {"loc": "p. 2", "text": "the quick brown fox jumps over"}]
        self.assertEqual(quote_location("quick brown fox jumps over", "p. 2", pages)[0], True)
        self.assertEqual(quote_location("quick brown fox jumps over", "p. 1", pages), (False, "p. 2"))
        self.assertIsNone(quote_location("quick brown fox jumps over", "sec. Results", pages)[0])


class Model(unittest.TestCase):
    def test_ids(self):
        self.assertEqual(norm_doi("https://doi.org/10.1136/BMJ.i6583"), "10.1136/bmj.i6583")
        self.assertEqual(norm_id("arxiv", "https://arxiv.org/abs/2411.18583v2"), "2411.18583")
        self.assertEqual(norm_id("pmcid", "5310969"), "PMC5310969")

    def test_design(self):
        self.assertEqual(infer_design(Work(pub_types=["Journal Article", "Meta-Analysis"]))[0], "sr_ma")
        self.assertEqual(infer_design(Work(title="A randomized, double-blind trial of X"))[0], "rct")
        self.assertEqual(infer_design(Work(title="X in mice", mesh=["Animals", "Mice"]))[0], "animal")
        self.assertEqual(infer_design(Work(title="Randomized trial", kind="trial_registration"))[0], "trial_registration")
        self.assertEqual(infer_design(Work(title="Clinical trial", pub_types=["Clinical Trial"], mesh=["Animals"]))[0], "animal")

    def test_keys(self):
        w = Work(title="Vitamin D supplementation to prevent infections", authors=["Martineau, Adrian"], year=2017)
        self.assertEqual(make_key(w, set()), "martineau2017vitamin")
        self.assertEqual(make_key(w, {"martineau2017vitamin"}), "martineau2017vitamina")

    def test_display_names(self):
        from factual_research.model import display_name
        self.assertEqual(display_name("Julio J Secades"), "Secades, Julio J")
        self.assertEqual(display_name("Ludwig van Beethoven"), "van Beethoven, Ludwig")
        self.assertEqual(display_name("Martin Luther King Jr."), "King, Martin Luther")
        for kept in ("Xu C", "Martineau, Adrian R", "World Health Organization", "Plato"):
            self.assertEqual(display_name(kept), kept)

    def test_merge_prefers_structured_authors(self):
        from factual_research.model import merge
        raw = Work(authors=["Xu Chen", "Siyuan Bi"])                     # stored before names were normalised
        self.assertEqual(merge(raw, Work(authors=["Xu, Chen", "Bi, Siyuan"])).authors, ["Xu, Chen", "Bi, Siyuan"])
        # a longer display list keeps every author, but takes the structured names it can match
        oa = Work(authors=["Chen, Xu", "Bi, Siyuan", "Luo, Lin"], extra={"authors_from": "display"})
        m = merge(Work(authors=["Xu, Chen", "Bi, S."]), oa)
        self.assertEqual(m.authors, ["Xu, Chen", "Bi, S.", "Luo, Lin"])
        self.assertEqual(m.extra.get("authors_from"), "display")


def make_project(tmp: Path):
    from factual_research.store import Store
    (tmp / "plan.toml").write_text(textwrap.dedent('''
        [question]
        text = "Does vitamin D reduce respiratory infections?"
        lang = "en"
        packs = ["medicine"]
        since = 2000
        [[subq]]
        id = "SQ1"
        text = "Does vitamin D reduce respiratory infections?"
        queries = ["vitamin D respiratory infection"]
    '''), encoding="utf-8")
    st = Store(tmp)
    a = Work(title="Vitamin D supplementation to prevent acute respiratory infections: meta-analysis",
             authors=["Martineau, Adrian R"], year=2017, venue="BMJ", doi="10.1136/bmj.i6583",
             pub_types=["Meta-Analysis"], abstract="Vitamin D supplementation reduced the risk of acute respiratory "
             "tract infection among all participants (adjusted odds ratio 0.88, 95% confidence interval 0.81 to 0.96). "
             "10 933 participants were analysed.")
    b = Work(title="Vitamin D in mice lungs", authors=["Doe, J"], year=2019, venue="Lab J", mesh=["Animals", "Mice"],
             abstract="Vitamin D reduced viral load in murine lungs by 45%.")
    c = Work(title="Retracted vitamin paper", authors=["Fake, F"], year=2020, venue="X", is_retracted=True,
             abstract="Vitamin D cured everything.")
    keys = []
    for w in (a, b, c):
        k, _ = st.upsert(w)
        st.index_abstract(k, st.get(k))
        keys.append(k)
    st.db.commit()
    st.decide(keys[0], "ta", "include", "PICO")
    st.decide(keys[1], "ta", "include", "mechanism")
    st.save_fulltext(keys[0], "pdf", "x.pdf", [
        {"loc": "p. 1", "text": "Vitamin D supplementation reduced the risk of acute respiratory tract infection among "
                                "all participants (adjusted odds ratio 0.88, 95% confidence interval 0.81 to 0.96)."},
        {"loc": "p. 2", "text": "Protective effects were seen with daily or weekly dosing (adjusted odds ratio 0.81, "
                                "0.72 to 0.91) but not with bolus doses (0.97, 0.86 to 1.10)."}], {})
    return st, keys


class VerifyPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.st, self.keys = make_project(self.tmp)
        from factual_research.project import Plan
        self.plan = Plan(self.tmp)

    def run_verify(self, text):
        from factual_research.verify import verify
        p = self.tmp / "draft.md"
        p.write_text(text, encoding="utf-8")
        return verify(self.st, self.plan, p)

    def test_good_draft_passes(self):
        k = self.keys[0]
        r = self.run_verify(f"# T\n\nVitamin D supplementation reduced the risk of acute respiratory infection "
                            f"(OR 0.88, 95% CI 0.81 to 0.96) [@{k}, p. 1 :: \"reduced the risk of acute respiratory "
                            f"tract infection among all participants\"]. Daily or weekly dosing was protective "
                            f"(OR 0.81) [@{k}, p. 2].\n")
        self.assertTrue(r["pass"], r["issues"])
        self.assertGreaterEqual(r["score"], 90)

    def test_errors_are_caught(self):
        k0, k1, k2 = self.keys
        r = self.run_verify(f"Vitamin D reduced infections by 0.77 [@{k0}]. It is proven forever [@nobody2020x]. "
                            f"Vitamin D reduces viral load [@{k1}]. Retracted claim [@{k2}]. "
                            f"Around 40% of people are deficient.\n")
        checks = {(i["check"], i["severity"]) for i in r["issues"]}
        self.assertIn(("integrity", "error"), checks)        # unknown + retracted (+ unscreened)
        self.assertIn(("numbers", "error"), checks)          # 0.77 not in source, 40% uncited
        self.assertIn(("calibration", "warn"), checks)       # animal study stated generally
        self.assertFalse(r["pass"])
        self.assertLess(r["score"], 60)

    def test_quote_and_tables(self):
        from factual_research.tables import check_cell, custom_table
        k = self.keys[0]
        sc, pr = check_cell(self.st, k, "OR 0.88", "adjusted odds ratio 0.88, 95% confidence interval 0.81 to 0.96")
        self.assertEqual(sc, 1.0, pr)
        sc, pr = check_cell(self.st, k, "OR 0.70", "adjusted odds ratio 0.70 in all groups of participants")
        self.assertLess(sc, 0.85)
        t, probs = custom_table(self.st, {"title": "t", "columns": ["S", "E"], "rows": [
            {"key": k, "cells": [f"@{k}", "OR 0.88 (0.81–0.96)"]}, {"key": k, "cells": [f"@{k}", "OR 0.55"]}]})
        self.assertEqual(len(probs), 1)

    def test_bracketed_quote(self):
        """Brackets inside an anchor quote ("odds ratio [OR] 1.56") must parse the
        same in verify and render (prokopidis2023creatine, secades2016citicoline)."""
        from factual_research.export import render_draft
        k = self.keys[0]
        self.st.save_fulltext(k, "pdf", "x.pdf", [{"loc": "p. 1", "text": (
            "Supplementation reduced the risk of acute respiratory tract infection (adjusted odds ratio [OR] 0.88, "
            "95% confidence interval [CI] 0.81 to 0.96) among all participants.")}], {})
        quote = "reduced the risk of acute respiratory tract infection (adjusted odds ratio [OR] 0.88, 95% confidence interval [CI] 0.81 to 0.96)"
        draft = f'# T\n\nVitamin D reduced infections (OR 0.88, 95% CI 0.81 to 0.96) [@{k}, p. 1 :: "{quote}"; @{k}].\n'
        self.assertEqual([(c.key, c.locator, c.quote) for c in parse_cites(draft)], [(k, "p. 1", quote), (k, "", "")])
        r = self.run_verify(draft)
        self.assertTrue(r["pass"], r["issues"])
        self.assertEqual(r["metrics"]["quotes_ok"], 1)
        out, order, _ = render_draft(self.st, self.plan, draft, "apa")
        self.assertIn("(OR 0.88, 95% CI 0.81 to 0.96) (Martineau, 2017, p. 1; Martineau, 2017).", out)
        self.assertNotIn("[@", out)
        self.assertEqual(order, [k])
        # what render cannot convert, verify must not pass
        r = self.run_verify(f'# T\n\nVitamin D reduced infections [@{k} :: "adjusted odds ratio [OR 0.88"].\n')
        self.assertIn(("integrity", "error"), {(i["check"], i["severity"]) for i in r["issues"]})
        self.assertFalse(r["pass"])

    def test_render_tables_pl(self):
        from factual_research.export import render_draft
        from factual_research.tables import extract_set
        k = self.keys[0]
        extract_set(self.st, k, "effect", "OR 0.88 (95% CI 0.81 to 0.96)",
                    "adjusted odds ratio 0.88, 95% confidence interval 0.81 to 0.96", "p. 1")
        draft = "Tekst.\n\n<!-- fr:table extraction effect -->\n"
        out, _, _ = render_draft(self.st, self.plan, draft, "apa", lang="pl")
        self.assertIn("**Charakterystyka i wyniki włączonych badań**", out)
        self.assertIn("| Badanie | Efekt (95% CI) |", out)
        self.assertIn("*Każda niepusta komórka jest poparta dosłownym cytatem", out)
        self.assertIn("## Bibliografia", out)
        out, _, _ = render_draft(self.st, self.plan, draft, "apa")
        self.assertIn("| Study | Effect (95% CI) |", out)

    def test_render_numbering(self):
        from factual_research.export import render_draft
        k0, k1 = self.keys[:2]
        out, order, probs = render_draft(self.st, self.plan, f"A [@{k1}]. B [@{k0}, p. 2]. C [@{k1}].", "vancouver")
        self.assertIn("A [1]. B [2, p. 2]. C [1].", out)
        self.assertEqual(order, [k1, k0])
        out, _, _ = render_draft(self.st, self.plan, f"A [@{k0}].", "apa")
        self.assertIn("(Martineau, 2017)", out)

    def test_article_html(self):
        from factual_research.article import build_article
        k0, k1 = self.keys[:2]
        self.st.save_fulltext(k0, "pdf", "x.pdf", [{"loc": "p. 1", "text": "reduced the risk of acute respiratory tract infection"}], {})
        draft = (f"# Witamina D i infekcje\n\n> Krótki wstęp.\n\n## Wyniki\n\nSuplementacja wiąże się z mniejszym ryzykiem "
                 f'infekcji [@{k0}, p. 1 :: "reduced the risk of acute respiratory tract infection"; @{k1}].\n\n'
                 "## Wnioski\n\nTo jest wniosek i podsumowanie.\n")
        vr = {"score": 92.0, "pass": True, "components": {"support": 90.0, "numbers": 100.0}}
        html, order, probs = build_article(self.st, self.plan, draft, "apa", verify_report=vr)
        self.assertEqual(order, [k0, k1])
        self.assertEqual(probs, [])
        self.assertIn('<html lang="pl">', html)                                   # detected from the text
        self.assertIn("Witamina D i infekcje", html)                           # Polish one-letter words
        self.assertIn('<p class="dek">Krótki wstęp.</p>', html)
        self.assertIn('data-quote="reduced the risk of acute respiratory tract infection"', html)
        self.assertIn('data-loc="s. 1"', html)
        self.assertIn(f'id="ref-{k0}"', html)
        self.assertIn('<section class="conclusion">', html)
        self.assertIn("92/100", html)
        self.assertIn("Bibliografia", html)
        self.assertNotIn("{{", html)
        self.assertNotIn("[@", html)
        html, _, _ = build_article(self.st, self.plan, f"Plain text [@{k0}].", "vancouver", lang="en")
        self.assertIn('<span class="cites num">[<a class="cite"', html)
        self.assertIn("References", html)

    def test_matrix(self):
        from factual_research.evidence import summarize_claim
        k0, k1 = self.keys[:2]
        self.st.add_claim("C1", "Vitamin D reduces ARI")
        self.st.add_evidence("C1", k0, "for", "q", "", "", "", 1.0)
        self.st.add_evidence("C1", k1, "against", "q", "", "", "", 1.0)
        works = {k: w for k, _, w in self.st.works()}
        s = summarize_claim(self.st.claims()[0], self.st.evidence("C1"), works, self.plan.combined())
        self.assertGreater(s.consensus, 0.8)        # SR/MA outweighs an animal study
        self.assertEqual(s.n, 2)

    def test_matrix_review_appraisal(self):
        """A review that rates its own evidence low caps the claim's certainty
        (bonvicini2023citicoline, eckert2025creatine)."""
        from factual_research.evidence import reported_certainty, summarize_claim
        self.assertEqual(reported_certainty("SMD -0.34 (95% CI -0.68, -0.00; GRADE: very low quality of evidence)"), "very low")
        self.assertEqual(reported_certainty("the available evidence is of poor quality and likely to be biased"), "low")
        self.assertIsNone(reported_certainty("Certainty of evidence was evaluated using GRADE. Studies at high risk of "
                                             "bias were excluded."))
        sr, _ = self.st.upsert(Work(title="Citicoline and cognition: a systematic review and meta-analysis",
                                    authors=["Bonvicini, M"], year=2023, venue="Nutrients", pub_types=["Meta-Analysis"],
                                    abstract="Citicoline improved cognitive status."))
        self.st.add_claim("C1", "Citicoline improves cognition")
        self.st.add_evidence("C1", self.keys[0], "for", "q", "", "", "", 1.0)
        self.st.add_evidence("C1", sr, "for", "Citicoline improved cognitive status.", "", "", "", 1.0)
        works = {k: w for k, _, w in self.st.works()}
        s = summarize_claim(self.st.claims()[0], self.st.evidence("C1"), works, self.plan.combined())
        # two syntheses, neither states its certainty: capped at moderate
        self.assertEqual(s.certainty, "moderate")
        self.st.add_evidence("C1", sr, "mixed", "Our results confirm the positive effects, but the available evidence "
                             "is of poor quality and likely to be biased.", "", "", "", 1.0)
        s = summarize_claim(self.st.claims()[0], self.st.evidence("C1"), works, self.plan.combined())
        self.assertEqual(s.certainty, "low")
        self.assertTrue(any(f"{sr}: low" in r for r in s.reasons), s.reasons)


class Compatibility(unittest.TestCase):
    def test_older_project_layout(self):
        """Older projects keep their .cg/ folder and `cg:table` placeholders."""
        from factual_research import env
        from factual_research.export import render_draft
        from factual_research.project import Plan, open_project
        from factual_research.store import find_root
        from factual_research.tables import extract_set
        new = Path(tempfile.mkdtemp())
        make_project(new)
        self.assertTrue((new / ".fr" / "db.sqlite").exists())
        old = Path(tempfile.mkdtemp())
        (old / ".cg").mkdir()
        st, keys = make_project(old)
        self.assertEqual(st.dir, old / ".cg")
        self.assertFalse((old / ".fr").exists())
        (old / "sub").mkdir()
        self.assertEqual(find_root(old / "sub"), old.resolve())
        self.assertEqual(open_project(str(old))[0].dir.name, ".cg")
        extract_set(st, keys[0], "effect", "OR 0.88 (95% CI 0.81 to 0.96)",
                    "adjusted odds ratio 0.88, 95% confidence interval 0.81 to 0.96", "p. 1")
        out, _, _ = render_draft(st, Plan(old), "A.\n\n<!-- cg:table extraction effect -->\n", "apa")
        self.assertIn("| Study | Effect (95% CI) |", out)
        os.environ["FACTUAL_RESEARCH_MAILTO"] = "new@example.org"
        try:
            self.assertEqual(env("MAILTO"), "new@example.org")
        finally:
            os.environ.pop("FACTUAL_RESEARCH_MAILTO", None)


class ReviewChecks(unittest.TestCase):
    """Fixes from the Haiku 5.5 test runs (2026-10-10)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.st, self.keys = make_project(self.tmp)
        from factual_research.project import Plan
        self.plan = Plan(self.tmp)

    def test_certainty_wording(self):
        from factual_research.evidence import certainty_near, reported_certainty
        self.assertEqual(reported_certainty("10 studies; low\u2010certainty evidence), but"), "low")      # Cochrane dash
        self.assertEqual(reported_certainty("The body of evidence was assessed as being of high quality"), "high")
        abstract = ("A may result in a slight reduction in visits (RR 0.95; low\u2010certainty evidence), but does "
                    "not reduce costs (moderate\u2010certainty evidence). B may not change deaths (very low\u2010certainty evidence).")
        self.assertEqual(reported_certainty(abstract), "very low")
        self.assertEqual(certainty_near("may result in a slight reduction in visits", abstract), "low")
        concl = abstract + " We found low\u2010certainty evidence that A may slightly reduce the proportion with visits."
        self.assertEqual(certainty_near("A may slightly reduce the proportion with visits", concl), "low")

    def test_mixed_review_is_inconsistency(self):
        from factual_research.evidence import summarize_claim
        k0 = self.keys[0]
        rev, _ = self.st.upsert(Work(title="Update: systematic review and meta-analysis of vitamin D", year=2025,
                                     authors=["Jolliffe, D"], venue="Lancet DE", pub_types=["Meta-Analysis"],
                                     abstract="Vitamin D did not significantly affect overall risk."))
        self.st.add_claim("C1", "Vitamin D reduces ARI")
        self.st.add_evidence("C1", k0, "for", "q", "", "", "", 1.0)
        self.st.add_evidence("C1", rev, "mixed", "did not significantly affect overall risk", "", "", "", 1.0)
        works = {k: w for k, _, w in self.st.works()}
        s = summarize_claim(self.st.claims()[0], self.st.evidence("C1"), works, self.plan.combined())
        self.assertNotEqual(s.certainty, "high")
        self.assertTrue(any("mixed or non-significant" in r for r in s.reasons), s.reasons)

    def test_main_estimate(self):
        from factual_research.evidence import main_estimate
        ab = ("Background: A 2021 meta-analysis found a protective effect (odds ratio [OR] 0.92, 95% CI 0.86 to 0.99). "
              "Findings: For the primary comparison the intervention did not significantly affect overall risk "
              "(OR 0.94, 95% CI 0.88 to 1.00, P=0.057). Daily dosing was protective (OR 0.84, 0.73 to 0.97).")
        self.assertEqual(main_estimate(ab)[1:4], (0.94, 0.88, 1.0))
        self.assertIsNone(main_estimate("We found OR 0.80 (0.70 to 0.90)."))      # no results label
        self.assertIsNone(main_estimate("Results: patients took 2 or 3 doses or 4.5 or 6.1 to 7.2 tablets."))

    def test_verify_flags_missing_main_result_and_table(self):
        from factual_research.verify import verify
        rev, _ = self.st.upsert(Work(title="Vitamin D for ARI: updated systematic review and meta-analysis", year=2025,
                                     authors=["Jolliffe, D"], venue="Lancet DE", pub_types=["Meta-Analysis"],
                                     abstract="Background: earlier review (OR 0.92, 95% CI 0.86 to 0.99). Findings: overall "
                                     "the intervention did not significantly affect risk (OR 0.94, 95% CI 0.88 to 1.00). "
                                     "A fixed effects model gave OR 0.96 (95% CI 0.93 to 1.00). Children aged 1-15 years "
                                     "benefited (OR 0.74, 95% CI 0.60 to 0.92)."))
        self.st.index_abstract(rev, self.st.get(rev))
        self.st.db.commit()
        self.st.decide(rev, "ta", "include", "SR")
        def run(text):
            p = self.tmp / "d.md"
            p.write_text(text, encoding="utf-8")
            return verify(self.st, self.plan, p)
        bad = run(f"A fixed effects model gave OR 0.96 (95% CI 0.93 to 1.00) [@{rev}]. Children aged 1-15 years "
                  f"benefited (OR 0.74, 95% CI 0.60 to 0.92) [@{rev}].\n")
        self.assertTrue(any(i["check"] == "primary" for i in bad["issues"]), bad["issues"])
        good = run(f"Overall the intervention did not significantly affect risk (OR 0.94, 95% CI 0.88 to 1.00) [@{rev}]. "
                   f"Children aged 1-15 years benefited (OR 0.74, 95% CI 0.60 to 0.92) [@{rev}].\n")
        self.assertFalse(any(i["check"] == "primary" for i in good["issues"]))
        k0, k1 = self.keys[:2]
        three = (f"Vitamin D supplementation reduced the risk of acute respiratory infection [@{k0}]. "
                 f"Vitamin D reduced viral load in murine lungs [@{k1}]. "
                 f"Overall the intervention did not significantly affect risk (OR 0.94, 95% CI 0.88 to 1.00) [@{rev}].\n")
        r = run(three)
        self.assertEqual(r["components"].get("evidence_table"), 0.0)
        self.assertTrue(any("No verified evidence table" in i["message"] for i in r["issues"]))

    def test_venue_flags(self):
        from factual_research.connectors.openalex import parse
        from factual_research.evidence import flags
        rec = {"id": "https://openalex.org/W9", "display_name": "Vitamin D for preventing ARI in children", "type": "review",
               "primary_location": {"source": {"display_name": "Europe PMC", "type": "repository", "is_in_doaj": False,
                                               "is_core": False}},
               "locations": [{"source": {"display_name": "Europe PMC", "type": "repository"}},
                             {"source": {"display_name": "Cochrane Database of Systematic Reviews", "type": "journal",
                                         "is_core": True, "listed_in": ["medline"]}}]}
        w = parse(rec)
        self.assertEqual(w.venue, "Cochrane Database of Systematic Reviews")
        self.assertNotIn("low-visibility venue", flags(w))
        w2 = Work(title="x", venue="Some Journal", design="journal_article", sources=["pubmed"], mesh=["Humans"],
                  venue_signals={"in_doaj": False, "core": False, "source_type": "journal"})
        self.assertNotIn("low-visibility venue", flags(w2))                  # MEDLINE-indexed

class Connectors(unittest.TestCase):
    def test_pubmed_parse(self):
        import xml.etree.ElementTree as ET
        from factual_research.connectors.pubmed import parse_article
        xml = """<PubmedArticle><MedlineCitation><PMID>1</PMID><Article><Journal><Title>BMJ</Title>
        <JournalIssue><Volume>356</Volume><PubDate><Year>2017</Year></PubDate></JournalIssue></Journal>
        <ArticleTitle>Vitamin D trial.</ArticleTitle><Abstract><AbstractText Label="RESULTS">It worked.</AbstractText></Abstract>
        <AuthorList><Author><LastName>Smith</LastName><ForeName>Ann</ForeName></Author></AuthorList>
        <PublicationTypeList><PublicationType>Randomized Controlled Trial</PublicationType></PublicationTypeList>
        <ELocationID EIdType="doi">10.1/x</ELocationID></Article>
        <CommentsCorrectionsList><CommentsCorrections RefType="RetractionIn"><RefSource>X</RefSource></CommentsCorrections></CommentsCorrectionsList>
        </MedlineCitation></PubmedArticle>"""
        w = parse_article(ET.fromstring(xml))
        self.assertEqual((w.title, w.year, w.doi, w.authors), ("Vitamin D trial", 2017, "10.1/x", ["Smith, Ann"]))
        self.assertTrue(w.is_retracted)
        self.assertEqual(infer_design(w)[0], "rct")

    def test_author_order_across_sources(self):
        """chen2024creatine (doi 10.3389/fnut.2024.1424972): OpenAlex gives the
        display name "Xu Chen", Crossref given="Chen" family="Xu", PubMed "Xu C".
        The structured names must win and the key must follow them."""
        import xml.etree.ElementTree as ET
        from factual_research.connectors import crossref, openalex, pubmed
        from factual_research.export import intext_author, reference
        from factual_research.store import Store
        doi = "10.3389/fnut.2024.1424972"
        title = "The effects of creatine supplementation on cognitive function in adults: a systematic review and meta-analysis"
        names = [("Xu", "Chen", "C"), ("Bi", "Siyuan", "S"), ("Zhang", "Wenxin", "W"), ("Luo", "Lin", "L")]
        oa = openalex.parse({"id": "https://openalex.org/W4400581339", "doi": f"https://doi.org/{doi}",
                             "display_name": title, "publication_year": 2024,
                             "authorships": [{"author": {"display_name": f"{f} {g}"}} for f, g, _ in names]})
        self.assertEqual(oa.authors[0], "Chen, Xu")              # best guess from a display name...
        pm = pubmed.parse_article(ET.fromstring(
            f"<PubmedArticle><MedlineCitation><PMID>39070254</PMID><Article><ArticleTitle>{title}.</ArticleTitle>"
            "<AuthorList>" + "".join(f"<Author><LastName>{f}</LastName><Initials>{i}</Initials></Author>"
                                     for f, _, i in names) + "</AuthorList>"
            f'<ELocationID EIdType="doi">{doi}</ELocationID></Article></MedlineCitation></PubmedArticle>'))
        cr = crossref.parse({"DOI": doi, "title": [title], "issued": {"date-parts": [[2024, 7, 12]]},
                             "author": [{"given": g, "family": f} for f, g, _ in names]})
        st = Store(Path(tempfile.mkdtemp()))
        k, new = st.upsert(oa)
        self.assertEqual((k, new), ("chen2024creatine", True))
        k, _ = st.upsert(pm)                                     # ...corrected by the structured source
        self.assertEqual(k, "xu2024creatine")
        k, _ = st.upsert(cr)
        w = st.get(k)
        self.assertEqual(w.authors, ["Xu, Chen", "Bi, Siyuan", "Zhang, Wenxin", "Luo, Lin"])
        self.assertNotIn("authors_from", w.extra)
        self.assertIsNone(st.row("chen2024creatine"))
        self.assertEqual(st.resolve(doi), "xu2024creatine")
        self.assertEqual(intext_author(w), "Xu et al.")
        self.assertTrue(reference(w, "vancouver").startswith("Xu C, Bi S, Zhang W, Luo L."))
        # once screened, a key is never changed again
        st.decide(k, "ta", "include", "PICO")
        st.upsert(openalex.parse({"id": "https://openalex.org/W4400581339", "display_name": title,
                                  "publication_year": 2024, "authorships": [{"author": {"display_name": "Q Zed"}}] * 5}))
        self.assertIsNotNone(st.row("xu2024creatine"))

    def test_openalex_abstract(self):
        from factual_research.connectors.openalex import parse
        w = parse({"id": "https://openalex.org/W1", "display_name": "T", "abstract_inverted_index": {"b": [1], "a": [0]},
                   "ids": {"pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/123"}, "type": "preprint"})
        self.assertEqual((w.abstract, w.openalex, w.pmcid, w.is_preprint), ("a b", "W1", "PMC123", True))


class MCP(unittest.TestCase):
    def test_handshake(self):
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
        p = subprocess.run([sys.executable, "-m", "factual_research", "mcp"], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                           capture_output=True, text=True, cwd=ROOT, timeout=60)
        lines = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
        self.assertEqual(lines[0]["result"]["serverInfo"]["name"], "factual-research")
        names = {t["name"] for t in lines[1]["result"]["tools"]}
        self.assertTrue({"search", "verify", "evidence_add", "extract_set"} <= names)


if __name__ == "__main__":
    unittest.main()
