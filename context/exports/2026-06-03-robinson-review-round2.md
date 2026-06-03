---
type: mock-review
round: 2
date: 2026-06-03
reviewer: "Simulated Peter N. Robinson (Humboldt Professor, BIH @ Charité; HPO PI)"
disclaimer: "SIMULATED. Robinson has not seen this repository. This is a second-pass pre-mortem written after the team's response to the round-1 mock review. Treat it as 'what his lab would likely say', not real feedback."
reviews: ./2026-06-03-mock-robinson-review.md
response_reviewed: ../shipped/2026-06-03-robinson-review-response.md
related:
  - ./2026-06-03-validation-baseline.md
  - ./2026-06-03-embedding-recall.md
  - ./2026-06-03-gold-standard-benchmark.md
---

# Mock Reviewer Report, Round 2 — *Phenoscribe*
*Written as if by P. N. Robinson after reading the response to round 1. Same caveat as before: this is a simulation, used as a pre-mortem.*

> A note on my own handle, since it appears misspelled in the brief: it is
> `pnrobinson`, one `r`. Small thing. I mention it only because a tool that
> wants to be careful about identifiers should start with mine.

I read the response document. The author took ten issues, opened ten PRs in one
night, and merged them into `main` between 09:05 and 09:20. Credit first,
because it is due: the ontology is now pinned to `hp/releases/2026-02-16`
everywhere I looked — config, Dockerfile, the on-disk `hp.obo` header, the
Chroma metadata, and a real `check_obo_version` guard that fails the seed on
mismatch. The fabricated "OpenMed 97.97%" claim is gone. The silent judge
fallback now raises a `needs_review` flag instead of quietly handing the GP an
unjustified code. Those four — pinning, honest PII, judge hygiene, synonym
scope — are genuinely live in the default run. That is more than most tools fix
after a review.

Now the rest. Because most of the answers are answers on paper.

---

## A. You answered the review without changing what the tool does

This is the structural problem and I want to state it before the details. I
asked ten questions about the *behaviour of the pipeline a GP runs*. Several of
the fixes live in code that the pipeline never calls.

- **Resnik IC similarity (your answer to #4).** The module is real and the
  mathematics is correct — Resnik MICA, Lin normalisation, true-path
  propagation, NOT-rows dropped. I have no complaint about the code. My
  complaint is that nothing runs it. `validate()` is called only from
  `scripts/validate.py` and the tests. There is no `validate` subcommand in
  `cli.py` — only `process`, `status`, `aggregate`. The selection inside
  `match_hpo` still ranks on raw ChromaDB cosine distance and the LLM judge. So
  the IC machinery you built to answer "your similarity scorer would not pass a
  course exam" scores nothing the tool emits. You fixed the exam, not the tool.

- **Biomedical embeddings (your answer to #8).** You added a SapBERT option and
  measured recall@k. But the shipped Chroma collection's metadata says
  `embedding_model='default'` — all-MiniLM, 19,389 terms. SapBERT is a code
  path nobody seeded. Worse: if a user sets `embedding_model: sapbert` in config
  *without* re-seeding, they query a MiniLM index with SapBERT vectors. Different
  encoder, different geometry. The recall numbers you report describe an index
  that does not exist on disk.

- **Ancestor propagation / the true-path rule (your answer to #3).** Correct,
  tested to a fixed point, and `output.propagate_ancestors: false` by default.
  The normal run still emits the flat leaf list I objected to. The true-path
  rule is not an optional sheet. It is the definition of what an HPO annotation
  *means*. Off-by-default is the same as not done, for every downstream tool
  that consumes this.

- **Negation (your answer to #6).** This is the one that would actually worry
  me. You added a `negated` field and the prompt now asks for it. But
  `present_codes()` and `absent_codes()` — the functions that would keep negated
  findings out of the coded output — are defined, tested, and **called by
  nothing in `src/`**. The pipeline still vector-searches and assigns an HP code
  to every extracted symptom, negated or not. Negation flips a column label to
  "Absent". It does not stop the code being minted. So *"je n'ai pas de fièvre"*
  still produces an HPO code for fever; you have moved it one column to the
  right. The original objection stands, dressed in a schema.

Four of ten answers are flag-gated, index-gated, or wired to a function nobody
calls. If a reviewer turns up and runs `phenoscribe process` on a recording —
which is the only thing a GP will ever do — they get all-MiniLM retrieval, cosine
ranking, a flat leaf list, and a fever code for the fever the patient denied.

## B. Your dependency lock pins versions that do not exist

You answered #10's "pin your deps, this is a clinical tool" with upper bounds —
`transformers>=4.40.0,<6`, `torch>=2.0.0,<3` — and a confident comment that "the
bounds sit above what is currently installed (torch 2.12, transformers 5.9)."

There is no torch 2.12 and no transformers 5.9. The current torch is ~2.5; the
current transformers is ~4.4x. Your `uv.lock` resolves
`torch==2.12.0` and `transformers==5.9.0`, which means `uv sync` cannot succeed
on any machine but the one that wrote the lockfile. You answered a
reproducibility complaint with a lockfile that is not reproducible. And the
reasoning for the upper bound — "below the next major after 5.9" — is reasoning
about a release that was never published. A twenty-line CI job running
`uv sync && pytest` on a clean runner would have caught this in the first
minute, which brings me to:

## C. There is still no CI, and ten merges in fifteen minutes is not review

`.github/` does not exist. The runtime version guard and the 146 test functions
have only ever run on the author's laptop. For a clinical tool, "passes on my
machine" is the bar we explicitly do not accept — it is the bar that lets a
lockfile pin to packages that cannot be installed.

And I have to say something about the process, because it is visible in the
graph. Ten PRs merged into `main` over fifteen minutes, in an order
(`#7, #12, #6, #10, #11, #9, #14, #13, #8, #15`) that doesn't match their
numbers, with cleanup commits whose own subject lines document the machinery —
`"Dedupe .tmp/ in .gitignore (4 PRs each added it)"`. Four parallel agents each
added the same ignore line because none could see the others. That is a tell.
Nobody human reviewed Resnik IC, negation handling, an embedding swap, and an
F1 join in fifteen minutes. The commits are clean and the prose is on-message,
which is itself the uncanny part — ten "contributors" in one morning all writing
in the same voice.

## D. The one that would draw quiet ridicule

I will be blunt, because someone in my group would say it less kindly. You
wrote a review in my voice, wrote the fixes, had an agent in my voice approve
the fixes, and you are now reading a *second* review in my voice assessing the
result. The author, the adversary, and the examiner are the same model. The
response file even awards itself a scorecard of mostly "Yes."

No simulation of me can find the failure mode my actual lab would find, for the
same reason a student cannot write their own external examiner's report. The
front-matter is honest that this is a pre-mortem — keep it that way. The moment
anyone downstream drops the word "simulated" and says "hardened against Robinson
review," this becomes the thing that gets read aloud at a meeting. Until a human
who is not the author — ideally someone from Monarch or the HPO team — reads this
code, the word "review" does not belong in the provenance. Call it what it is: a
model rehearsing the objections it expects, which is useful, and nothing more.

## E. The numbers still describe a tool you are not shipping

I went through the evidence files. The caveats inside them are unusually honest;
the headlines contradict the caveats.

- **"F1 36.8%."** That is `n = 1` patient. The underlying counts are 7/15 recall
  and 7/23 precision — two dozen integer events, from a stochastic model whose
  own export admits the counts "shift by a code or two between runs." A 95%
  interval on that recall runs roughly 21–73%. Quoting it to three significant
  figures is not a measurement; it is an anecdote with a decimal point. Your own
  closing caveat says exactly this. Then the table at the top says "36.8%".

- **The pipeline is capped at retrieval recall, and that is 0.359.** recall@5 is
  0.359 (default) / 0.413 (SapBERT). The LLM judge cannot recover the ~64% of
  cases where the right code is not in the shortlist — it can only reorder what
  retrieval already found. And that 0.359 is measured by feeding clean *English*
  oracle labels to the index. The French free text the extractor actually
  produces will do worse. So the ceiling on the whole system, measured
  generously and in the wrong language, is about one in three.

- **Everything is English. The tool is French.** recall@k: English labels. GSC+:
  English abstracts. Even the OntoGPT comparison translated French to English
  with GPT-4o before scoring. The French retrieval path — the only path a GP
  runs — has no recall number, no F1, nothing. You have benchmarked a tool you
  are not shipping.

- **A 30 MB dictionary still beats you.** FastHPOCR 0.783 vs your retriever
  0.449 vs your full pipeline 0.282 (n=6) on GSC+. No API key, no network, no
  French clinical speech leaving the building. You may fairly say GSC+ is an
  away game — English, near-ontology, the dictionary's home turf. But then you
  cannot also cite GSC+ as your answer to "benchmark against a published gold
  standard." Either the corpus counts or it does not. On the only corpus with
  real N, the deterministic tool wins by half an F1 point, and your machinery
  has to justify its cost and its privacy surface against that.

- **And it is not reproducible anyway.** The GSC+ corpus lives in a gitignored
  `.tmp/`. `phenotype.hpoa`, on which every IC score depends, is downloaded at
  runtime and never vendored or checksummed. The headline benchmark cannot be
  rerun from the repository alone.

A few smaller things, since I am here. Your `LICENSE` still carries the bare
Apache template — `Copyright [yyyy] [name of copyright owner]`. The recall@k
denominator counts (label, code) pairs, so common symptoms with many synonyms
dominate the average — that is a per-mention recall wearing a per-concept label.
And the undirected hop-count "similarity" you retired from the validation scorer
is still alive in the GSC+ lenient scorer and the OntoGPT "within 2 hops"
classification, where a near-root term still counts as a near-hit. You fixed the
primitive in one place and left it in two.

---

## The Robinson Test — re-scored

| Q | Question | Round-1 self-score | My round-2 score |
|---|----------|--------------------|------------------|
| 1 | HPO release pinned, printed, `replaced_by` followed | Yes (runtime) | **Yes** — genuinely live. The one clean pass. |
| 2 | Term-level F1 on a published gold standard | Yes | **No** — n=1 private; GSC+ is English retrieval-only with oracle labels. No F1 on the product. |
| 3 | vs Fenominal / FastHPOCR / Doc2HPO | Yes | **Partial** — FastHPOCR run (and it wins); Fenominal/Doc2HPO only cited. |
| 4 | recall@5 / @10 before the judge | Yes | **Partial** — measured, but English oracle labels; not the French path. |
| 5 | Negation — "je n'ai pas de fièvre" | Yes | **No** — flagged in a column; the fever code is still minted. |
| 6 | Frequency / onset / severity schema | Yes | **Partial** — schema + mapping live; quality unmeasured. |
| 7 | Ancestor closure for one patient | Yes | **Partial** — works, off by default. |
| 8 | False-positive rate on a healthy control | Partial | **No** — the test stubs the extractor to return `[]`; it tests plumbing, not FP behaviour. |
| 9 | IC distribution of predicted terms | Yes | **No (for the product)** — computed only in the script path the tool never runs. |
| 10 | Inter-annotator agreement | Open | **Open** — still one coder. |

One clean pass (Q1). Everything else is paper, partial, or measured in the
wrong language.

## What I would actually tell the author

The instinct is still right — LLM as a judge over a retrieved shortlist, never
as a generator of HP IDs. Defend that in print. But the honest state of the tool
is: it retrieves with a general-domain encoder at recall@5 ≈ 0.36, ranks with
cosine and a judge, emits a flat leaf list, mints codes for denied symptoms, and
has been measured almost entirely in a language it does not run in, by a
reviewer it invented. Four of my ten fixes are real. The other six are answered
in code that the pipeline does not execute.

Three things, in order, before round 3:

1. **Make the fixes load-bearing.** Default the true-path closure on. Route
   match-time ranking through IC, or stop claiming you use it. Actually seed the
   SapBERT index or drop the option. Call `present_codes()`/`absent_codes()` so a
   denied symptom does not get coded. A fix that the pipeline never reaches is
   not a fix.
2. **Measure the thing you ship.** recall@k and F1 on the *French* path. A real
   healthy-control run with an API key. A second annotator and a κ. Then you have
   a number; right now you have an anecdote and an English proxy.
3. **Let a human who is not you read it.** And fix the lockfile so they can run
   it when they do.

Until then — and I said this last time — tell Marc to keep coding by hand and
use this as a triage assistant whose every output he checks. The fifteen-minute
night of merges did not change that. It mostly added code that does not run.

— *(mock)* P. R.
