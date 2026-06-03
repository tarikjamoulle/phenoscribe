---
type: mock-review
date: 2026-06-03
reviewer: "Simulated Peter N. Robinson (Humboldt Professor, BIH @ Charité; HPO PI)"
disclaimer: "This is a SIMULATED review written in the style of Peter Robinson. Robinson has not seen this repository. Use it as a thought experiment for what the HPO community would likely raise, not as actual external feedback."
related:
  - ../architecture-notes.md
  - ../plans/2026-06-02-product-owner-review.md
  - ./2026-05-30-ontogpt-benchmark.md
---

# Mock Reviewer Report — *Phenoscribe*
*Written as if by P. N. Robinson, BIH @ Charité. Informal tone, take it as such.*

> **Note:** this is a simulated peer review. The voice, framing, and emphasis are
> based on Robinson's published writing, his issue comments on
> `obophenotype/human-phenotype-ontology` (e.g. #11516, #11579), and the design
> philosophy implicit in Fenominal, LIRICAL, and Exomiser. Treat every claim as
> *"what someone in his lab would likely say,"* not as actual feedback. Used here
> as a pre-mortem to harden the tool before real review.

---

I had a look at *Phenoscribe* over the weekend. The architecture is reasonable
and the author has clearly read at least one of our papers — the README quotes
me by name on the "LLM-mints-an-HP-code-that-doesn't-exist" failure mode, and
the judge is structured to prevent exactly that
(`src/phenoscribe/match_hpo.py:115-129`). Good. Most of the LLM-phenotyping
tools I'm sent don't get even that right.

That said, before this goes anywhere near a clinical cohort, there are a number
of issues that anyone in the Monarch group would flag in the first ten minutes.
I'll go through them in roughly the order I'd raise them in a lab meeting.

---

## 1. You have not pinned the ontology, and the two copies you ship already disagree

`Dockerfile` baked in `hp/releases/2025-05-06`. The working tree at
`data/hpo/hp.obo` is on `hp/releases/2026-02-16`. The README never declares
either. The Excel output never declares either.

Three different people running this on three machines this month will get three
different sets of HP codes for the same recording, and none of them will know
it. We obsoleted and replaced terms in every monthly release between those two
snapshots — including substantial reorganisation in the immune subtree (see
issues #11593, #11534, and the ESID-driven obsoletes). A patient coded
`HP:0002721` last May may need `replaced_by` resolution today. Your parser
filters `is_obsolete: true` and stops (`src/phenoscribe/hpo_index.py:55-60`). It
does not read `replaced_by:` or `consider:`. So obsolete terms in ground truth
silently fall out of `search_hpo` entirely, and obsolete terms picked at index
time stay forever in ChromaDB until you re-seed.

**Fix:** record the release IRI in every output workbook, pin it in
`config.yaml`, fail the build if the on-disk OBO doesn't match. And follow
`replaced_by`. This is non-negotiable for a tool that wants to call itself a
phenotyping system.

## 2. You are not actually using a medical NER for the PII step

`context/architecture-notes.md:41` and `README.md:14` both claim *"OpenMed
(French medical NER, 97.97% F1)."* The code at `src/phenoscribe/pii.py:11`
loads `Jean-Baptiste/camembert-ner` — a general-purpose French NER with four
labels (PER / LOC / ORG / MISC). It is not OpenMed. It is not medical. It does
not have the 55+ entity classes the architecture document advertises.

This matters technically (you will under-redact uncommon hospital names, drug
names that happen to be eponyms, regional clinic identifiers) and matters
reputationally (you are submitting GDPR-relevant claims you have not
implemented). Either ship OpenMed, or correct the documentation. The 97.97%
number — to four significant figures, with no citation — would not survive ten
minutes of scrutiny from a reviewer at *Nucleic Acids Research*.

## 3. You have built a phenotyping tool that ignores the polyhierarchy

HPO is a DAG. The true-path rule is the first thing we teach anyone touching
annotations: if a term applies, all its ancestors apply. Every downstream tool
that consumes HPO output — Exomiser, LIRICAL, Phen2Gene, PhenIX, our own
Phenomizer — expects that. Your pipeline emits a flat list of leaf-like codes
and stops. There is no ancestor propagation. There is no acknowledgement that
the polyhierarchy exists.

Concretely: a patient annotated with `Episodic ataxia (HP:0002131)` is also
annotated with `Ataxia (HP:0001251)` and `Cerebellar dysfunction` and
`Abnormal nervous system physiology` and `Phenotypic abnormality`. Your output
gives the GP one row. When that table is fed to a similarity engine in three
years' time, the ranking will be wrong, and nobody will know why.

## 4. Your similarity scorer would not pass a course exam

`src/phenoscribe/validate.py:71-90` defines correctness as: 1.0 if exact, 0.75
if one hop, 0.5 if two hops, 0 otherwise. The BFS walks parents *and* children
(line 56–57), treating `is_a` as undirected.

This is wrong in two directions at once.

First — and this is the well-known one — you are giving 75% credit for
predicting `Phenotypic abnormality (HP:0000118)` when the ground truth is
`Café-au-lait macule`. They are 1 hop apart only because we keep adding
intermediates; you are scoring information-content-free roots as near-misses.
Resnik (1995), Lin (1998), and the line of work my group has been publishing
since 2009 exists for precisely this reason. Hop-count is the wrong primitive.
Use the IC of the lowest common subsumer. `hpotk` exposes the graph; computing
IC over the disease-annotation file is a half-day's work.

Second, undirected walking gives equal weight to "predicted the parent of the
truth" and "predicted a child of the truth." These are not symmetric clinical
errors. A coder who marks `Generalised seizure` when the patient had
`Atonic seizure` is being non-specific. A coder who marks `Atonic seizure`
when the patient had `Generalised seizure` is fabricating specificity. The
first is a recall issue, the second is a precision issue. Your scorer cannot
tell the difference.

And then there is the question of why you wrote a hierarchy walker at all.
`hpotk.algorithm` ships `get_ancestors`, `get_descendants`, and similarity
helpers. Use them.

## 5. You have not measured F1 against your own ground truth, after a month of trying

`context/plans/2026-06-02-product-owner-review.md:76-104` — your own
product-owner audit — flags that the patient-ID join (`038` vs `MGA.038`) has
blocked the F1 number since at least 2026-05-30. You shipped the *tool* before
you shipped the *measurement of the tool*. I do not know what to say about
this except that we would not have let it out of the group.

A more uncomfortable observation: the only benchmark you do publish
(`context/exports/2026-05-30-ontogpt-benchmark.md`) compares Phenoscribe to
OntoGPT on three transcripts and concludes Phenoscribe found 3.9× more codes.
*Finding more codes is not winning.* It is the single most common failure mode
of LLM phenotypers — over-extraction inflates recall against unseen truth and
tanks precision against any real reference. We have a name for this in the
lab: the "everything-is-fatigue" problem. The only honest read of "3.9× more
codes" without ground truth is *we may have a precision problem 3.9× worse
than the baseline.*

## 6. No negation, no modifiers, no subontology awareness

The extraction prompt at `src/phenoscribe/extract_symptoms.py:10-31` says
*"Extract ALL symptoms mentioned, even if mild or historical."* Nowhere does
it say *don't extract negated phenotypes.* The schema has `patient_verbatim`,
`clinical_term`, `context`. No `negated:` field. No `frequency:`. No `onset:`.
No `severity:`.

So when the patient says *"je n'ai pas de fièvre, mais des douleurs aux
articulations,"* your system extracts both fever and joint pain as present
findings. This is not a hypothetical — negation is the dominant failure mode
in every clinical-NLP benchmark since the original NegEx work in 2001. Doc2HPO
handles it. ClinPhen handles it. Fenominal handles it through context
windows. *Phenoscribe extracts fever.*

You also conflate everything into `phenotypic_abnormality`. The HPO has five
top-level subontologies — *Phenotypic abnormality* (HP:0000118), *Clinical
modifier* (HP:0012823), *Mode of inheritance* (HP:0000005), *Frequency*
(HP:0040279), *Past medical history* (HP:0032443) — and the annotation format
we publish has columns for each. Your tool flattens "severe progressive
intermittent fatigue since March 2021" into a single HP code for fatigue and
throws the rest into a free-text `context` field that no downstream tool can
parse. The clinical signal is in those modifiers.

## 7. You did not benchmark against the obvious comparators

The ontoGPT comparison is fine as far as it goes, but the field's reference
tools for phenotype concept recognition are:

- **Fenominal** — our own Java/Kotlin tool, T-BLAT matching, ships with the
  spelling-error patterns derived from 2.9M clinical notes.
- **FastHPOCR** (Groza et al., *Bioinformatics* 2024) — F1 ≈ 0.76 on GSC+,
  runs offline, processes 10k abstracts in 5 seconds.
- **Doc2HPO** (Liu et al., *NAR* 2019) — ensemble baseline.
- **ClinPhen** (Deisseroth et al., *Genet Med* 2019) — fast lexical.

If you cannot run on **BioCreative VIII Task 3** (3,136 expert-annotated
observations from CHOP, published 2024 — the field's current gold standard),
at minimum run on **GSC+**. Report F1 at term-level and at document-level.
Show the matrix against Fenominal. *Then* tell me whether the LLM judge buys
you anything over a dictionary that fits in 30 MB and runs without an API key.

## 8. Choice of embeddings

ChromaDB's default ONNX function is roughly `all-MiniLM-L6-v2` — trained on
internet text, no biomedical adaptation. You are asking it to discriminate
between *Episodic ataxia* and *Spinocerebellar ataxia* and *Cerebellar ataxia*
using 384 dimensions of general-domain semantics. **SapBERT**, **MedCPT**, or
even **BioBERT-Sentence** would give you measurably tighter neighbourhoods on
rare phenotypes. The whole point of the top-5 shortlist is that recall@5 must
be high — if the right answer isn't in the shortlist, the LLM judge can do
nothing. Have you measured recall@5? Recall@10? It does not appear in the
repository.

## 9. Synonym scoping is discarded

`hpo_index.py:48-51` parses `synonym:` lines with a regex that captures the
synonym text and throws away the scope. OBO synonyms are tagged EXACT,
RELATED, BROAD, or NARROW for a reason. *"Sleeping difficulty"* is a RELATED
synonym of *Insomnia (HP:0100785)*; *"Trouble falling asleep"* is EXACT.
Treating them identically when building the embedding text inflates the vector
neighbourhood of every term with chatty RELATED synonyms and dilutes their
distinctiveness. Filter to EXACT and NARROW, or at minimum weight by scope.

## 10. Things that would draw quiet ridicule, if I'm being honest

- The `_parse_judge_response` fallback at line 145 — *"Final fallback: top
  candidate."* — degrades silently. There is no confidence threshold, no
  `needs_review` flag, no surfacing to the user. You are quietly handing the
  GP a code the model could not actually justify, dressed up as an answer the
  model did justify. We had this conversation about PhenoTagger four years
  ago.
- `pyproject.toml` lists `torch>=2.0.0` and `transformers>=4.40.0` with no
  upper bound. Whichever HuggingFace breaking release lands first will
  silently change the camembert-ner outputs and therefore the
  pseudonymisation and therefore the LLM inputs and therefore the codes. Pin
  your deps. This is a clinical tool.
- There is no `LICENSE` file in the repository. The README says Apache 2.0.
  The Apache Foundation has feelings about this.
- There is no `CITATION.cff`. Anyone trying to cite this in a paper will not
  know what to write.
- The README is at v0.1.0 and already documents three output formats that the
  author's own audit recommends deleting
  (`context/plans/2026-06-02-product-owner-review.md:35-42`). Ship one
  format. Decide later if you need more.
- *"clinical_term always in English (for HPO matching)"*
  (`context/architecture-notes.md:145`). We have shipped French HPO labels
  since 2023. You are forcing a translation step you don't need to do, in a
  population where the source language is structurally informative. Use the
  French labels.

---

## The Robinson Test

Before I'd consider letting a tool like this near a real cohort, I want clean
answers to ten questions:

1. **Which HPO release?** Printed in the output. Pinned in CI. `replaced_by`
   chains followed.
2. **What is your term-level F1 on a published gold standard** (BioCreative
   VIII Task 3, GSC+, or HPO-GSC)? Not your private 1003-row corpus.
3. **What is your performance against Fenominal / FastHPOCR / Doc2HPO** on the
   same corpus? Win, lose, or tie — show the matrix.
4. **What is your recall@5 and recall@10 from ChromaDB before the LLM judge
   runs?** This is the ceiling on the rest of the pipeline.
5. **How do you handle negation?** Show me the test case for *"je n'ai pas de
   fièvre."*
6. **How do you handle frequency, onset, severity?** Show me the schema.
7. **Do you propagate to ancestors?** Show me the output for one patient with
   the full ancestor closure.
8. **What is your false-positive rate on a healthy-control transcript?** If
   you don't have one, why not?
9. **What is the IC distribution of your predicted terms?** If you are mostly
   predicting `Phenotypic abnormality` and `Abnormality of the nervous
   system`, you have built a syntactic exercise.
10. **What is the inter-annotator agreement on your 1003 ground-truth rows?**
    One coder is not a gold standard. It is one opinion.

---

## Closing

There is a real tool here, and the architectural instinct to use the LLM as a
*judge over a constrained shortlist* rather than a *generator of HP codes* is
the correct one. That part of the design I would happily defend in print. But
the rest of it — the unpinned ontology, the misadvertised PII model, the
hop-count "similarity," the absent negation handling, the unmeasured F1 after
a month, the comparison only against the weakest LLM-based competitor — is the
kind of work the field gets a bad name for.

My recommendation is straightforward. Fix the ground-truth join. Measure F1
against GSC+ and BioCreative VIII. Benchmark against Fenominal. Replace
hop-count with Resnik IC. Add a negation field to the extraction schema.
Either ship the medical NER you claim or change the documentation. Then come
back.

Until then I would tell Marc, with respect, to keep coding his recordings by
hand and use this as a triage assistant whose every output he reviews. Which I
suspect is what he is already doing.

— *(mock)* P. R.
