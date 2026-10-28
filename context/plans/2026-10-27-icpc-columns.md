# Plan: ICPC-2 and ICPC-3 columns in the Codes sheet

Date: 2026-10-27. Status: merged to main 2026-10-28. Chapter and component columns added after feedback from Peter Robinson and Marc Jamoulle. Asked for by Marc Jamoulle. It replaces steps 2-4 of
`2026-10-27-hpo-icpc-mapping.md` with a small first version.

## Summary

The tool looks up each HPO code in one small table and writes the ICPC-3 and ICPC-2 code next to it.
This applies to the `detailed` format, which `config.yaml` sets. Its first sheet is the sheet that Marc calls `Codes`.
The table starts from Marc's own HPO to ICPC-3 file, plus about 50 frequent codes that we draft and Marc checks.
No UMLS, no 3BT, no Chroma, no LLM call at run time.

## Goal and limits

- Goal: about 80% of the rows in the Codes sheet get an ICPC-3 and an ICPC-2 code.
- A row with an HPO code that is not in the table keeps empty ICPC cells. We do not guess.
- Out of scope: a full HPO to ICPC mapping, ancestor fallback, automatic mapping, ICPC titles,
  ICPC columns in the `semicolon` and `purl` formats.
- The coverage figures below count HPO codes in the table. They do not prove that each ICPC-2 code is correct.

## Facts (checked 2026-10-27)

Source file from Marc: `data/Covid_ontology- ICPC3 study (Deer et all. 2021).xlsx`, sheet `Full_en`.
Column C is the HPO code, column D the ICPC-3 code, column F the HPO term. 274 HPO codes, 137 distinct ICPC-3 codes.
The file has no ICPC-2 codes and no ICPC-3 titles.

Coverage against `data/results_all_2026-10-27.xlsx`, sheet `Codes` (2,130 rows, 488 distinct HPO codes):

| Table | Codes rows covered |
|---|---|
| Marc's table only | 1,194 (56%), 126 distinct codes |
| + 30 most frequent missing codes | 73% |
| + 50 most frequent missing codes | 78% |
| + 100 most frequent missing codes | 85% |

The most frequent missing codes: abdominal pain, nausea, diarrhea, speech articulation difficulties,
constipation, decreased libido, lower limb pain, auditory sensitivity, paroxysmal tachycardia, total anosmia.

### The official ICPC-2 source: the ICPC-3 browser

The ICPC-3 Foundation browser (https://browser.icpc-3.info/) gives the ICPC-2 code for each ICPC-3 class.
It also gives the title, ICD-10, ICD-11 and SNOMED CT. Its data feed is `browse.php`:

- `?operation=searchCode&str=AS04` gives the internal id (`{"found":[127]}`).
- `?operation=getRubrics&id=127` gives the rubrics. The rubric with `kind = "icpc-2"` holds the ICPC-2 code (`A04`).

Result for the 137 ICPC-3 codes in Marc's table (checked 2026-10-27, ICPC-3-EN version 2025-01-13):

- Almost all symptom and diagnosis codes have exactly one ICPC-2 code. Example: `RS02` Shortness of breath = `R02`.
- 10 codes have 2 or 3 ICPC-2 codes. Examples: `NS11` Speech problem = `N19`, `P10`; `PS07` = `P07`, `P08`;
  `US50` = `U71`, `U90`, `U98`. For these, the ICPC-2 code is chosen for each HPO term.
- The functioning codes (`2F..`) have no ICPC-2 code. ICPC-2 has no functioning chapter. The `icpc2` cell stays empty.
- The browser blocks fast requests (HTTP 429). Fetch with 1 request per second and keep a local cache.

### Damaged codes in Marc's file: our guess

Only 26 rows of the results use one of these codes. Each guess is a valid code in the browser. All are `draft`.

| Marc's value | Terms | Guess ICPC-3 | ICPC-2 | Reason |
|---|---|---|---|---|
| `-105.0`, `-110.0`, `-113.0`, `R105`, `K105`, `K113`, `B105` | 50 lab, lung function and imaging findings | `AS50` Other specified abnormal result investigation | `A91` | Marc wrote a chapter letter + a process number. The browser does not have these codes. Excel removed the letter. |
| `A03` | Low-grade fever, recurrent fever, temperature instability | `AS03` Fever | `A03` | `A03` is the ICPC-2 code |
| `A2F85` | Exercise intolerance | `2F85` Exercise tolerance functions | empty | typo, extra `A` |
| `NS12`, `NS13` | Parageusia, phantageusia | `NS08` Disturbance of smell, taste or both | `N16` | not in the 2025 browser |
| `KD` | Myocarditis | `KD01` Infection of circulatory system | `K70` | the browser search for myocarditis gives `KD01` |
| `2F` | Frailty | `AS08` Frailty | `P05` | a new class since Marc's 2021 file |
| `2F` | Impairment of activities of daily living | empty | empty | the van Boven note says to use several `2F` codes; no single code fits |
| `ZC5` | Occupational disability | `ZC16` Work problem | `Z05` | `ZC5` does not exist. `Z05` comes from the ICPC-2 list; the browser blocked the check |
| `AD46` | Anaphylactic shock | `AD46.00` Anaphylactic shock | `A92` | the van Boven note in the `ICPC-3` sheet |

## Design

### 1. The table: `src/phenoscribe/resources/hpo_icpc.csv`

One row for each HPO code. Columns:

| Column | Example | Note |
|---|---|---|
| `hpo_id` | `HP:0012378` | unique key |
| `hpo_term` | `Fatigue` | for the reviewer only |
| `icpc3` | `AS04` | |
| `icpc2` | `A04` | one code, or empty when no ICPC-2 code fits |
| `source` | `marc` or `draft` | `marc` = Marc checked both codes of the row |

The ICPC-2 code belongs to the HPO row, not to the ICPC-3 code.
One ICPC-3 code can go to more than one ICPC-2 code, so a fixed ICPC-3 to ICPC-2 rule gives false certainty.

The file goes in the package folder. The code reads it with
`importlib.resources.files("phenoscribe") / "resources" / "hpo_icpc.csv"`, so it works in the repo and in a wheel. `data/` is in `.gitignore`, and the release zip (`git archive`) ships only committed files.
The table holds no patient data, so a commit is safe.

### 2. Build the table: `scripts/build_icpc_table.py`

1. Read `Full_en`. Strip spaces. Replace the damaged codes with the guesses above (`source = draft`).
2. Read the browser cache `context/icpc/icpc3_browser.json` (title and ICPC-2 list for each ICPC-3 code).
   `scripts/fetch_icpc3_browser.py` fills the cache, 1 request per second, and fetches only codes that are not in it.
3. If the ICPC-3 code has one ICPC-2 code, use it. If it has more, take the choice from the hand file.
4. Read a hand file `context/icpc/icpc_drafts.csv` with the extra HPO rows and the ICPC-2 choices (same columns).
5. Check: each `hpo_id` exists in `data/hpo/hp.obo` and is not obsolete. Each `hpo_id` occurs one time.
   Each ICPC-3 code is in the browser cache. Each ICPC-2 code matches `^[A-Z]\d{2}$`.
6. Write `src/phenoscribe/resources/hpo_icpc.csv`, sorted by `hpo_id`.

The script runs one time by hand. The pipeline reads only the CSV.

### 3. Draft the missing codes (manual work, about half a day)

1. List the 50 most frequent HPO codes in the Codes sheet that are not in Marc's table.
2. Draft an ICPC-3 code for each, from the ICPC-3 browser (icpc-3.info).
3. The ICPC-2 code comes from the browser for all rows. Choose by hand only for the about 10 codes with more than one.
4. Marc's rows with a single browser ICPC-2 code keep `source = marc`: the ICPC-3 code is his, the ICPC-2 code is official.
   The other rows have `source = draft`.
5. Send Marc one review sheet with the `draft` rows and the damaged codes. Add the ICPC titles there to help him.
6. Set `source = marc` on the rows he accepts. Rebuild the CSV.

### 4. Lookup: `src/phenoscribe/icpc.py`

- `load_icpc_table(path=None) -> dict[str, dict]` reads the CSV one time (cache with `functools.lru_cache`).
- `icpc_for(hpo_id) -> dict` returns `{"icpc3": ..., "icpc2": ..., "source": ...}`, or empty strings.
- A missing CSV logs one warning and gives empty cells. It does not stop the pipeline.

### 5. Excel output: `src/phenoscribe/output.py`

- Add 3 headers at the end of `HEADERS["detailed"]`: `ICPC-3`, `ICPC-2`, `ICPC Source`.
  They go at the end because the upgrade code for an older workbook adds new headers only at the end.
- `_write_detailed_format` fills them from `icpc_for(m.get("hpo_id"))`. Absent findings get the code too.
  The `Present/Absent` column already tells the reader that the finding is denied.
- `ICPC Source` shows `marc` or `draft`. A `draft` row is not a `REVIEW` row: the HPO code can still be good.
- `_write_detailed_format` appends 3 more values to each row (20 in total, today 17).
- `_stamp_provenance` writes `icpc_table` in row 3 (key in A3, build date and row count in B3).
  It refreshes the row on each run, the same as `hpo_release` in row 2.
- The `semicolon` and `purl` formats do not change.

### 6. Add the columns to the existing results: `scripts/add_icpc_columns.py`

Marc already has `data/results_all_2026-10-27.xlsx`, a merged workbook with hand codes in it.
The pipeline does not make that workbook, and a new run takes hours and loses the hand codes.
The script reads the `Codes` sheet, finds the `HPO Code` column by name, and appends the 3 ICPC columns.
It writes a new file and keeps the original. It uses `icpc_for`, so the output equals a new pipeline run.

## Tests (`tests/test_icpc.py`, `tests/test_output.py`)

- The committed CSV loads, has unique `hpo_id` values, and every code matches the patterns above.
- `icpc_for("HP:0012378")` gives the expected codes. An unknown code and an empty code give empty strings.
- `write_excel` in the `detailed` format writes the 3 new headers and filled cells.
- An older workbook with 17 columns gets the 3 new headers at the end.
- A missing CSV gives empty cells and no crash.
- `tests/test_release_zip.py`: the release zip holds `src/phenoscribe/resources/hpo_icpc.csv`.
- A built wheel (`uv build`) holds `phenoscribe/resources/hpo_icpc.csv`.
- `add_icpc_columns.py` on a small fixture workbook adds the columns and leaves the other cells the same.

## Check after the build

Run `add_icpc_columns.py` on `results_all_2026-10-27.xlsx`. Report 2 numbers: the share of Codes rows with an ICPC-3 code, and the share with an ICPC-2 code.
Split each by `marc` and `draft`. Expected: about 78% for ICPC-3, a little less for ICPC-2.

## Questions for Marc

1. Does he accept the guesses for the damaged codes (table above)?
2. Licence: the ICPC-3 titles and conversions belong to the ICPC-3 Foundation (WONCA).
   We commit only codes, no titles. Is this use acceptable, or does he ask Kees van Boven?
3. Does he accept that a row with no ICPC code stays empty?
4. Is one ICPC-2 code for each HPO term enough, or does he want more than one in some cases?

## Codex review (2026-10-27, gpt-6-luna)

Applied: ICPC-2 per HPO row (one ICPC-3 code can map to several ICPC-2 codes), the resource path and wheel check,
row values 17 to 20, the provenance row, separate ICPC-2 coverage, titles out of the CSV.
