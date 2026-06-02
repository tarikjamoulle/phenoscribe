"""Benchmark Phenoscribe on the GSC+ gold standard, against FastHPOCR.

GSC+ (Groza et al. 2015, extended by Lobo et al. 2017; "GSC-2024" refresh by
Groza, distributed with the FastHPOCR paper) is the standard corpus for HPO
phenotype concept recognition. 228 PubMed abstracts with mention-level HPO
annotations. This is the corpus FastHPOCR, PhenoTagger, Doc2HPO, ClinPhen,
NCBO/OBO Annotator and the Monarch tagger all report on.

What this script measures
-------------------------
Three systems, all scored document-level (the FastHPOCR paper's primary
metric: presence/absence of an HPO concept ID per document, boundaries
ignored):

1. Phenoscribe retrieval-only — for each document, feed the GOLD mention
   strings into ChromaDB and take the top-1 HP code. No LLM, no cost. This
   isolates the embedding retriever and gives an upper bound on what the
   judge can pick from. Also reports recall@k (gold ID anywhere in the
   top-k shortlist).

2. Phenoscribe full pipeline (SAMPLED, live LLM) — on a stated sample of
   documents, run the real two-call pipeline end-to-end on the abstract:
   extract_symptoms (LLM call 1) then match_hpo (ChromaDB + LLM judge, call
   2). This is the honest end-to-end number. Sampled to bound API cost.

3. FastHPOCR — end-to-end dictionary recognition on the same documents.

Scoring
-------
Primary: exact HP-ID match at document level (P / R / F1), same as the
FastHPOCR paper. Secondary: lenient match where a predicted code counts if
it is within `--lenient-hops` of a gold code in the HPO graph (hpo-toolkit),
reported separately so it is never confused with the exact number.

Usage
-----
    cd <worktree>
    export ANTHROPIC_API_KEY=...   # only needed for --full-sample > 0
    PYTHONPATH=$PWD/src <venv>/bin/python scripts/benchmark_gsc.py \
        --gsc-dir .tmp/GSC_2024 \
        --fasthpocr-index .tmp/fasthpocr_index/hp.index \
        --chroma-path /Users/tarikjamoulle/projects/hpo_identifier/data/chroma_db \
        --full-sample 6 \
        --provider anthropic --model claude-sonnet-4-6 \
        --out context/exports/2026-06-03-gold-standard-benchmark.md

Set --full-sample 0 to skip all live LLM calls (retrieval-only + FastHPOCR).
"""

import argparse
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

logger = logging.getLogger("benchmark_gsc")

# FastHPOCR emits OBO PURLs (http://purl.obolibrary.org/obo/HP_0001234).
_PURL_RE = re.compile(r"HP[_:](\d{7})")


def normalize_hp(raw: str) -> str | None:
    """Normalize any HP identifier form (PURL, HP_, HP:) to canonical HP:nnnnnnn."""
    m = _PURL_RE.search(raw or "")
    return f"HP:{m.group(1)}" if m else None


@dataclass
class GscDoc:
    pmid: str
    text: str
    gold_ids: set[str]
    gold_mentions: list[tuple[str, str]] = field(default_factory=list)  # (text_span, hp_id)


def load_gsc(gsc_dir: Path) -> list[GscDoc]:
    """Load the GSC+ corpus from the extracted GSC_2024 directory.

    Text/<pmid> holds the abstract. Annotations/<pmid> holds tab-separated
    rows: 'start:end<TAB>HP:code<TAB>mention text'.
    """
    text_dir = gsc_dir / "Text"
    ann_dir = gsc_dir / "Annotations"
    docs = []
    for text_file in sorted(text_dir.iterdir()):
        if not text_file.is_file() or text_file.name.startswith("."):
            continue
        pmid = text_file.name
        ann_file = ann_dir / pmid
        if not ann_file.exists():
            continue
        gold_ids: set[str] = set()
        mentions: list[tuple[str, str]] = []
        for line in ann_file.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            hp = normalize_hp(parts[1])
            if not hp:
                continue
            gold_ids.add(hp)
            span = parts[2] if len(parts) > 2 else ""
            mentions.append((span, hp))
        docs.append(
            GscDoc(
                pmid=pmid,
                text=text_file.read_text(encoding="utf-8", errors="replace"),
                gold_ids=gold_ids,
                gold_mentions=mentions,
            )
        )
    return docs


# --------------------------------------------------------------------------
# Scoring (document-level, set-based)
# --------------------------------------------------------------------------


def _lenient_hit(pred: str, gold: set[str], hpo, max_hops: int) -> bool:
    from phenoscribe.validate import hop_distance

    if pred in gold:
        return True
    for g in gold:
        d = hop_distance(hpo, pred, g, max_hops=max_hops)
        if d is not None and d <= max_hops:
            return True
    return False


def score_documents(per_doc_pred: dict[str, set[str]], docs: list[GscDoc],
                    hpo=None, lenient_hops: int = 0) -> dict:
    """Document-level micro P/R/F1 over a set of documents.

    Exact mode (lenient_hops=0): a predicted ID is a TP iff it is in the gold
    set for that document. Lenient mode: a predicted ID is a TP iff it is
    within `lenient_hops` of any gold ID; recall credits a gold ID if any
    prediction is within range.
    """
    tp = fp = fn = 0
    per_doc = {}
    for d in docs:
        pred = per_doc_pred.get(d.pmid, set())
        gold = d.gold_ids
        if lenient_hops and hpo is not None:
            doc_tp = sum(1 for p in pred if _lenient_hit(p, gold, hpo, lenient_hops))
            doc_fp = len(pred) - doc_tp
            matched_gold = sum(
                1 for g in gold
                if any(_lenient_hit(p, {g}, hpo, lenient_hops) for p in pred)
            )
            doc_fn = len(gold) - matched_gold
        else:
            inter = pred & gold
            doc_tp = len(inter)
            doc_fp = len(pred - gold)
            doc_fn = len(gold - pred)
        tp += doc_tp
        fp += doc_fp
        fn += doc_fn
        p = doc_tp / (doc_tp + doc_fp) if (doc_tp + doc_fp) else 0.0
        r = doc_tp / (doc_tp + doc_fn) if (doc_tp + doc_fn) else 0.0
        per_doc[d.pmid] = {
            "pred": len(pred), "gold": len(gold),
            "tp": doc_tp, "fp": doc_fp, "fn": doc_fn,
            "precision": p, "recall": r,
        }
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": precision, "recall": recall, "f1": f1,
        "per_doc": per_doc,
    }


# --------------------------------------------------------------------------
# System 1: Phenoscribe retrieval-only (gold mentions -> ChromaDB)
# --------------------------------------------------------------------------


def run_phenoscribe_retrieval(docs: list[GscDoc], chroma_path: str, k: int) -> dict:
    """Feed gold mention strings into ChromaDB; collect top-1 IDs per document.

    Also returns recall@k: fraction of gold mentions whose gold HP ID appears
    anywhere in the top-k shortlist. recall@k is the ceiling for the LLM judge,
    which can only pick a code that retrieval surfaced.
    """
    from phenoscribe.hpo_index import search_hpo

    per_doc_pred: dict[str, set[str]] = {}
    mention_total = 0
    mention_top1_hit = 0
    mention_topk_hit = 0
    cache: dict[str, list[dict]] = {}
    for d in docs:
        preds: set[str] = set()
        for span, gold_hp in d.gold_mentions:
            q = span.strip()
            if not q:
                continue
            if q not in cache:
                cache[q] = search_hpo(q, k=k, chroma_path=chroma_path)
            cands = cache[q]
            if not cands:
                continue
            mention_total += 1
            preds.add(cands[0]["hpo_id"])
            if cands[0]["hpo_id"] == gold_hp:
                mention_top1_hit += 1
            if any(c["hpo_id"] == gold_hp for c in cands):
                mention_topk_hit += 1
        per_doc_pred[d.pmid] = preds
    return {
        "per_doc_pred": per_doc_pred,
        "mention_total": mention_total,
        "mention_top1_acc": mention_top1_hit / mention_total if mention_total else 0.0,
        "mention_recall_at_k": mention_topk_hit / mention_total if mention_total else 0.0,
        "k": k,
    }


# --------------------------------------------------------------------------
# System 2: Phenoscribe full pipeline (extract -> retrieve -> judge), SAMPLED
# --------------------------------------------------------------------------


def run_phenoscribe_full(docs: list[GscDoc], chroma_path: str, k: int,
                        provider: str, model: str) -> dict:
    """Run the real two-call pipeline on each abstract. Live LLM calls."""
    from phenoscribe.extract_symptoms import extract_symptoms
    from phenoscribe.match_hpo import match_hpo

    per_doc_pred: dict[str, set[str]] = {}
    for d in docs:
        try:
            symptoms = extract_symptoms(d.text, provider=provider, model=model)
            matched = match_hpo(symptoms, provider=provider, model=model,
                                chroma_path=chroma_path, k=k)
            per_doc_pred[d.pmid] = {m["hpo_id"] for m in matched}
        except Exception as e:
            logger.error("[%s] full pipeline failed: %s", d.pmid, e)
            per_doc_pred[d.pmid] = set()
        logger.info("[%s] full pipeline -> %d codes", d.pmid,
                    len(per_doc_pred[d.pmid]))
    return {"per_doc_pred": per_doc_pred}


# --------------------------------------------------------------------------
# System 3: FastHPOCR
# --------------------------------------------------------------------------


def run_fasthpocr(docs: list[GscDoc], index_path: str) -> dict:
    """End-to-end FastHPOCR concept recognition on each abstract."""
    from FastHPOCR.HPOAnnotator import HPOAnnotator

    annotator = HPOAnnotator(index_path)
    per_doc_pred: dict[str, set[str]] = {}
    for d in docs:
        preds: set[str] = set()
        for ann in annotator.annotate(d.text):
            hp = normalize_hp(ann.getHPOUri())
            if hp:
                preds.add(hp)
        per_doc_pred[d.pmid] = preds
    return {"per_doc_pred": per_doc_pred}


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def _row(name, s):
    return (f"| {name} | {s['precision']:.3f} | {s['recall']:.3f} | "
            f"{s['f1']:.3f} | {s['tp']} | {s['fp']} | {s['fn']} |")


def write_report(out_path: Path, *, all_docs, eval_docs, full_docs,
                retr, retr_exact, retr_len,
                fh, fh_exact, fh_len,
                full, full_exact, full_len,
                k, lenient_hops, provider, model, hpo_version):
    n_all = len(all_docs)
    n_eval = len(eval_docs)
    n_full = len(full_docs)
    total_gold = sum(len(d.gold_ids) for d in eval_docs)

    lines = [
        "---",
        f"date: {date.today().isoformat()}",
        "type: benchmark",
        "corpus: GSC+ (GSC-2024 refresh, Groza et al.)",
        "---",
        "",
        "# Phenoscribe on a published gold standard (GSC+), vs FastHPOCR",
        "",
        "## What this answers",
        "",
        "Robinson issue #7 / Robinson Test Q3: the only prior benchmark was vs "
        "OntoGPT. The field reports on GSC+ against FastHPOCR, PhenoTagger, "
        "Doc2HPO, ClinPhen, NCBO/OBO Annotator and the Monarch tagger. This "
        "puts Phenoscribe on that corpus and runs FastHPOCR head-to-head on "
        "the same documents.",
        "",
        "## What actually ran here vs what is cited",
        "",
        f"- **Ran:** Phenoscribe retrieval (ChromaDB) on all {n_eval} GSC+ "
        f"documents; FastHPOCR on the same {n_eval} documents; Phenoscribe "
        f"full two-call pipeline on a sample of {n_full} documents.",
        "- **Cited (not re-run here):** published GSC+ F1 for FastHPOCR, "
        "PhenoTagger, Doc2HPO, ClinPhen, NCBO/OBO Annotator, Monarch (Groza "
        "2024, Bioinformatics, Table 1). Fenominal is the Java port of "
        "FastHPOCR and is not run separately here.",
        "- **BioCreative VIII Task 3:** now public "
        "(github.com/Ian-Campbell-Lab/Clinical-Genetics-Training-Data). It is "
        "a different task — span normalisation of dysmorphology physical-exam "
        "observations, not free-text concept recognition over abstracts — so "
        "it is not directly comparable to the GSC+ document-level metric. "
        "Documented, not run.",
        "",
        "## Corpus and versions",
        "",
        f"- GSC+ / GSC-2024: {n_all} abstracts, {sum(len(d.gold_ids) for d in all_docs)} "
        f"distinct gold HP codes total. Mention-level annotations, scored here "
        f"at document level (presence/absence of a concept ID), matching the "
        f"FastHPOCR paper's primary metric.",
        f"- HPO release for scoring graph walks (hpo-toolkit): {hpo_version}.",
        f"- ChromaDB: shared seeded index, ~19k terms. Retrieval k={k}.",
        f"- FastHPOCR {_fasthpocr_version()} indexed from the same hp.obo as ChromaDB.",
        "",
        "## Results — exact document-level HP-ID match (primary metric)",
        "",
        f"Scored on {n_eval} documents ({total_gold} gold codes). TP/FP/FN are "
        "micro-summed over documents.",
        "",
        "| System | Precision | Recall | F1 | TP | FP | FN |",
        "|---|---|---|---|---|---|---|",
        _row("FastHPOCR (this run)", fh_exact),
        _row(f"Phenoscribe retrieval top-1 (gold mentions, k={k})", retr_exact),
    ]
    if full_exact is not None:
        lines.append(_row(f"Phenoscribe full pipeline (sample n={n_full}, {model})",
                         full_exact))
    lines += [
        "",
        "## Results — lenient match "
        f"(predicted within {lenient_hops} HPO hops of a gold code)",
        "",
        "Reported separately. The FastHPOCR paper uses exact IDs; lenient "
        "credit is shown so near-misses (parent/child/sibling) are visible, "
        "never folded into the exact number.",
        "",
        "| System | Precision | Recall | F1 | TP | FP | FN |",
        "|---|---|---|---|---|---|---|",
        _row("FastHPOCR (this run)", fh_len),
        _row(f"Phenoscribe retrieval top-1 (k={k})", retr_len),
    ]
    if full_len is not None:
        lines.append(_row(f"Phenoscribe full pipeline (sample n={n_full})", full_len))
    lines += [
        "",
        "## Retriever ceiling (no LLM)",
        "",
        f"On {retr['mention_total']} gold mention strings fed straight into "
        "ChromaDB:",
        "",
        f"- top-1 exact-ID accuracy: **{retr['mention_top1_acc']:.1%}**",
        f"- recall@{k} (gold ID anywhere in the shortlist): "
        f"**{retr['mention_recall_at_k']:.1%}**",
        "",
        f"recall@{k} is the ceiling for the LLM judge: it can only return a "
        f"code retrieval surfaced. The gap between top-1 and recall@{k} is the "
        "room the judge has to improve on raw nearest-neighbour.",
        "",
        "## Published GSC+ numbers (cited, document-level F1)",
        "",
        "From Groza et al. 2024 (Bioinformatics, FastHPOCR), Table 1, GSC+ "
        "aligned to the 2019-11 HPO release. Exact concept-ID match.",
        "",
        "| System | Precision | Recall | F1 |",
        "|---|---|---|---|",
        "| FastHPOCR | 0.82 | 0.71 | 0.76 |",
        "| PhenoTagger | 0.77 | 0.67 | 0.72 |",
        "| OBO Annotator | 0.80 | 0.56 | 0.66 |",
        "| Monarch | 0.75 | 0.60 | 0.67 |",
        "| ClinPhen | 0.63 | 0.65 | 0.64 |",
        "| Doc2HPO | 0.80 | 0.49 | 0.61 |",
        "| NCBO Annotator | 0.66 | 0.49 | 0.56 |",
        "",
        "These rows are not re-runs; they are the published figures, included "
        "so the Phenoscribe and FastHPOCR rows above sit in the field's "
        "context. Our FastHPOCR row is a fresh run on a newer HPO release and "
        "the GSC-2024 corpus refresh, so it will not match the 0.76 cell "
        "exactly.",
        "",
        "## Honest read",
        "",
        _verdict(fh_exact, retr_exact, full_exact, retr, k, n_full),
        "",
        "## Method",
        "",
        "1. Load GSC+ (GSC-2024). For each abstract collect the set of gold HP "
        "IDs from its annotation file.",
        "2. FastHPOCR: index hp.obo once, annotate each abstract, collect HP "
        "IDs. End-to-end.",
        "3. Phenoscribe retrieval: feed each gold mention string into ChromaDB "
        "(top-k), take top-1 as the predicted code; also record whether the "
        "gold code was anywhere in the shortlist (recall@k).",
        "4. Phenoscribe full pipeline (sampled): run extract_symptoms (LLM call "
        "1) then match_hpo (ChromaDB + LLM judge, call 2) on the raw abstract.",
        "5. Score document-level micro P/R/F1, exact and lenient.",
        "",
        "## Reproduce",
        "",
        "```",
        "pip install FastHPOCR pronto",
        "# download GSC-2024:",
        "#   github.com/tudorgroza/code-for-papers/tree/main/gsc-2024",
        "PYTHONPATH=$PWD/src python scripts/benchmark_gsc.py \\",
        "    --gsc-dir .tmp/GSC_2024 \\",
        "    --fasthpocr-index .tmp/fasthpocr_index/hp.index \\",
        f"    --chroma-path <shared chroma_db> --full-sample {n_full} \\",
        f"    --provider {provider} --model {model}",
        "```",
        "",
        "## Robinson Test",
        "",
        "Moves Q3 (\"benchmarked against the field's references on a published "
        "gold standard?\"): Phenoscribe is now scored on GSC+ with FastHPOCR "
        "run head-to-head on the same documents, and placed against the "
        "published GSC+ matrix.",
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))
    logger.info("wrote report to %s", out_path)


def _fasthpocr_version() -> str:
    try:
        from importlib.metadata import version
        return f"v{version('fasthpocr')}"
    except Exception:
        return "(version unknown)"


def _verdict(fh_exact, retr_exact, full_exact, retr, k, n_full) -> str:
    parts = []
    parts.append(
        f"On this corpus the 30MB offline FastHPOCR dictionary scores "
        f"F1={fh_exact['f1']:.3f} (P={fh_exact['precision']:.3f}, "
        f"R={fh_exact['recall']:.3f}) end-to-end."
    )
    parts.append(
        f"Phenoscribe's retriever, handed the gold mention strings, gets "
        f"top-1 exact accuracy {retr['mention_top1_acc']:.1%} and "
        f"recall@{k} {retr['mention_recall_at_k']:.1%}."
    )
    if full_exact is not None:
        parts.append(
            f"The full sampled pipeline scores F1={full_exact['f1']:.3f} "
            f"(P={full_exact['precision']:.3f}, R={full_exact['recall']:.3f}) "
            f"on n={n_full} documents — too small to rank against the "
            f"published matrix, a directional signal only."
        )
    parts.append(
        "Phenoscribe was built for spontaneous French speech, where a patient "
        "says \"je suis vidé\" and means fatigue; GSC+ is English scientific "
        "abstracts written in near-ontology language, which is exactly where a "
        "morphological dictionary like FastHPOCR is strongest. Read these "
        "numbers as Phenoscribe playing an away game on the dictionary's home "
        "ground, not as a verdict on the clinical-interview task it targets."
    )
    return " ".join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gsc-dir", required=True)
    ap.add_argument("--fasthpocr-index", required=True)
    ap.add_argument("--chroma-path", required=True)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--lenient-hops", type=int, default=2)
    ap.add_argument("--doc-limit", type=int, default=None,
                    help="Evaluate only the first N documents (for a quick run)")
    ap.add_argument("--full-sample", type=int, default=0,
                    help="Run the live LLM pipeline on this many sampled docs")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--provider", default="anthropic")
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--out", default="context/exports/2026-06-03-gold-standard-benchmark.md")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")

    all_docs = load_gsc(Path(args.gsc_dir))
    logger.info("loaded %d GSC+ documents", len(all_docs))
    eval_docs = all_docs[: args.doc_limit] if args.doc_limit else all_docs

    from phenoscribe.validate import _get_hpo
    hpo = _get_hpo()
    hpo_version = hpo.version

    logger.info("running FastHPOCR ...")
    fh = run_fasthpocr(eval_docs, args.fasthpocr_index)
    fh_exact = score_documents(fh["per_doc_pred"], eval_docs)
    fh_len = score_documents(fh["per_doc_pred"], eval_docs, hpo, args.lenient_hops)

    logger.info("running Phenoscribe retrieval ...")
    retr = run_phenoscribe_retrieval(eval_docs, args.chroma_path, args.k)
    retr_exact = score_documents(retr["per_doc_pred"], eval_docs)
    retr_len = score_documents(retr["per_doc_pred"], eval_docs, hpo, args.lenient_hops)

    full_docs: list[GscDoc] = []
    full_exact = full_len = None
    if args.full_sample > 0:
        rng = random.Random(args.seed)
        full_docs = rng.sample(eval_docs, min(args.full_sample, len(eval_docs)))
        logger.info("running Phenoscribe full pipeline on %d sampled docs ...",
                    len(full_docs))
        full = run_phenoscribe_full(full_docs, args.chroma_path, args.k,
                                    args.provider, args.model)
        full_exact = score_documents(full["per_doc_pred"], full_docs)
        full_len = score_documents(full["per_doc_pred"], full_docs, hpo,
                                   args.lenient_hops)

    write_report(
        Path(args.out),
        all_docs=all_docs, eval_docs=eval_docs, full_docs=full_docs,
        retr=retr, retr_exact=retr_exact, retr_len=retr_len,
        fh=fh, fh_exact=fh_exact, fh_len=fh_len,
        full=None, full_exact=full_exact, full_len=full_len,
        k=args.k, lenient_hops=args.lenient_hops,
        provider=args.provider, model=args.model, hpo_version=hpo_version,
    )

    print("\n=== EXACT document-level F1 ===")
    print(f"FastHPOCR:              P={fh_exact['precision']:.3f} "
          f"R={fh_exact['recall']:.3f} F1={fh_exact['f1']:.3f}")
    print(f"Phenoscribe retr top-1: P={retr_exact['precision']:.3f} "
          f"R={retr_exact['recall']:.3f} F1={retr_exact['f1']:.3f}")
    if full_exact is not None:
        print(f"Phenoscribe full (n={len(full_docs)}):  "
              f"P={full_exact['precision']:.3f} R={full_exact['recall']:.3f} "
              f"F1={full_exact['f1']:.3f}")
    print(f"retriever top-1 acc={retr['mention_top1_acc']:.1%} "
          f"recall@{args.k}={retr['mention_recall_at_k']:.1%}")


if __name__ == "__main__":
    main()
