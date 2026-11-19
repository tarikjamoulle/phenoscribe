# PII test set (synthetic)

Fifteen short interviews, five for each language (fr, nl, en). Every interview is
synthetic and new: no line comes from a real patient or a real transcript. The
names, towns, streets, phone numbers and national numbers are made up.

- `<lang>/NN.txt`: the interview text, as a detector reads it.
- `<lang>/NN.json`: the expected spans.
  - `details`: each planted personal detail, with `start`, `end`, `value` and `type`
    (`first`, `last`, `town`, `street`, `hosp`, `org`, `phone`, `id`, `dob`, `email`).
  - `keep`: words that look like a name or a town and are not ("peau rose", "geel",
    "peer", "spa", "maladie de Crohn"). They must stay in the text.

The set was written and frozen before any detector ran on it. `CHECKSUMS` holds the
SHA-256 of each file, and `tests/test_pii_testset.py` fails if a file changes.
Do not change a file to help a detector. Add a new file instead.

`scripts/eval_pii.py` measures each detector on this set.
