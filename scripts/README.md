# Scripts

Helper scripts for maintainers. The app does not need them. Run each script from the
repository root, after `uv sync --group bench` and `uv run phenoscribe setup`.

The `bench` dependency group (`pyproject.toml`) holds what the scripts import beyond the
package: pandas, for `dev/eval_recall.py`. `benchmark_gsc.py` also needs FastHPOCR, which is
not in the lock: install it with `uv pip install FastHPOCR pronto`.

The scripts in `dev/` need private data that is not in this repository, for example a
manual code list or real transcripts. They stay here so a maintainer with that data can
run them again.

| Script | Purpose | How to run | Needs private data |
|---|---|---|---|
| `smoke_run.py` | One short French text through the whole pipeline, with the LLM stubbed (CI) | `uv run python scripts/smoke_run.py` | No |
| `eval_pii.py` | Recall of each privacy model on the synthetic test set (`tests/fixtures/pii`) | `uv run python scripts/eval_pii.py [--lang fr] [--json out.json]` | No |
| `search_recall.py` | Rank of the reviewed HPO code in the search shortlist (`benchmarks/review_codes.json`) | `uv run python scripts/search_recall.py [--k 10]` | No |
| `fetch_benchmark_data.py` | Download the GSC+ corpus and `phenotype.hpoa`, and check them against `benchmarks/CHECKSUMS.txt` (no digest for GSC+ yet: a warning, `--record` stores one) | `uv run python scripts/fetch_benchmark_data.py --hpoa --gsc` | No |
| `benchmark_gsc.py` | Phenoscribe and FastHPOCR on the GSC+ gold standard | `uv run python scripts/benchmark_gsc.py --gsc-dir .tmp/GSC_2024 --fasthpocr-index <index> --chroma-path data/chroma_db` | No (needs FastHPOCR; an API key for `--full-sample`) |
| `build_name_lists.py` | Build the first-name and town lists of the leak check from the Statbel files | `uv run python scripts/build_name_lists.py <folder>` | No (public Statbel files) |
| `fetch_icpc3_browser.py` | Fetch ICPC-3 classes and their ICPC-2 codes into a local cache | `uv run python scripts/fetch_icpc3_browser.py AS04 RS02` | No |
| `add_icpc_columns.py` | Add the ICPC columns to an existing results workbook | `uv run python scripts/add_icpc_columns.py in.xlsx out.xlsx` | No |
| `dev/build_icpc_table.py` | Build `src/phenoscribe/resources/hpo_icpc.csv` from the hand mapping workbook | `uv run python scripts/dev/build_icpc_table.py <workbook.xlsx>` | Yes |
| `dev/build_sapbert_index.py` | Build a SapBERT index in `.tmp/chroma_sapbert` for recall tests | `uv run python scripts/dev/build_sapbert_index.py` | No (used by `dev/eval_recall.py`) |
| `dev/eval_recall.py` | recall@5 and recall@10 of the default and SapBERT indexes on a manual code list | `uv run python scripts/dev/eval_recall.py` | Yes |
| `dev/measure_reffree.py` | Checks on a results workbook that need no gold standard, plus agreement with a manual code list | `uv run python scripts/dev/measure_reffree.py --results <xlsx> --gt <xlsx>` | Yes |
| `dev/benchmark_ontogpt.py` | ontoGPT and Phenoscribe on the same pseudonymised transcripts | `uv run python scripts/dev/benchmark_ontogpt.py [--limit N]` | Yes |
