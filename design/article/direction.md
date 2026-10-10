# Direction: factual-research article template

## Options considered
A. **Scholarly brief** (chosen): warm paper background, text serif body (system Charter/Iowan, offline), system sans for UI and metadata; Distill-style metadata grid (refs/distill-pub-2020-circuits-zoom-in), sticky TOC beside the text (refs/gwern-net-scaling-hypothesis), key-findings cards with certainty (refs/ourworldindata-org-co2-and-greenhouse-gas-emissions). Serves P2, P6, P8.
B. Dashboard report: sans everywhere, tiles, charts first. Rejected: the deliverable is prose to read, not a dashboard (P6/P8 measure and size).
C. Minimal print-like page (pure B/W, numbered footnotes only). Rejected: loses the verification layer (quotes) that is the point of factual-research.

## Tokens
- Body 19px (P6) / line-height 1.5 (P2 says 1.2-1.45; we go slightly above for screen reading, as both reading refs do: gwern 1.6, distill 1.7), measure 68ch (P10, 45-90 chars).
- Type scale: 1.25 ratio, steps -2..4; 2 families + mono.
- Palette (oklch): paper 0.985 0.006 85, ink 0.24 0.015 260 (15.8:1), muted 0.47 (6.5:1), accent teal 0.45 0.085 205 (6.8:1, not stock indigo). Dark: own values (P5), all AA (P4).
- Certainty colours: high green, moderate teal, low amber, very low red, always with a text label (not colour only).

## Signature detail
Citation chips that open a popover with the verbatim anchor quote, its location and a "verified on full text" mark. No animation beyond a 120ms fade, removed under reduced motion (P9).

## Principle checklist
- P2 line spacing: body 1.5 (slightly above P2, see Tokens), headings 1.2.
- P4 contrast: all text pairs checked with design-scout contrast.
- P5 light default, dark follows OS + toggle.
- P6 body 19px desktop, 17.5px mobile.
- P10 measure: .prose max-width 68ch.
- P8 sticky top bar: scroll-padding-top on html.
- P9 reduced motion: transitions off.
