"""Run validation scorer against ground truth."""

import argparse

from phenoscribe.validate import validate, print_report

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate pipeline output against ground truth")
    parser.add_argument("--ground-truth", required=True, help="Path to ground truth Excel file")
    parser.add_argument("--pipeline-output", required=True, help="Path to pipeline output Excel file")
    parser.add_argument("--obo", default="data/hpo/hp.obo", help="Path to HPO OBO file")
    args = parser.parse_args()

    report = validate(args.ground_truth, args.pipeline_output, args.obo)
    print_report(report)
