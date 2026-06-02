"""Validation scorer — compare pipeline output against ground truth.

Hierarchy walks use hpo-toolkit (recommended by Peter Robinson) so that
sibling and uncle/nephew relationships score correctly via a shared
ancestor, which the previous strictly-up/strictly-down walk missed.
"""

import logging
import re
from collections import deque

import hpotk
import openpyxl

from phenoscribe.aggregate import load_patient_codes

logger = logging.getLogger(__name__)

_HP_CODE = re.compile(r"HP:\d{7}")

_HPO_CACHE = None


def _get_hpo():
    """Load HPO once per process (the auto-downloaded release is ~10MB and parsing takes a few seconds)."""
    global _HPO_CACHE
    if _HPO_CACHE is None:
        store = hpotk.configure_ontology_store()
        _HPO_CACHE = store.load_minimal_hpo()
        logger.info("Loaded HPO via hpo-toolkit, version %s", _HPO_CACHE.version)
    return _HPO_CACHE


def _raw_codes_by_patient(path: str) -> dict[str, set[str]]:
    """Scan a two-column (Patient_ID, observation) workbook for bare HP codes.

    Marc Jamoulle's ground truth mixes delimiter styles between patients:
    "Term (HP:0001279)", "Term|HP:0001279|verbatim", and
    "Term/HP:0001279[verbatim]" all appear. The structured semicolon parser
    only reads the parenthesised style. For scoring we need the set of HP
    codes per patient, so pull every HP:####### token from the row regardless
    of the surrounding format.
    """
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb.active
    result: dict[str, set[str]] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        pid = str(row[0])
        text = " ".join(str(c) for c in row[1:] if c)
        codes = set(_HP_CODE.findall(text))
        if codes:
            result.setdefault(pid, set()).update(codes)
    return result


def load_codes_from_excel(path: str) -> dict[str, set[str]]:
    """Extract HPO codes per patient from an Excel file (any output format).

    Wraps `aggregate.load_patient_codes` and discards term names. Falls back
    to a raw HP-code scan for any patient the structured parser missed, so
    mixed-delimiter ground-truth rows still score.
    Returns dict of patient_id -> set of HP codes.
    """
    rich = load_patient_codes(path)
    codes = {pid: {entry["hpo_id"] for entry in entries} for pid, entries in rich.items()}

    raw = _raw_codes_by_patient(path)
    for pid, raw_codes in raw.items():
        if not codes.get(pid):
            codes[pid] = raw_codes
    return codes


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
    _obo_path_ignored: str | None = None,
    **_legacy_kwargs,
) -> dict:
    """Compare pipeline output against ground truth. Returns validation report dict."""
    gt_codes = load_codes_from_excel(ground_truth_path)
    pred_codes = load_codes_from_excel(pipeline_output_path)
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

    # Partial-credit score: close (hierarchy-near) predictions count toward
    # precision; recall counts only exact hits. This is the lenient view.
    precision = (total_exact + total_close) / total_pred if total_pred else 0.0
    recall = total_exact / total_gt if total_gt else 0.0

    # Strict document-level F1: a true positive is an exact HPO ID match,
    # no hierarchy expansion. This is the convention used in HPO
    # concept-recognition benchmarks (Groza et al., FastHPOCR), where a
    # document-level TP is "the gold HPO ID is found at least once".
    # TP = |pred ∩ gt| summed over patients (== total_exact, since each
    # exact prediction matched a distinct GT code).
    strict_tp = total_exact
    strict_precision = strict_tp / total_pred if total_pred else 0.0
    strict_recall = strict_tp / total_gt if total_gt else 0.0
    strict_f1 = (
        2 * strict_precision * strict_recall / (strict_precision + strict_recall)
        if (strict_precision + strict_recall)
        else 0.0
    )

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
        # Strict exact-match document-level metrics.
        "strict_precision": strict_precision,
        "strict_recall": strict_recall,
        "strict_f1": strict_f1,
        "strict_tp": strict_tp,
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
    print("Strict (exact HPO ID match, document-level):")
    print(f"  Precision: {report['strict_precision']:.1%}")
    print(f"  Recall:    {report['strict_recall']:.1%}")
    print(f"  F1:        {report['strict_f1']:.1%}")
    print()
    print("Partial credit (hierarchy-near within 2 hops counts for precision):")
    print(f"  Precision: {report['precision']:.1%}")
    print(f"  Recall:    {report['recall']:.1%}")
    print(f"  F1:        {report['f1']:.1%}")
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
