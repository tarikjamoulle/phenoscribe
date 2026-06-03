"""Fetch + checksum the corpora the benchmarks need, so they rerun from the repo.

The large corpora are NOT vendored (GSC+ is hundreds of files, phenotype.hpoa is
~35 MB). What is vendored is this fetch script and a recorded SHA-256 for each
artefact, keyed to the pinned HPO release `hp/releases/2026-02-16`. Running this
script downloads each artefact and verifies it against the recorded checksum, so
the headline GSC+ and IC-based numbers can be reproduced from a clean checkout.

Two artefacts:

  1. phenotype.hpoa (release v2026-02-16) — every IC score depends on it
     (`phenoscribe.semantic_similarity`). Downloaded from the HPO GitHub release.

  2. GSC+ / GSC-2024 corpus — the gold standard the GSC+ benchmark scores on.
     Downloaded as the upstream zip and unpacked. Document set is large, so a
     single SHA-256 over the sorted (relative-path, file-bytes) manifest pins it.

Checksums live in benchmarks/CHECKSUMS.txt, one `sha256␠␠name` line each
(the `shasum -a 256` format). If an artefact's checksum is not yet recorded, the
script prints the computed value and writes it, so the FIRST fetch on a machine
that already trusts its source seeds the record and every later fetch verifies.
Use --strict to fail instead when a checksum is missing.

Usage:
    PYTHONPATH=$PWD/src python scripts/fetch_benchmark_data.py \\
        --hpoa --gsc --gsc-dir .tmp/GSC_2024
"""

import argparse
import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

# Pinned HPO release. Keep in lockstep with phenoscribe.config.release and
# phenoscribe.semantic_similarity._HPOA_RELEASE.
HPO_RELEASE = "2026-02-16"

HPOA_URL = (
    "https://github.com/obophenotype/human-phenotype-ontology/"
    f"releases/download/v{HPO_RELEASE}/phenotype.hpoa"
)

# GSC-2024 refresh (Groza et al.), distributed with the FastHPOCR paper.
GSC_ZIP_URL = (
    "https://github.com/tudorgroza/code-for-papers/raw/main/gsc-2024/GSC_2024.zip"
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKSUMS_PATH = REPO_ROOT / "benchmarks" / "CHECKSUMS.txt"


def sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_tree(root: Path) -> str:
    """Stable SHA-256 over a directory: hash of sorted 'relpath\\nsha\\n' lines.

    Independent of filesystem order and of where the tree is unpacked, so the
    same corpus produces the same digest on any machine.
    """
    entries = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        rel = p.relative_to(root).as_posix()
        entries.append(f"{rel}\n{sha256_file(p)}\n")
    return sha256_bytes("".join(entries).encode("utf-8"))


def load_checksums() -> dict[str, str]:
    """Parse benchmarks/CHECKSUMS.txt into {name: sha256} (shasum format)."""
    out: dict[str, str] = {}
    if not CHECKSUMS_PATH.exists():
        return out
    for line in CHECKSUMS_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            out[parts[1]] = parts[0]
    return out


def record_checksum(name: str, digest: str) -> None:
    """Add or update a `sha256␠␠name` line in CHECKSUMS.txt."""
    CHECKSUMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if CHECKSUMS_PATH.exists():
        lines = CHECKSUMS_PATH.read_text().splitlines()
    out: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and stripped.split()[1:2] == [name]:
            out.append(f"{digest}  {name}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{digest}  {name}")
    CHECKSUMS_PATH.write_text("\n".join(out) + "\n")


def verify_or_record(name: str, digest: str, strict: bool) -> None:
    """Verify a computed digest against the recorded one, or record it.

    Raises SystemExit on mismatch, or on a missing record when --strict.
    """
    recorded = load_checksums().get(name)
    if recorded is None:
        if strict:
            print(f"FAIL: no recorded checksum for {name} (computed {digest})",
                  file=sys.stderr)
            sys.exit(2)
        print(f"  no recorded checksum for {name}; recording {digest}")
        record_checksum(name, digest)
        return
    if recorded.lower() != digest.lower():
        print(
            f"FAIL: checksum mismatch for {name}\n"
            f"  recorded: {recorded}\n  computed: {digest}",
            file=sys.stderr,
        )
        sys.exit(2)
    print(f"  OK {name}: {digest}")


def fetch_hpoa(dest: Path, strict: bool) -> None:
    print(f"fetching phenotype.hpoa (v{HPO_RELEASE}) -> {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(HPOA_URL, dest)
    verify_or_record(f"phenotype.hpoa@v{HPO_RELEASE}", sha256_file(dest), strict)


def fetch_gsc(dest_dir: Path, strict: bool) -> None:
    print(f"fetching GSC-2024 -> {dest_dir}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(GSC_ZIP_URL) as resp:
        raw = resp.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        zf.extractall(dest_dir)
    # The zip may unpack into a nested top-level dir; score whatever holds
    # Text/ + Annotations/.
    root = dest_dir
    if not (root / "Text").exists():
        for child in dest_dir.iterdir():
            if child.is_dir() and (child / "Text").exists():
                root = child
                break
    verify_or_record(f"GSC-2024@v{HPO_RELEASE}", sha256_tree(root), strict)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--hpoa", action="store_true", help="fetch phenotype.hpoa")
    ap.add_argument("--gsc", action="store_true", help="fetch the GSC-2024 corpus")
    ap.add_argument("--hpoa-dest", default=None,
                    help="where to write phenotype.hpoa (default: the "
                         "semantic_similarity on-disk cache)")
    ap.add_argument("--gsc-dir", default=".tmp/GSC_2024",
                    help="where to unpack GSC-2024")
    ap.add_argument("--strict", action="store_true",
                    help="fail if an artefact has no recorded checksum yet")
    args = ap.parse_args()

    if not (args.hpoa or args.gsc):
        ap.error("nothing to do: pass --hpoa and/or --gsc")

    if args.hpoa:
        if args.hpoa_dest:
            dest = Path(args.hpoa_dest)
        else:
            from phenoscribe.semantic_similarity import _cache_path
            dest = Path(_cache_path(f"phenotype_{HPO_RELEASE}.hpoa"))
        fetch_hpoa(dest, args.strict)

    if args.gsc:
        fetch_gsc(Path(args.gsc_dir), args.strict)


if __name__ == "__main__":
    main()
