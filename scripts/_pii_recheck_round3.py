"""Round-3 PII recheck: run the FIXED pii.py over the 26 transcripts and scan for leaks."""
import json
import re
import sys
from pathlib import Path

ROOT = Path("/Users/tarikjamoulle/projects/hpo_identifier")
sys.path.insert(0, str(ROOT / "src"))

from phenoscribe.pii import pseudonymize  # noqa: E402

TRANSCRIPT_DIR = ROOT / "output" / "transcripts"
OUT_DIR = ROOT / "output" / "pseudo_round3"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# A leak: a pseudonym placeholder (CATEGORY_<n>) immediately adjacent to a
# letter on either side, e.g. "PERSON_1loïse" or "xPERSON_1". The fixed
# _snap_to_word_boundaries should have eliminated these.
CATEGORIES = "PERSON|LOCATION|ORGANIZATION|EMAIL|PHONE|ID|URL|MISC|JOB|DATE|AGE|GENDER"
LETTER = r"[A-Za-zÀ-ÖØ-öø-ÿ]"
LEAK_PATTERN = re.compile(
    rf"(?:{LETTER}(?:{CATEGORIES})_\d+)"        # letter immediately before placeholder
    rf"|(?:(?:{CATEGORIES})_\d+{LETTER})"        # letter immediately after placeholder
)

files = sorted(TRANSCRIPT_DIR.glob("MGA.*.txt"))
total_leaks = 0
files_with_leaks = 0
per_file = []

for f in files:
    text = f.read_text(encoding="utf-8")
    result, mapping = pseudonymize(text)
    (OUT_DIR / f.name).write_text(result, encoding="utf-8")

    leaks = LEAK_PATTERN.findall(result)
    if leaks:
        files_with_leaks += 1
        total_leaks += len(leaks)
    per_file.append({"file": f.name, "leaks": len(leaks), "examples": leaks[:5]})

summary = {
    "files_scanned": len(files),
    "files_with_leaks": files_with_leaks,
    "total_leak_occurrences": total_leaks,
    "per_file": per_file,
}
print(json.dumps(summary, ensure_ascii=False, indent=2))
