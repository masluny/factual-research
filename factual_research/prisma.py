"""PRISMA 2020-style flow counts and diagram (SVG)."""
from __future__ import annotations

import html as H
from collections import Counter

from .store import Store


def counts(st: Store) -> dict:
    qs = st.queries()
    by_conn = Counter()
    for q in qs:
        if not q["error"] and q["run"] != "watch":
            by_conn[q["connector"]] += q["returned"] or 0
    rows = list(st.db.execute("SELECT key, status, origin FROM works"))
    from_db = [r for r in rows if r["origin"] == "search"]
    other = [r for r in rows if r["origin"] != "search"]
    identified = sum(by_conn.values())
    dup = max(0, identified - len(from_db))
    st_count = Counter(r["status"] for r in rows)
    reasons = Counter()
    ft_reasons = Counter()
    last = {}
    for d in st.decisions():
        last[d["key"]] = d
    for k, d in last.items():
        if d["decision"] == "exclude":
            r = (d["reason"] or "no reason").split(":")[0].split("(")[0].strip()[:60]
            (ft_reasons if d["stage"] == "ft" else reasons)[f"{'[auto] ' if d['stage'] == 'auto' else ''}{r}"] += 1
    ft_keys = {r[0] for r in st.db.execute("SELECT key FROM fulltext")}
    incl = [r["key"] for r in rows if r["status"] in ("ta_included", "ft_included")]
    sought = len(incl) + st_count["ft_excluded"] + st_count["not_retrieved"]
    retrieved = sum(1 for r in rows if r["key"] in ft_keys and r["status"] in ("ta_included", "ft_included", "ft_excluded"))
    return {
        "by_connector": dict(by_conn), "identified": identified, "other_sources": len(other),
        "other_by_origin": dict(Counter(r["origin"] for r in other)), "duplicates": dup,
        "screened": len(rows), "auto_excluded": st_count["auto_excluded"], "ta_excluded": st_count["ta_excluded"],
        "awaiting": st_count["new"] + st_count["maybe"], "excluded_reasons": dict(reasons.most_common(8)),
        "sought": sought, "full_text": retrieved, "not_retrieved": sought - retrieved,
        "ft_excluded": st_count["ft_excluded"], "ft_reasons": dict(ft_reasons.most_common(8)),
        "included": len(incl), "included_fulltext": sum(1 for k in incl if k in ft_keys),
    }


def svg(c: dict) -> str:
    W = 860

    def box(x, y, w, h, title, lines, cls="b"):
        t = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" class="{cls}"/>',
             f'<text x="{x + 12}" y="{y + 22}" class="bt">{H.escape(title)}</text>']
        for i, l in enumerate(lines):
            t.append(f'<text x="{x + 12}" y="{y + 42 + i * 17}" class="bl">{H.escape(l)}</text>')
        return "\n".join(t)

    def arrow(x1, y1, x2, y2):
        return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" class="ar" marker-end="url(#ah)"/>'

    conn = [f"{k}: {v}" for k, v in sorted(c["by_connector"].items(), key=lambda x: -x[1])]
    other = [f"{k}: {v}" for k, v in c["other_by_origin"].items()] or ["none"]
    excl = [f"{k}: {v}" for k, v in c["excluded_reasons"].items()][:6]
    ftex = [f"{k}: {v}" for k, v in c["ft_reasons"].items()][:5]
    h1 = 50 + 17 * max(len(conn), 1)
    y2 = 40 + h1 + 30
    h2 = 58
    y3 = y2 + h2 + 30
    h3 = 58 + 17 * max(len(excl), 0)
    y4 = y3 + max(h3, 75) + 30
    y5 = y4 + 75 + 30
    H_ = y5 + 75 + 20
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H_}" class="prisma" role="img" aria-label="PRISMA flow diagram">',
             '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
             '<path d="M0,0 L10,5 L0,10 z" class="ah"/></marker></defs>',
             box(20, 40, 400, h1, f"Records from databases (n = {c['identified']})", conn),
             box(450, 40, 390, h1, f"Other sources (n = {c['other_sources']})", other, "b2"),
             arrow(220, 40 + h1, 220, y2),
             box(20, y2, 400, h2, f"After duplicates removed (n = {c['screened']})", [f"duplicates removed: {c['duplicates']}"]),
             arrow(220, y2 + h2, 220, y3),
             box(20, y3, 400, max(h3, 75), f"Records screened (n = {c['screened']})",
                 [f"awaiting screening: {c['awaiting']}"]),
             box(450, y3, 390, max(h3, 75), f"Excluded (n = {c['auto_excluded'] + c['ta_excluded']})",
                 [f"automatic rules: {c['auto_excluded']}", f"title/abstract: {c['ta_excluded']}"] + excl[:4], "x"),
             arrow(420, y3 + 30, 450, y3 + 30),
             arrow(220, y3 + max(h3, 75), 220, y4),
             box(20, y4, 400, 75, f"Reports sought (n = {c['sought']})",
                 [f"full text retrieved: {c['full_text']}", f"abstract only: {c['not_retrieved']}"]),
             box(450, y4, 390, 75, f"Excluded after full text (n = {c['ft_excluded']})", ftex[:2] or ["none"], "x"),
             arrow(420, y4 + 30, 450, y4 + 30),
             arrow(220, y4 + 75, 220, y5),
             box(20, y5, 400, 75, f"Included (n = {c['included']})",
                 [f"verified against full text: {c['included_fulltext']}"], "inc"),
             '<text x="20" y="22" class="ph">Identification → Screening → Included</text>',
             "</svg>"]
    return "\n".join(parts)
