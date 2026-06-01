# Phenoscribe context

Documentation that travels with the codebase. Working notes live here; runtime data does not.

## Layout

| Path | What lives here |
|---|---|
| `architecture-notes.md` | Live design reference for the pipeline. Updated as the system evolves. The first thing to read. |
| `exports/` | Formal documents shared with stakeholders (Marc, funders, collaborators). ADRs, client reports, benchmark write-ups. |
| `plans/` | Forward-looking implementation plans currently in flight. Empty when nothing is open. |
| `shipped/` | Plans that have been fully delivered. Dated, sorted oldest-first. |

All files in `exports/` and `shipped/` are named `YYYY-MM-DD-<slug>.md` so they sort chronologically.

## What's in each folder right now

### `exports/`
- **`2026-03-01-adr-hpo-pipeline.md`** — Architecture Decision Record for the initial pipeline (transcription → PII → LLM → ChromaDB → Excel). Six-stage design, tech-stack rationale.
- **`2026-03-01-client-report-architecture-validation.md`** — Pre-build technical validation report for Dr Jamoulle. Audits the previous manual ChatGPT-based coding (≈35% accurate), proposes the automated pipeline.
- **`2026-05-30-ontogpt-benchmark.md`** — Head-to-head ontoGPT vs Phenoscribe on three pseudonymised transcripts. Verdict: keep Phenoscribe.

### `shipped/`
- **`2026-03-01-hpo-pipeline-initial-build.md`** — The 11-task plan for the initial pipeline build. Shipped 2026-03-07. Includes post-ship additions (diarization, transcript caching, detailed output format).
- **`2026-05-30-stakeholder-feedback-round-1.md`** — Four-task plan addressing Peter Robinson's HPO+LLM caveat, hpo-toolkit hierarchy walks, the `phenoscribe aggregate` cohort command, and the ontoGPT benchmark. Shipped 2026-05-30 across PRs #1–#4.
- **`2026-06-01-gradio-web-app.md`** — Gradio browser UI + Docker image + Claude provider wired end-to-end. Tasks 1–3 of the cross-platform Docker plan; multi-arch builds, OS launchers, and CUDA variant deferred.

## Data files do not live here

Ground-truth Excels (`CRS__*.xlsx`, `hop_list_terms.csv`) and recordings live under `data/` (gitignored). They contain patient identifiers and should never be committed.
