"""Measure each PII detector on the synthetic test set (tests/fixtures/pii).

Three numbers for each language and each detector (the regex layer always runs):

- recall: planted details that are fully removed / planted details, with the
  missed details listed by type. "Removed" is what the pipeline removes: each
  found value at every position in the text (review.clean_text), so a name that
  is also a word ("Rose", "rose") is removed in both places;
- words removed by mistake: removed words that are not part of a planted
  detail, with the "keep" words (medical and look-alike words) listed apart;
- warning rate on clean files: the share of files with every detail removed
  that still get a warning sign (review.warnings). The page sends a file to a
  person only for a blocking sign (review.blocking_warnings); the script counts
  those files apart.

The test set is frozen (tests/fixtures/pii/CHECKSUMS). This script refuses to
run when a file differs from its checksum.

    uv run python scripts/eval_pii.py              # all languages
    uv run python scripts/eval_pii.py --lang nl    # one language
    uv run python scripts/eval_pii.py --json benchmarks/pii/pii_eval_<date>.json
"""

import argparse
import hashlib
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path

from phenoscribe import pii
from phenoscribe.review import Review, _occurrences, blocking_warnings, fold, removed_values, warning_signs

ROOT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "pii"

CAMEMBERT = pii.DEFAULT_MODEL
GLINER = pii.GLINER_MODEL
STANFORD = "StanfordAIMI/stanford-deidentifier-base"
OPENMED = "OpenMed/OpenMed-PII-Dutch-SuperClinical-Large-434M-v1"

# Detector sets to compare for each language. The last set of each language
# is the map that config.yaml ships (pii.models).
DETECTORS = {
    "fr": [[CAMEMBERT], [GLINER], [CAMEMBERT, GLINER]],
    # OpenMed is measured only: its training data (ai4privacy) is non-commercial.
    "nl": [[OPENMED], [pii.DEDUCE], [GLINER, OPENMED], [GLINER], [GLINER, pii.DEDUCE]],
    "en": [[STANFORD], [GLINER], [STANFORD, GLINER]],
}

WORD = re.compile(r"\w+")
NAMES = {CAMEMBERT: "camembert-pii-fr", GLINER: "gliner-multi-pii", STANFORD: "stanford-deid",
         OPENMED: "openmed-nl-large", pii.DEDUCE: "deduce"}


def check_frozen() -> None:
    for line in (ROOT / "CHECKSUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            sys.exit(f"{name} differs from its frozen checksum. The test set must not change.")


def covered(start: int, end: int, removed: list[tuple[int, int]], text: str) -> bool:
    """True when every letter and digit of text[start:end] is inside a removed span."""
    return all(any(s <= i < e for s, e in removed) for i in range(start, end) if text[i].isalnum())


def removed_positions(review: Review) -> list[tuple[int, int]]:
    """The positions that review.clean_text replaces: the spans, plus every
    other place of each removed value (any case, accents, hyphens)."""
    folded = fold(review.raw_text)
    keys = {fold(e.value) for e in removed_values(review)}
    positions = [(s["start"], s["end"]) for s in review.spans if fold(s["value"]) in keys]
    for entry in removed_values(review):
        positions += _occurrences(folded, entry.value)
    return positions


def evaluate(lang: str, models: list[str], min_score: float = 0.6) -> dict:
    planted = found = 0
    missed: Counter = Counter()
    missed_values: list[str] = []
    wrong: list[str] = []
    wrong_keep: list[str] = []
    clean_files = clean_warned = clean_blocked = 0
    reasons: Counter = Counter()
    missed_files = missed_warned = missed_blocked = 0
    for path in sorted((ROOT / lang).glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        gold = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        spans, ran = pii.detect_personal_details(text, models=models, fallback_model=None,
                                                 min_score=min_score)
        review = Review.from_spans(path.name, path.stem, text, spans)
        review.models = ran
        removed = removed_positions(review)
        all_found = True
        for d in gold["details"]:
            planted += 1
            if covered(d["start"], d["end"], removed, text):
                found += 1
            else:
                all_found = False
                missed[d["type"]] += 1
                missed_values.append(f"{d['type']}:{d['value']}")
        for w in WORD.finditer(text):
            a, b = w.start(), w.end()
            if not any(a < e and b > s for s, e in removed):
                continue  # a word with no removed character
            if any(a < d["end"] and b > d["start"] for d in gold["details"]):
                continue
            if any(a < k["end"] and b > k["start"] for k in gold["keep"]):
                wrong_keep.append(w.group())
            else:
                wrong.append(w.group())
        signs = warning_signs(review)
        blocking = blocking_warnings(review, signs)
        if all_found:
            clean_files += 1
            clean_warned += bool(signs)
            clean_blocked += bool(blocking)
            reasons.update(s.kind for s in signs)
        else:
            missed_files += 1
            missed_warned += bool(signs)
            missed_blocked += bool(blocking)
    return {
        "lang": lang,
        "detector": " + ".join(NAMES.get(m, m) for m in models),
        "planted": planted,
        "found": found,
        "recall": round(found / planted, 3),
        "missed_by_type": dict(missed.most_common()),
        "missed": missed_values,
        "removed_by_mistake": wrong,
        "keep_words_removed": wrong_keep,
        "clean_files": clean_files,
        "clean_files_with_warning": clean_warned,
        "warning_rate_clean": round(clean_warned / clean_files, 2) if clean_files else None,
        "warning_reasons_clean": dict(reasons),
        "files_with_a_miss": missed_files,
        "files_with_a_miss_and_warning": missed_warned,
        "clean_files_to_a_person": clean_blocked,
        "files_with_a_miss_to_a_person": missed_blocked,
    }


def name_recall(result: dict, lang: str) -> float:
    """Recall on first and family names only."""
    total = sum(1 for p in (ROOT / lang).glob("*.json")
                for d in json.loads(p.read_text(encoding="utf-8"))["details"] if d["type"] in ("first", "last"))
    missed = sum(n for t, n in result["missed_by_type"].items() if t in ("first", "last"))
    return round((total - missed) / total, 3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lang", choices=list(DETECTORS))
    parser.add_argument("--json", help="Write the results to this JSON file")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    check_frozen()
    results = []
    for lang in [args.lang] if args.lang else list(DETECTORS):
        for models in DETECTORS[lang]:
            if pii.DEDUCE in models and not pii.deduce_installed():
                print(f"{lang} {models}: skipped, DEDUCE is not installed (uv sync --extra dutch-rules)")
                continue
            r = evaluate(lang, models)
            r["name_recall"] = name_recall(r, lang)
            results.append(r)
            print(f"\n{lang} · {r['detector']}")
            print(f"  recall {r['recall']} ({r['found']}/{r['planted']}), names {r['name_recall']}")
            print(f"  missed by type: {r['missed_by_type']}  {r['missed']}")
            print(f"  removed by mistake: {r['removed_by_mistake']}  keep words: {r['keep_words_removed']}")
            print(f"  warning on clean files: {r['clean_files_with_warning']}/{r['clean_files']} "
                  f"{r['warning_reasons_clean']}  · files with a miss that get a warning: "
                  f"{r['files_with_a_miss_and_warning']}/{r['files_with_a_miss']}")
            print(f"  to a person on the page (blocking sign): clean files "
                  f"{r['clean_files_to_a_person']}/{r['clean_files']}, files with a miss "
                  f"{r['files_with_a_miss_to_a_person']}/{r['files_with_a_miss']}")
    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
