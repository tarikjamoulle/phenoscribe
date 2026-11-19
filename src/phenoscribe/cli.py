"""CLI entry point for Phenoscribe.

The commands import the pipeline (and with it torch, ChromaDB and the LLM
SDKs) when they run, so `phenoscribe --help` starts fast. The console shows warnings only; --verbose shows each step.
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import NoReturn

from phenoscribe.config import ConfigError, load_config
from phenoscribe.llm import PROVIDERS, missing_key_variable
from phenoscribe.review import (
    APPROVED,
    DONE,
    NEEDS_REVIEW,
    NO_REVIEW,
    all_reviews,
    approve,
    blocking_warnings,
    removed_values,
    save_review,
    summary_line,
)

logger = logging.getLogger(__name__)

PROVIDER_ALIASES = {
    "claude": "anthropic",
    "gpt": "openai",
}


def resolve_provider(name: str) -> str:
    """Map a user-supplied provider name to its canonical form.

    Accepts canonical names ("openai", "anthropic", "ollama") and friendly aliases
    ("claude", "gpt"). Raises ValueError on anything else.
    """
    canonical = PROVIDER_ALIASES.get(name.lower(), name.lower())
    if canonical not in PROVIDERS:
        raise ValueError(
            f"Unknown provider '{name}'. "
            f"Use one of: {sorted(PROVIDERS)} "
            f"(or aliases {sorted(PROVIDER_ALIASES)})."
        )
    return canonical


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="phenoscribe",
        description="Automated HPO phenotype coding from patient interviews.",
    )
    # --verbose belongs to every command, so it can go after the command name.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--verbose", "-v", action="store_true", help="Show each step in the console")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_command(name: str, **kwargs) -> argparse.ArgumentParser:
        return subparsers.add_parser(name, parents=[common], **kwargs)

    proc = add_command("process", help="Process recordings into HPO codes")
    proc.add_argument("input_dir", help="Directory containing audio/text files")
    proc.add_argument("--output", "-o", default="output", help="Output folder (default: output)")
    proc.add_argument("--config", "-c", default="config.yaml", help="Config file path")
    proc.add_argument(
        "--provider", "-p",
        default=None,
        help="Override LLM provider (openai|anthropic|ollama, or aliases claude|gpt)",
    )
    proc.add_argument(
        "--model", "-m",
        default=None,
        help="Override LLM model (e.g. claude-sonnet-4-6, gpt-4o-mini)",
    )
    proc.add_argument(
        "--propagate-ancestors",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="HPO true-path rule: emit an 'Ancestor Closure' sheet with the is_a ancestor expansion for downstream tools (Exomiser, LIRICAL, etc.). ON by default; pass --no-propagate-ancestors to suppress.",
    )
    proc.add_argument(
        "--no-review",
        action="store_true",
        help="Send the text to the LLM with the personal details the detector removed, "
        "with no person to check them. Without it, approve each file in the page first.",
    )
    proc.set_defaults(func=_cmd_process)

    clean = add_command(
        "clean",
        help="Remove personal details from recordings or transcripts (no LLM, no review)",
    )
    clean.add_argument("input_dir", help="Directory containing audio/text files")
    clean.add_argument("--output", "-o", default="output", help="Output folder (default: output)")
    clean.add_argument("--config", "-c", default="config.yaml", help="Config file path")
    clean.set_defaults(func=_cmd_clean)

    setup = add_command(
        "setup",
        help="Download the ontology and models and build the search index (skips what is present)",
    )
    setup.add_argument("--config", "-c", default="config.yaml", help="Config file path")
    setup.add_argument(
        "--no-models",
        action="store_true",
        help="Skip the speech and privacy model downloads (for CI)",
    )
    setup.set_defaults(func=_cmd_setup)

    status = add_command("status", help="Count the reviews of the output folder by status")
    status.add_argument("--output", "-o", default="output", help="Output folder (default: output)")
    status.set_defaults(func=_cmd_status)

    agg = add_command(
        "aggregate",
        help="Compute cohort-level HPO prevalence from a results workbook",
    )
    agg.add_argument("results", help="Path to results Excel file")
    agg.add_argument("--csv", default="output/prevalence.csv", help="Output CSV path")
    agg.add_argument("--chart", default="output/prevalence.png", help="Output chart PNG path")
    agg.add_argument("--top", type=int, default=20, help="Number of top terms to show in the chart")
    agg.add_argument("--no-chart", action="store_true", help="Skip the chart (CSV only)")
    agg.set_defaults(func=_cmd_aggregate)

    val = add_command(
        "validate",
        help="Score a results workbook against ground truth (term-level F1 + IC distribution of predicted terms)",
    )
    val.add_argument("results", help="Path to the pipeline-output Excel file (predicted codes)")
    val.add_argument("ground_truth", help="Path to the ground-truth Excel file")
    val.add_argument(
        "--obo",
        default=None,
        help="HPO OBO file for obsolete-id resolution (default: config.paths.hpo_obo)",
    )
    val.add_argument("--config", "-c", default="config.yaml", help="Config file path")
    val.set_defaults(func=_cmd_validate)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        args.func(args)
    except ConfigError as e:
        _fail(str(e))


def _fail(message: str) -> NoReturn:
    """Print the error on stderr and exit with code 1."""
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def _input_files(input_dir: str) -> list[str]:
    """The files of the input folder, sorted. plan_batch decides which ones run."""
    folder = Path(input_dir)
    if not folder.is_dir():
        _fail(f"'{folder}' is not a directory.")
    return sorted(str(f) for f in folder.iterdir() if f.is_file())


def _print_skipped(plans) -> None:
    for plan in plans:
        print(f"Skipped {plan.name}: {plan.note}")


def _cmd_process(args):
    """Code every file of the input folder into a new results workbook.

    Exits with code 1 when a file failed. A file that waits for a review is no failure.
    """
    from phenoscribe.batch import run_batch
    from phenoscribe.pipeline import NeedsReview

    config = load_config(args.config)
    if args.provider:
        try:
            config.llm.provider = resolve_provider(args.provider)
        except ValueError as e:
            _fail(str(e))
    if args.model:
        config.llm.model = args.model
    # None keeps the config value; True or False overrides it.
    if args.propagate_ancestors is not None:
        config.output.propagate_ancestors = args.propagate_ancestors

    files = _input_files(args.input_dir)
    # Stop before the transcription: it takes minutes and the coding cannot follow.
    variable = missing_key_variable(config.llm.provider)
    if variable:
        _fail(f"No API key for {config.llm.provider}: set the environment variable {variable}.")
    print(f"Output folder: {args.output} ({config.output.format} format)")
    print(f"LLM: {config.llm.provider}/{config.llm.model}")
    batch = run_batch(files, config, args.output, on_progress=_print_step, no_review=args.no_review)

    _print_skipped(batch.skipped)
    failed = 0
    for result in batch.files:
        head = f"[{result.patient_id}] {result.filename}"
        if result.error is None:
            print(f"{head}: done, {result.n_codes} HPO codes")
        elif isinstance(result.error, NeedsReview):
            print(f"{head}: waits for a review. {result.error}")
        else:
            failed += 1
            print(f"{head}: FAILED: {result.error}", file=sys.stderr)
    if not batch.files:
        _fail(f"No file to process in '{args.input_dir}'.")
    print(f"Results: {batch.excel_path}" if batch.excel_path else "No results file: no file was coded.")
    if failed:
        sys.exit(1)


def _print_step(fraction: float, message: str) -> None:
    if message != "Finished":
        print(f"  {message}", flush=True)


def _cmd_clean(args):
    """Remove personal details from each file. Writes clean texts, a report and a ZIP.

    Exits with code 1 when a file failed. A file with a blocking warning sign
    stays out of the ZIP and is no failure.
    """
    from phenoscribe.batch import finish_clean, plan_batch
    from phenoscribe.pipeline import find_details, read_transcript

    config = load_config(args.config)
    plans = plan_batch(_input_files(args.input_dir), config, args.output)
    _print_skipped(p for p in plans if p.action == "skip")
    done = []
    failed = 0
    for plan in (p for p in plans if p.action != "skip"):
        patient_id = plan.patient_id
        try:
            raw = read_transcript(plan.path, patient_id, config, args.output, lambda label: None,
                                  reuse_transcript=True)
            review = find_details(raw, plan.name, patient_id, config, args.output)
            blocking = blocking_warnings(review)
            if not blocking:
                approve(review, how=NO_REVIEW)
            save_review(review, args.output)
        except Exception as e:  # one bad file must not stop the run
            failed += 1
            print(f"[{patient_id}] FAILED: {e}", file=sys.stderr)
            continue
        print(f"[{patient_id}] {summary_line(review)}")
        if blocking:
            # The same rule as batch mode: a name can still be in the clean text.
            print(f"    NOT IN THE ZIP: {'; '.join(s.text for s in blocking)}. Check the file in the page.")
            continue
        done.append(patient_id)
        for entry in removed_values(review):
            print(f"    {entry.category:<12} ×{entry.count}  {entry.value}")
    archive = finish_clean(done, args.output)
    print(f"\nClean texts: {archive}" if archive else "\nNo file cleaned.")
    if failed:
        sys.exit(1)


def _cmd_setup(args):
    """Prepare a fresh copy of the app. Safe to run at every start.

    Each part is skipped when it is already present, so the start scripts
    call this every time and only the first start downloads anything.
    """
    from phenoscribe import hpo_index, pii, transcribe

    config = load_config(args.config)
    obo, chroma, release = config.paths.hpo_obo, config.paths.chroma_db, config.hpo.release
    embedding = config.hpo_index.embedding_model
    speech = config.transcription

    _setup_part(
        "HPO ontology",
        lambda: hpo_index.obo_ready(obo, release),
        "Downloading the HPO ontology (10 MB, one time only)...",
        lambda: hpo_index.download_obo(obo, release),
    )
    _setup_part(
        "Search index",
        lambda: hpo_index.index_ready(chroma, release, embedding),
        "Building the search index (one time only, a few minutes)...",
        lambda: hpo_index.seed_pinned_index(obo, chroma, release, embedding),
    )
    if args.no_models:
        print("Speech and privacy models: skipped (--no-models).")
    else:
        _setup_part(
            "Speech model",
            lambda: _cached(transcribe.download_speech_model, speech.backend, speech.model),
            "Downloading the speech model (3 GB, one time only)...",
            lambda: transcribe.download_speech_model(speech.backend, speech.model),
        )
        # Every model of the language map: the page lets the user pick the language.
        for model in config.pii.all_models():
            if model == pii.DEDUCE:
                continue  # a package (uv sync --extra dutch-rules), nothing to download
            _setup_part(
                f"Privacy model {model}",
                lambda model=model: _cached(pii.download_model, model),
                f"Downloading the privacy model {model} ({pii.size_text(model)}, one time only)...",
                lambda model=model: pii.download_model(model),
            )
    print("Setup done.")


def _setup_part(name, is_ready, message, fetch) -> None:
    """Print one plain line for a setup part, and fetch it only when it is missing."""
    if is_ready():
        print(f"{name}: ready.")
        return
    print(message, flush=True)
    fetch()


def _cached(download, *args) -> bool:
    """True when ``download(*args, local_files_only=True)`` finds the model in the cache."""
    try:
        download(*args, local_files_only=True)
    except FileNotFoundError:
        return False
    return True


def _cmd_status(args):
    """Count the reviews of the output folder by status, and name the files that wait."""
    reviews = all_reviews(args.output)
    if not reviews:
        print(f"No review in '{args.output}'.")
        return
    print(f"Reviews in '{args.output}': {len(reviews)}")
    for status in (NEEDS_REVIEW, APPROVED, DONE):
        print(f"  {status}: {sum(r.status == status for r in reviews)}")
    waiting = [r for r in reviews if r.status == NEEDS_REVIEW]
    if waiting:
        print("\nWaiting for a review:")
        for review in waiting:
            print(f"  {review.patient_id}  {review.source}")


def _cmd_aggregate(args):
    from phenoscribe.aggregate import (
        compute_prevalence,
        load_patient_codes,
        write_prevalence_chart,
        write_prevalence_csv,
    )

    if not Path(args.results).is_file():
        _fail(f"'{args.results}' is not a file.")

    patient_codes = load_patient_codes(args.results)
    if not patient_codes:
        _fail(f"No HPO codes found in {args.results}.")

    rows = compute_prevalence(patient_codes)
    n_patients = len(patient_codes)
    print(f"Cohort: {n_patients} patient(s), {len(rows)} distinct HPO term(s).")

    write_prevalence_csv(rows, args.csv)
    if not args.no_chart:
        write_prevalence_chart(rows, args.chart, top_n=args.top, n_patients=n_patients)

    print(f"\nTop {min(10, len(rows))} terms by prevalence:")
    print(f"{'#':>3}  {'Patients':>8}  {'Pct':>5}  {'HPO Code':<12}  Term")
    print("-" * 70)
    for i, r in enumerate(rows[:10], 1):
        print(f"{i:>3}  {r['n_patients']:>8}  {r['pct']:>4.1f}%  {r['hpo_id']:<12}  {r['hpo_term']}")
    print(f"\nCSV:   {args.csv}")
    if not args.no_chart:
        print(f"Chart: {args.chart}")


def _cmd_validate(args):
    """Score predicted codes against a ground truth: term-level F1 and the IC of the predicted terms.

    The IC scores are for measurement only. The pipeline picks a code with the
    ChromaDB shortlist and the LLM judge.
    """
    from phenoscribe.validate import print_report, validate

    for label, path in (("results", args.results), ("ground truth", args.ground_truth)):
        if not Path(path).is_file():
            _fail(f"{label} file '{path}' is not a file.")

    config = load_config(args.config)
    obo_path = args.obo or config.paths.hpo_obo
    report = validate(args.ground_truth, args.results, obo_path, release=config.hpo.release)
    print_report(report)


if __name__ == "__main__":
    main()
