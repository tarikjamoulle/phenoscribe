"""Information-content-based semantic similarity for HPO terms.

The Phenomizer work (Köhler et al. 2009) scores term similarity by the
information content (IC) of the most informative common ancestor (MICA),
following Resnik (1995) and Lin (1998). A prediction that lands on a near-root
term such as "Phenotypic abnormality" shares only a low-IC ancestor with a
specific ground-truth term, so it scores near zero. A count of hops up the DAG
would reward those near-root predictions.

IC is computed from the HPO disease-annotation file (phenotype.hpoa):

    IC(t) = -log( freq(t) / N )

where freq(t) is the number of diseases annotated with term t after
propagating every annotation up to its ancestors (the "true-path rule"), and
N is the number of annotated diseases. Frequent (general) terms get low IC;
rare (specific) terms get high IC. The root has IC 0.

References:
  Resnik P. (1995) Using information content to evaluate semantic similarity.
  Lin D. (1998) An information-theoretic definition of similarity.
  Kohler S., Robinson P. et al. (2009) Phenomizer / Clinical diagnostics in
  human genetics with semantic similarity searches in ontologies.
"""

import json
import logging
import math
from collections import Counter
from pathlib import Path

import requests

from phenoscribe.hpo_index import release_asset_url
from phenoscribe.ontology import HPO_ROOT, PHENOTYPIC_ABNORMALITY

logger = logging.getLogger(__name__)

# Cache under the user cache dir so repeated runs (and tests) skip the download.
_CACHE_DIR = Path.home() / ".cache" / "phenoscribe" / "semantic_similarity"


def _cache_path(name: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / name


def _download_hpoa(release: str, dest: Path) -> None:
    """Download phenotype.hpoa of ``release`` to ``dest``.

    Writes to a temporary file first, so a broken download never leaves a
    half-written file that a later run takes for a good one.
    """
    logger.info("Downloading phenotype.hpoa (release %s) to %s", release, dest)
    tmp = dest.with_suffix(".part")
    url = release_asset_url(release, "phenotype.hpoa")
    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in response.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    tmp.replace(dest)


def _read_hpoa_annotations(hpoa_path: Path) -> list[tuple[str, str]]:
    """Yield (disease_id, hpo_id) for phenotype annotations, skipping NOT.

    Only aspect 'P' (phenotypic abnormality) rows are used. Negated rows
    (qualifier == "NOT") are dropped — they assert a term is absent.
    """
    pairs = []
    with open(hpoa_path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or line.startswith("database_id"):
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 11:
                continue
            disease_id, qualifier, hpo_id, aspect = cols[0], cols[2], cols[3], cols[10]
            if aspect != "P" or qualifier == "NOT":
                continue
            pairs.append((disease_id, hpo_id))
    return pairs


def _build_ic(hpo, hpoa_path: Path) -> dict[str, float]:
    """Compute IC per term with true-path propagation.

    For each (disease, term) annotation, the term and all of its ancestors are
    credited to that disease (true-path rule). freq(t) is the count of distinct
    diseases that reach t. IC(t) = -log(freq(t) / N).
    """
    graph = hpo.graph
    # disease -> set of terms reached (annotated + propagated to ancestors)
    disease_terms: dict[str, set[str]] = {}
    skipped = 0
    for disease_id, hpo_id in _read_hpoa_annotations(hpoa_path):
        try:
            terms = {str(a) for a in graph.get_ancestors(hpo_id)}
        except (KeyError, ValueError):
            skipped += 1
            continue
        terms.add(hpo_id)
        disease_terms.setdefault(disease_id, set()).update(terms)

    n_diseases = len(disease_terms)
    term_freq: Counter[str] = Counter()
    for terms in disease_terms.values():
        term_freq.update(terms)

    if not n_diseases:
        raise RuntimeError("No usable phenotype annotations found in phenotype.hpoa")

    # `-log(1.0)` is -0.0; normalise to 0.0 so the root reads cleanly.
    ic = {t: (-math.log(freq / n_diseases)) or 0.0 for t, freq in term_freq.items()}
    logger.info(
        "Built IC for %d terms from %d diseases (%d annotations skipped as unknown ids)",
        len(ic),
        n_diseases,
        skipped,
    )
    return ic


def get_ic_map(hpo) -> dict[str, float]:
    """Return the IC map for the loaded HPO, building and caching it on first use.

    The annotations come from the phenotype.hpoa of the same release as ``hpo``
    (its ``version``, e.g. ``2026-02-16``). The IC values are cached on disk per
    release, so repeated runs and the test suite do not download or compute again.
    """
    release = f"hp/releases/{hpo.version}"
    cache_file = _cache_path(f"ic_{hpo.version}.json")
    if cache_file.exists():
        with open(cache_file, encoding="utf-8") as fh:
            return json.load(fh)

    hpoa_path = _cache_path(f"phenotype_{hpo.version}.hpoa")
    if not hpoa_path.exists():
        _download_hpoa(release, hpoa_path)

    ic = _build_ic(hpo, hpoa_path)
    tmp = cache_file.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ic, fh)
    tmp.replace(cache_file)
    return ic


def ic_of(ic_map: dict[str, float], term: str) -> float:
    """IC of a single term. Unknown terms have IC 0 (treated as uninformative)."""
    return ic_map.get(term, 0.0)


def _ancestors_inclusive(hpo, term: str) -> set[str]:
    """Ancestors of `term` including the term itself."""
    ancestors = {str(a) for a in hpo.graph.get_ancestors(term)}
    ancestors.add(term)
    return ancestors


def mica_ic(ic_map: dict[str, float], hpo, a: str, b: str) -> float:
    """IC of the most informative common ancestor (MICA) of a and b.

    This is the Resnik (1995) similarity. Returns 0.0 if the terms share only
    the (uninformative) root, or if either term is unknown to the graph.
    """
    if a == b:
        return ic_of(ic_map, a)
    try:
        common = _ancestors_inclusive(hpo, a) & _ancestors_inclusive(hpo, b)
    except (KeyError, ValueError):
        return 0.0
    if not common:
        return 0.0
    return max(ic_of(ic_map, t) for t in common)


def resnik_similarity(ic_map: dict[str, float], hpo, a: str, b: str) -> float:
    """Resnik similarity = IC(MICA(a, b)). resnik(x, x) == IC(x)."""
    return mica_ic(ic_map, hpo, a, b)


def lin_similarity(ic_map: dict[str, float], hpo, a: str, b: str) -> float:
    """Lin (1998) similarity, normalised to [0, 1].

        lin(a, b) = 2 * IC(MICA) / (IC(a) + IC(b))

    lin(x, x) == 1 for any term with IC > 0. A specific ground-truth term paired
    with a near-root prediction (MICA IC ~= 0) scores ~0.
    """
    mica = mica_ic(ic_map, hpo, a, b)
    denom = ic_of(ic_map, a) + ic_of(ic_map, b)
    if denom <= 0:
        # Both terms are uninformative (root-ish). Identical -> 1, else 0.
        return 1.0 if a == b else 0.0
    return 2.0 * mica / denom


# Direction of an error relative to a ground-truth term.
DIR_EXACT = "exact"
DIR_NON_SPECIFIC = "non_specific"  # predicted an ANCESTOR of truth (recall side)
DIR_OVER_SPECIFIC = "over_specific"  # predicted a DESCENDANT of truth (precision side)
DIR_RELATED = "related"  # shares an informative ancestor but neither lineage
DIR_UNRELATED = "unrelated"

# Every term shares these ancestors, so they do not make two terms related.
_ROOTS = frozenset({HPO_ROOT, PHENOTYPIC_ABNORMALITY})


def error_direction(hpo, predicted: str, gt_term: str) -> str:
    """Classify a predicted term against one ground-truth term by direction.

    - exact:        same term
    - non_specific: predicted is an ancestor of truth (too general, recall miss)
    - over_specific: predicted is a descendant of truth (fabricated specificity)
    - related:      shares an ancestor below the roots, neither lineage
    - unrelated:    shares only the roots, or a term is unknown
    """
    if predicted == gt_term:
        return DIR_EXACT
    graph = hpo.graph
    try:
        if graph.is_ancestor_of(predicted, gt_term):
            return DIR_NON_SPECIFIC
        if graph.is_descendant_of(predicted, gt_term):
            return DIR_OVER_SPECIFIC
    except (KeyError, ValueError):
        return DIR_UNRELATED
    try:
        common = _ancestors_inclusive(hpo, predicted) & _ancestors_inclusive(hpo, gt_term)
    except (KeyError, ValueError):
        return DIR_UNRELATED
    return DIR_RELATED if common - _ROOTS else DIR_UNRELATED


def ic_distribution(ic_map: dict[str, float], terms) -> dict:
    """Summary statistics of the IC of a collection of predicted terms.

    Shows whether low-IC near-root terms dominate the predictions.
    Returns counts, quartiles, and a `low_ic_dominated` flag.
    """
    values = sorted(ic_of(ic_map, t) for t in terms)
    n = len(values)
    if n == 0:
        return {
            "count": 0,
            "min": 0.0,
            "median": 0.0,
            "mean": 0.0,
            "max": 0.0,
            "low_ic_count": 0,
            "low_ic_fraction": 0.0,
            "low_ic_dominated": False,
            "low_ic_threshold": 1.0,
        }

    def _pct(p: float) -> float:
        if n == 1:
            return values[0]
        idx = p * (n - 1)
        lo = math.floor(idx)
        hi = math.ceil(idx)
        if lo == hi:
            return values[lo]
        return values[lo] + (values[hi] - values[lo]) * (idx - lo)

    # IC < 1.0 means freq(t) > N/e: the term covers more than ~37% of diseases.
    # Such terms are near-root and carry little diagnostic specificity.
    low_ic_threshold = 1.0
    low_ic_count = sum(1 for v in values if v < low_ic_threshold)
    low_ic_fraction = low_ic_count / n
    return {
        "count": n,
        "min": values[0],
        "median": _pct(0.5),
        "mean": sum(values) / n,
        "max": values[-1],
        "low_ic_count": low_ic_count,
        "low_ic_fraction": low_ic_fraction,
        "low_ic_dominated": low_ic_fraction > 0.5,
        "low_ic_threshold": low_ic_threshold,
    }
