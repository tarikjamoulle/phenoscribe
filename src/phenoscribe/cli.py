"""CLI entry point for Phenoscribe."""

import argparse
import logging
import sys
from pathlib import Path

from phenoscribe.config import load_config
from phenoscribe.jobs import create_job, get_failed_jobs, update_job
from phenoscribe.pipeline import process_recording
from phenoscribe.transcribe import AUDIO_EXTENSIONS, TEXT_EXTENSIONS

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = AUDIO_EXTENSIONS | TEXT_EXTENSIONS


def main():
    parser = argparse.ArgumentParser(
        prog="phenoscribe",
        description="Automated HPO phenotype coding from patient interviews.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # Process command
    proc = subparsers.add_parser("process", help="Process recordings into HPO codes")
    proc.add_argument("input_dir", help="Directory containing audio/text files")
    proc.add_argument("--output", "-o", default=None, help="Output Excel file path")
    proc.add_argument("--config", "-c", default="config.yaml", help="Config file path")
    proc.add_argument("--skip-transcription", action="store_true", help="Skip transcription, read from saved transcripts in output/transcripts/")
    proc.add_argument("--retry-failed", action="store_true", help="Retry previously failed jobs")

    # Status command
    status = subparsers.add_parser("status", help="Show processing status")
    status.add_argument("--config", "-c", default="config.yaml", help="Config file path")

    # Aggregate command
    agg = subparsers.add_parser(
        "aggregate",
        help="Compute cohort-level HPO prevalence from a results workbook",
    )
    agg.add_argument("results", help="Path to results Excel file")
    agg.add_argument("--csv", default="output/prevalence.csv", help="Output CSV path")
    agg.add_argument("--chart", default="output/prevalence.png", help="Output chart PNG path")
    agg.add_argument("--top", type=int, default=20, help="Number of top terms to show in the chart")
    agg.add_argument("--no-chart", action="store_true", help="Skip the chart (CSV only)")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command == "process":
        _cmd_process(args)
    elif args.command == "status":
        _cmd_status(args)
    elif args.command == "aggregate":
        _cmd_aggregate(args)
    else:
        parser.print_help()
        sys.exit(1)


def _cmd_process(args):
    config = load_config(args.config)
    output_path = args.output or config.output.path
    input_dir = Path(args.input_dir)
    db_path = config.paths.jobs_db

    if not input_dir.is_dir():
        print(f"Error: '{input_dir}' is not a directory.")
        sys.exit(1)

    # Find input files (exclude _pseudo files which are generated outputs)
    files = sorted(
        f for f in input_dir.iterdir()
        if f.suffix.lower() in SUPPORTED_EXTENSIONS
        and not f.stem.endswith("_pseudo")
    )

    if not files:
        print(f"No supported files found in '{input_dir}'.")
        print(f"Supported formats: {SUPPORTED_EXTENSIONS}")
        sys.exit(1)

    print(f"Found {len(files)} file(s) to process.")
    print(f"Output: {output_path} ({config.output.format} format)")
    print(f"LLM: {config.llm.provider}/{config.llm.model}")
    print()

    completed = 0
    failed = 0

    for filepath in files:
        # Extract patient ID from filename (e.g., "MGA.014.txt" -> "MGA.014")
        patient_id = filepath.stem

        # Create job
        job_id = create_job(db_path, str(filepath), patient_id)
        update_job(db_path, job_id, "processing")

        print(f"[{patient_id}] Processing {filepath.name}...")

        try:
            matches = process_recording(
                str(filepath),
                patient_id,
                config,
                output_path=output_path,
                skip_transcription=args.skip_transcription,
            )
            update_job(db_path, job_id, "completed")
            completed += 1
            print(f"[{patient_id}] Done — {len(matches)} HPO codes found.")
        except Exception as e:
            step = _identify_failed_step(e)
            update_job(db_path, job_id, "failed", step_failed=step, error_msg=str(e))
            failed += 1
            print(f"[{patient_id}] FAILED at {step}: {e}")

    # Retry failed jobs once
    if not args.retry_failed:
        failed_jobs = get_failed_jobs(db_path)
        retryable = [j for j in failed_jobs if j["retries"] < 2]
        if retryable:
            print(f"\nRetrying {len(retryable)} failed job(s)...")
            for job in retryable:
                patient_id = job["patient_id"]
                print(f"[{patient_id}] Retrying...")
                try:
                    matches = process_recording(
                        job["input_file"],
                        patient_id,
                        config,
                        output_path=output_path,
                        skip_transcription=args.skip_transcription,
                    )
                    update_job(db_path, job["id"], "completed")
                    completed += 1
                    failed -= 1
                    print(f"[{patient_id}] Retry succeeded — {len(matches)} HPO codes.")
                except Exception as e:
                    step = _identify_failed_step(e)
                    update_job(db_path, job["id"], "failed", step_failed=step, error_msg=str(e))
                    print(f"[{patient_id}] Retry FAILED: {e}")

    print(f"\nDone. Completed: {completed}, Failed: {failed}")
    print(f"Results: {output_path}")


def _cmd_status(args):
    from phenoscribe.jobs import get_all_jobs

    config = load_config(args.config)
    jobs = get_all_jobs(config.paths.jobs_db)

    if not jobs:
        print("No jobs found.")
        return

    counts = {"pending": 0, "processing": 0, "completed": 0, "failed": 0}
    for j in jobs:
        counts[j["status"]] = counts.get(j["status"], 0) + 1

    print(f"Jobs: {len(jobs)} total")
    for status, count in counts.items():
        if count > 0:
            print(f"  {status}: {count}")

    failed = [j for j in jobs if j["status"] == "failed"]
    if failed:
        print("\nFailed jobs:")
        for j in failed:
            print(f"  {j['patient_id']} — {j['step_failed']}: {j['error_msg']}")


def _cmd_aggregate(args):
    from phenoscribe.aggregate import (
        compute_prevalence,
        load_patient_codes,
        write_prevalence_chart,
        write_prevalence_csv,
    )

    if not Path(args.results).is_file():
        print(f"Error: '{args.results}' is not a file.")
        sys.exit(1)

    patient_codes = load_patient_codes(args.results)
    if not patient_codes:
        print(f"No HPO codes found in {args.results}.")
        sys.exit(1)

    rows = compute_prevalence(patient_codes)
    n_patients = len(patient_codes)
    print(f"Cohort: {n_patients} patient(s), {len(rows)} distinct HPO term(s).")

    write_prevalence_csv(rows, args.csv)
    if not args.no_chart:
        write_prevalence_chart(rows, args.chart, top_n=args.top)

    print(f"\nTop {min(10, len(rows))} terms by prevalence:")
    print(f"{'#':>3}  {'Patients':>8}  {'Pct':>5}  {'HPO Code':<12}  Term")
    print("-" * 70)
    for i, r in enumerate(rows[:10], 1):
        print(f"{i:>3}  {r['n_patients']:>8}  {r['pct']:>4.1f}%  {r['hpo_id']:<12}  {r['hpo_term']}")
    print(f"\nCSV:   {args.csv}")
    if not args.no_chart:
        print(f"Chart: {args.chart}")


def _identify_failed_step(error: Exception) -> str:
    """Try to identify which pipeline step failed from the error."""
    msg = str(error).lower()
    if "whisper" in msg or "transcri" in msg or "audio" in msg:
        return "transcription"
    if "pii" in msg or "ner" in msg or "pseudonym" in msg:
        return "pii"
    if "extract" in msg or "symptom" in msg:
        return "extraction"
    if "chroma" in msg or "hpo" in msg or "match" in msg:
        return "hpo_matching"
    if "excel" in msg or "openpyxl" in msg or "output" in msg:
        return "output"
    return "unknown"


if __name__ == "__main__":
    main()
