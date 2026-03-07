"""Validation scorer — compare pipeline output against ground truth."""

import logging
import re
from collections import defaultdict

import openpyxl

from phenoscribe.hpo_index import parse_obo, build_hierarchy

logger = logging.getLogger(__name__)

HP_CODE_PATTERN = re.compile(r"HP:\d{7}")


def load_codes_from_excel(path: str) -> dict[str, set[str]]:
    """Extract HPO codes per patient from an Excel file.

    Handles both semicolon format and PURL format.
    Returns dict of patient_id -> set of HP codes.
    """
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    headers = [cell.value for cell in ws[1]]

    patient_codes: dict[str, set[str]] = defaultdict(set)

    # Detect format
    if "HPO Code Purl" in headers:
        # PURL format: one row per code
        id_col = headers.index("CASE ID")
        purl_col = headers.index("HPO Code Purl")
        for row in ws.iter_rows(min_row=2, values_only=True):
            pid = row[id_col]
            purl = row[purl_col]
            if pid and purl:
                # Extract HP code from PURL: http://purl.obolibrary.org/obo/HP_0002027
                match = re.search(r"HP_(\d{7})", str(purl))
                if match:
                    patient_codes[str(pid)].add(f"HP:{match.group(1)}")
    else:
        # Semicolon format: all codes in observation_source_value
        for row in ws.iter_rows(min_row=2, values_only=True):
            pid = row[0]
            obs = str(row[1]) if row[1] else ""
            if pid:
                codes = set(HP_CODE_PATTERN.findall(obs))
                if codes:
                    patient_codes[str(pid)] = codes

    return dict(patient_codes)


def get_ancestors(hpo_id: str, hierarchy: dict[str, list[str]], max_depth: int = 2) -> dict[str, int]:
    """Get ancestors of an HPO term up to max_depth.

    Returns dict of ancestor_id -> distance.
    """
    ancestors = {}
    frontier = [(hpo_id, 0)]
    visited = {hpo_id}

    while frontier:
        current, depth = frontier.pop(0)
        if depth > 0:
            ancestors[current] = depth
        if depth < max_depth:
            for parent in hierarchy.get(current, []):
                if parent not in visited:
                    visited.add(parent)
                    frontier.append((parent, depth + 1))

    return ancestors


def score_match(
    predicted: str, ground_truth: set[str], hierarchy: dict[str, list[str]]
) -> float:
    """Score a single predicted HPO code against ground truth codes.

    Returns:
        1.0 for exact match
        0.75 for parent/child (1 hop)
        0.5 for grandparent/grandchild (2 hops)
        0.0 otherwise
    """
    if predicted in ground_truth:
        return 1.0

    # Check if predicted is an ancestor of any ground truth code
    for gt_code in ground_truth:
        ancestors = get_ancestors(gt_code, hierarchy, max_depth=2)
        if predicted in ancestors:
            dist = ancestors[predicted]
            return 0.75 if dist == 1 else 0.5

    # Check if any ground truth code is an ancestor of predicted
    pred_ancestors = get_ancestors(predicted, hierarchy, max_depth=2)
    for gt_code in ground_truth:
        if gt_code in pred_ancestors:
            dist = pred_ancestors[gt_code]
            return 0.75 if dist == 1 else 0.5

    return 0.0


def validate(
    ground_truth_path: str,
    pipeline_output_path: str,
    obo_path: str = "data/hpo/hp.obo",
) -> dict:
    """Compare pipeline output against ground truth.

    Returns validation report dict.
    """
    # Load data
    gt_codes = load_codes_from_excel(ground_truth_path)
    pred_codes = load_codes_from_excel(pipeline_output_path)

    # Build hierarchy
    terms = parse_obo(obo_path)
    hierarchy = build_hierarchy(terms)

    # Score per patient
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

        # Score each prediction
        scores = []
        matched_gt = set()
        for p_code in pred:
            s = score_match(p_code, gt, hierarchy)
            scores.append(s)
            if s == 1.0:
                total_exact += 1
                matched_gt.add(p_code)
            elif s > 0:
                total_close += 1

        # Missed = ground truth codes not matched
        missed = gt - matched_gt
        total_missed += len(missed)

        # Extra = predicted codes with score 0
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

    # Aggregate
    precision = (total_exact + total_close) / total_pred if total_pred else 0.0
    recall = total_exact / total_gt if total_gt else 0.0

    report = {
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

    return report


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
