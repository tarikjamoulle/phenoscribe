"""Validation scorer — compare pipeline output against ground truth.

Hierarchy walks use hpo-toolkit (recommended by Peter Robinson) so that
sibling and uncle/nephew relationships score correctly via a shared
ancestor, which the previous strictly-up/strictly-down walk missed.
"""

import logging
from collections import deque

import hpotk

from phenoscribe.aggregate import load_patient_codes
from phenoscribe.config import load_config
from phenoscribe.hpo_index import build_obsolete_map, resolve_obsolete

logger = logging.getLogger(__name__)

_HPO_CACHE = None
_OBSOLETE_MAP_CACHE: dict[str, dict[str, str]] = {}


def _get_hpo():
    """Load HPO once per process (the auto-downloaded release is ~10MB and parsing takes a few seconds)."""
    global _HPO_CACHE
    if _HPO_CACHE is None:
        store = hpotk.configure_ontology_store()
        _HPO_CACHE = store.load_minimal_hpo()
        logger.info("Loaded HPO via hpo-toolkit, version %s", _HPO_CACHE.version)
    return _HPO_CACHE


def _get_obsolete_map(obo_path: str) -> dict[str, str]:
    """Load and cache the retired-id -> active-id map for an obo file."""
    if obo_path not in _OBSOLETE_MAP_CACHE:
        _OBSOLETE_MAP_CACHE[obo_path] = build_obsolete_map(obo_path)
    return _OBSOLETE_MAP_CACHE[obo_path]


def load_codes_from_excel(
    path: str, obsolete_map: dict[str, str] | None = None
) -> dict[str, set[str]]:
    """Extract HPO codes per patient from an Excel file (any output format).

    Wraps `aggregate.load_patient_codes` and discards term names.
    Returns dict of patient_id -> set of HP codes.

    If ``obsolete_map`` is given, retired ids (obsolete-with-replaced_by, or
    merged ids carried as alt_id) are resolved to their active id. This keeps an
    obsolete ground-truth code from silently failing to match a current
    prediction.
    """
    rich = load_patient_codes(path)

    def resolve(hpo_id: str) -> str:
        return resolve_obsolete(hpo_id, obsolete_map) if obsolete_map else hpo_id

    return {
        pid: {resolve(entry["hpo_id"]) for entry in entries}
        for pid, entries in rich.items()
    }


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


def score_match(predicted: str, ground_truth: set[str], hpo) -> float:
    """Score a predicted HPO code against a set of ground-truth codes.

    1.0 exact, 0.75 one hop, 0.5 two hops, 0 otherwise.
    """
    if predicted in ground_truth:
        return 1.0

    best = 0.0
    for gt_code in ground_truth:
        d = hop_distance(hpo, predicted, gt_code, max_hops=2)
        if d == 1:
            score = 0.75
        elif d == 2:
            score = 0.5
        else:
            score = 0.0
        if score > best:
            best = score
    return best


def validate(
    ground_truth_path: str,
    pipeline_output_path: str,
    obo_path: str | None = None,
    **_legacy_kwargs,
) -> dict:
    """Compare pipeline output against ground truth. Returns validation report dict.

    Retired HPO ids on either side are resolved to their active id (via the obo
    at ``obo_path``, defaulting to config.paths.hpo_obo) so an obsolete
    ground-truth code is scored against its replacement instead of being dropped.
    """
    if obo_path is None:
        obo_path = load_config().paths.hpo_obo
    obsolete_map = _get_obsolete_map(obo_path)

    gt_codes = load_codes_from_excel(ground_truth_path, obsolete_map)
    pred_codes = load_codes_from_excel(pipeline_output_path, obsolete_map)
    hpo = _get_hpo()

    patient_scores = {}
    all_patients = set(gt_codes.keys()) | set(pred_codes.keys())

    total_exact = 0
    total_close = 0
    total_missed = 0
    total_extra = 0
    total_gt = 0
    total_pred = 0

    for pid in sorted(all_patients):
        gt = gt_codes.get(pid, set())
        pred = pred_codes.get(pid, set())
        total_gt += len(gt)
        total_pred += len(pred)

        scores = []
        matched_gt = set()
        for p_code in pred:
            s = score_match(p_code, gt, hpo)
            scores.append(s)
            if s == 1.0:
                total_exact += 1
                matched_gt.add(p_code)
            elif s > 0:
                total_close += 1

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
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0,
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
    print(f"Precision: {report['precision']:.1%}")
    print(f"Recall:    {report['recall']:.1%}")
    print(f"F1:        {report['f1']:.1%}")
    print()

    print("Per-patient breakdown:")
    print(f"{'Patient':<12} {'GT':>4} {'Pred':>4} {'Exact':>5} {'Close':>5} {'Miss':>5} {'Extra':>5} {'Score':>6}")
    print("-" * 60)
    for pid, ps in sorted(report["per_patient"].items()):
        print(
            f"{pid:<12} {ps['gt_count']:>4} {ps['pred_count']:>4} "
            f"{ps['exact']:>5} {ps['close']:>5} {ps['missed']:>5} {ps['extra']:>5} "
            f"{ps['avg_score']:>5.1%}"
        )
