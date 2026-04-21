# mantis-rubric — Precedent Log

Self-observed operational failures for the M7 Zheng Pairwise Rubric Judgment engine. Format per `shared/conduct/precedent.md`. Append; never delete without marking `RESOLVED YYYY-MM-DD`.

Consult: grep before `print()`-ing any string containing math symbols, Greek letters, or emoji on Windows.

---

## 2026-04-21 — Unicode `≥` crashed Windows cp1252 console

**Command that failed:**
`print("Kappa ≥ 0.4 stable")` inside `plugins/mantis-rubric/scripts/` — raised `UnicodeEncodeError: 'charmap' codec can't encode character '\u2265'` on the default Windows console (cp1252).

**Why it failed:**
The Windows console `stdout` encoding defaults to cp1252, which has no mapping for U+2265 (≥). `print()` writes through `sys.stdout.encoding`; the encoder raises before the console sees the byte.

**What worked:**
Use ASCII `>=` in any string literal that may be printed. Let JSON persistence handle Unicode escapes (`json.dump` emits `\u2265` by default unless `ensure_ascii=False`). Kappa log stays JSONL — no console hop.

**Signal:** reserve Unicode math/greek for JSON payloads and PDF output (the dark-themed report renderer handles UTF-8). Anything that reaches `print()` is ASCII-only.

**Tags:** windows, cp1252, unicode, print, m7, kappa
