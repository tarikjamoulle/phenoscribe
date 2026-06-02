"""Validation scorer — compare pipeline output against ground truth.

Term similarity uses information content (IC) of the most informative common
ancestor, following Resnik (1995) and Lin (1998), via hpo-toolkit ancestor
sets. The previous scorer counted is_a hops and walked the DAG undirected,
which had two faults Robinson flagged:

  (a) A near-root prediction such as "Phenotypic abnormality" scored 0.75
      against a specific truth because intermediate hops kept being added.
      Near-root terms carry almost no information, so under IC they now score
      ~0. See `phenoscribe.semantic_similarity`.
  (b) Undirected walking treated "predicted the parent of truth" (too general,
      a recall problem) the same as "predicted a child of truth" (fabricated
      specificity, a precision problem). Errors are now classified by
      direction and reported separately.

`hop_distance` is retained for the ontogpt benchmark script, which uses it
directly; the validation score no longer depends on it.
"""

import logging
from collections import deque

import hpotk

from phenoscribe.aggregate import load_patient_codes
from phenoscribe.semantic_similarity import (
    DIR_EXACT,
    DIR_NON_SPECIFIC,
    DIR_OVER_SPECIFIC,
    DIR_RELATED,
    DIR_UNRELATED,
    error_direction,
    get_ic_map,
    ic_distribution,
    lin_similarity,
    resnik_similarity,
)

logger = logging.getLogger(__name__)

_IC_CACHE = None

_HPO_CACHE = None


def _get_hpo():
    """Load HPO once per process (the auto-downloaded release is ~10MB and parsing takes a few seconds)."""
    global _HPO_CACHE
    if _HPO_CACHE is None:
        store = hpotk.configure_ontology_store()
        _HPO_CACHE = store.load_minimal_hpo()
        logger.info("Loaded HPO via hpo-toolkit, version %s", _HPO_CACHE.version)
    return _HPO_CACHE


def _get_ic(hpo) -> dict[str, float]:
    """Load the IC map once per process (built from phenotype.hpoa, cached on disk)."""
    global _IC_CACHE
    if _IC_CACHE is None:
        _IC_CACHE = get_ic_map(hpo)
    return _IC_CACHE


def load_codes_from_excel(path: str) -> dict[str, set[str]]:
    """Extract HPO codes per patient from an Excel file (any output format).

    Wraps `aggregate.load_patient_codes` and discards term names.
    Returns dict of patient_id -> set of HP codes.
    """
    rich = load_patient_codes(path)
    return {pid: {entry["hpo_id"] for entry in entries} for pid, entries in rich.items()}


def hop_distance(hpo, a: str, b: str, max_hops: int = 2) -> int | None:
    """Shortest is_a path between two HPO terms, treating the DAG as undirected.

    Returns None if the distance exceeds max_hops or either term is unknown.
    With max_hops=2 this captures: exact, parent/child, grandparent/grandchild,
    siblings (shared parent), and uncle-nephew (one step up then one step down).
    """
    if a == b:
        return 0
    seen = {a}
    queue = deque([(a, 0)])
    while queue:
        current, depth = queue.popleft()
        if depth >= max_hops:
            continue
        try:
            neighbours = [str(p) for p in hpo.graph.get_parents(current)]
            neighbours += [str(c) for c in hpo.graph.get_children(current)]
        except (KeyError, ValueError):
            continue
        for nxt in neighbours:
            if nxt in seen:
                continue
            new_depth = depth + 1
            if nxt == b:
                return new_depth
            seen.add(nxt)
            queue.append((nxt, new_depth))
    return None


def score_match(predicted: str, ground_truth: set[str], hpo, ic_map=None) -> float:
    """Score a predicted HPO code against a set of ground-truth codes.

    Uses the Lin (1998) normalised information-content similarity. For each
    ground-truth term, similarity is 2*IC(MICA)/(IC(pred)+IC(gt)); the best
    over all ground-truth terms is returned. An exact match scores 1.0. A
    near-root prediction against a specific truth scores ~0 because their
    most informative common ancestor carries almost no information.

    `ic_map` is loaded lazily if not supplied.
    """
    if predicted in ground_truth:
        return 1.0
    if ic_map is None:
        ic_map = _get_ic(hpo)
    best = 0.0
    for gt_code in ground_truth:
        score = lin_similarity(ic_map, hpo, predicted, gt_code)
        if score > best:
            best = score
    return best


def classify_prediction(predicted: str, ground_truth: set[str], hpo, ic_map=None) -> dict:
    """Score one prediction and classify its error direction against the best GT term.

    Returns a dict with the Lin score, the Resnik (raw IC) similarity, the
    matched ground-truth term, and a direction label (exact / non_specific /
    over_specific / related / unrelated). "Best" is chosen by Lin score, with
    a direct lineage relationship preferred on ties so that a true
    ancestor/descendant of a GT term is labelled on the correct side.
    """
    if ic_map is None:
        ic_map = _get_ic(hpo)

    best = None
    for gt_code in ground_truth:
        lin = lin_similarity(ic_map, hpo, predicted, gt_code)
        direction = error_direction(hpo, predicted, gt_code)
        resnik = resnik_similarity(ic_map, hpo, predicted, gt_code)
        # Prefer higher Lin score; on a tie prefer a direct-lineage relationship.
        lineage_rank = 1 if direction in (DIR_EXACT, DIR_NON_SPECIFIC, DIR_OVER_SPECIFIC) else 0
        key = (lin, lineage_rank, resnik)
        if best is None or key > best[0]:
            best = (key, gt_code, lin, resnik, direction)

    if best is None:
        return {
            "lin": 0.0,
            "resnik": 0.0,
            "matched_gt": None,
            "direction": DIR_UNRELATED,
        }
    _, gt_code, lin, resnik, direction = best
    return {
        "lin": lin,
        "resnik": resnik,
        "matched_gt": gt_code,
        "direction": direction,
    }


def validate(
    ground_truth_path: str,
    pipeline_output_path: str,
    _obo_path_ignored: str | None = None,
    **_legacy_kwargs,
) -> dict:
    """Compare pipeline output against ground truth. Returns validation report dict."""
    gt_codes = load_codes_from_excel(ground_truth_path)
    pred_codes = load_codes_from_excel(pipeline_output_path)
    hpo = _get_hpo()
    ic_map = _get_ic(hpo)

    patient_scores = {}
    all_patients = set(gt_codes.keys()) | set(pred_codes.keys())

    total_exact = 0
    total_close = 0
    total_missed = 0
    total_extra = 0
    total_gt = 0
    total_pred = 0
    # Directional error counts (Robinson issue #4b).
    total_non_specific = 0  # predicted an ancestor of truth (recall side)
    total_over_specific = 0  # predicted a descendant of truth (precision side)
    all_pred_codes: list[str] = []

    for pid in sorted(all_patients):
        gt = gt_codes.get(pid, set())
        pred = pred_codes.get(pid, set())
        total_gt += len(gt)
        total_pred += len(pred)
        all_pred_codes.extend(pred)

        scores = []
        matched_gt = set()
        non_specific = 0
        over_specific = 0
        for p_code in pred:
            result = classify_prediction(p_code, gt, hpo, ic_map)
            s = result["lin"] if result["direction"] != DIR_EXACT else 1.0
            scores.append(s)
            if result["direction"] == DIR_EXACT:
                total_exact += 1
                matched_gt.add(p_code)
            elif s > 0:
                total_close += 1
            if result["direction"] == DIR_NON_SPECIFIC:
                non_specific += 1
            elif result["direction"] == DIR_OVER_SPECIFIC:
                over_specific += 1

        total_non_specific += non_specific
        total_over_specific += over_specific

        missed = gt - matched_gt
        total_missed += len(missed)
        extra = sum(1 for s in scores if s == 0.0)
        total_extra += extra

        avg_score = sum(scores) / len(scores) if scores else 0.0
        precision = sum(1 for s in scores if s > 0) / len(pred) if pred else 0.0
        recall = len(matched_gt) / len(gt) if gt else 0.0

        patient_scores[pid] = {
            "gt_count": len(gt),
            "pred_count": len(pred),
            "avg_score": avg_score,
            "precision": precision,
            "recall": recall,
            "exact": sum(1 for s in scores if s == 1.0),
            "close": sum(1 for s in scores if 0 < s < 1.0),
            "missed": len(missed),
            "extra": extra,
            "non_specific": non_specific,
            "over_specific": over_specific,
        }

    precision = (total_exact + total_close) / total_pred if total_pred else 0.0
    recall = total_exact / total_gt if total_gt else 0.0

    return {
        "patients_evaluated": len(all_patients),
        "total_gt_codes": total_gt,
        "total_pred_codes": total_pred,
        "exact_matches": total_exact,
        "close_matches": total_close,
        "missed": total_missed,
        "extra": total_extra,
        "non_specific": total_non_specific,
        "over_specific": total_over_specific,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0,
        "ic_distribution": ic_distribution(ic_map, all_pred_codes),
        "per_patient": patient_scores,
    }


def print_report(report: dict) -> None:
    """Print a formatted validation report."""
    print("=" * 60)
    print("PHENOSCRIBE VALIDATION REPORT")
    print("=" * 60)
    print(f"Patients evaluated: {report['patients_evaluated']}")
    print(f"Ground truth codes: {report['total_gt_codes']}")
    print(f"Predicted codes:    {report['total_pred_codes']}")
    print()
    print(f"Exact matches:  {report['exact_matches']}")
    print(f"Close matches:  {report['close_matches']}")
    print(f"Missed:         {report['missed']}")
    print(f"Extra:          {report['extra']}")
    print()
    print("Directional errors:")
    print(f"  Non-specific (predicted ancestor of truth, recall side):    {report.get('non_specific', 0)}")
    print(f"  Over-specific (predicted descendant of truth, precision side): {report.get('over_specific', 0)}")
    print()
    print(f"Precision: {report['precision']:.1%}")
    print(f"Recall:    {report['recall']:.1%}")
    print(f"F1:        {report['f1']:.1%}")
    print()

    ic = report.get("ic_distribution")
    if ic:
        print("Predicted-term IC distribution (Q9):")
        print(
            f"  n={ic['count']}  min={ic['min']:.2f}  median={ic['median']:.2f}  "
            f"mean={ic['mean']:.2f}  max={ic['max']:.2f}"
        )
        print(
            f"  low-IC (<{ic['low_ic_threshold']:.1f}) terms: {ic['low_ic_count']}/{ic['count']} "
            f"({ic['low_ic_fraction']:.1%})"
        )
        if ic["low_ic_dominated"]:
            print("  FLAG: predictions are dominated by low-IC near-root terms.")
        print()

    print("Per-patient breakdown:")
    print(
        f"{'Patient':<12} {'GT':>4} {'Pred':>4} {'Exact':>5} {'Close':>5} "
        f"{'Miss':>5} {'Extra':>5} {'NonSp':>5} {'OvSp':>5} {'Score':>6}"
    )
    print("-" * 72)
    for pid, ps in sorted(report["per_patient"].items()):
        print(
            f"{pid:<12} {ps['gt_count']:>4} {ps['pred_count']:>4} "
            f"{ps['exact']:>5} {ps['close']:>5} {ps['missed']:>5} {ps['extra']:>5} "
            f"{ps.get('non_specific', 0):>5} {ps.get('over_specific', 0):>5} "
            f"{ps['avg_score']:>5.1%}"
        )
