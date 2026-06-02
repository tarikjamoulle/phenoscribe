"""HPO is_a graph and the true-path (annotation propagation) rule.

The HPO is a DAG. The true-path rule says that if a term applies to a
patient, every ancestor of that term applies too. Downstream tools
(Exomiser, LIRICAL, Phen2Gene, Phenomizer) assume the annotation set is
ancestor-closed before they score it. Phenoscribe emits the specific
(leaf-like) terms the LLM selected, so this module computes the transitive
ancestor closure on demand.

The graph is built from the pinned OBO release that also seeds ChromaDB, so
term IDs and edges match the rest of the pipeline. Graph operations use
hpo-toolkit (Peter Robinson's recommended library).
"""

import logging

import hpotk
from hpotk.graph import IncrementalCsrGraphFactory
from hpotk.model import TermId

from phenoscribe.hpo_index import parse_obo

logger = logging.getLogger(__name__)

# Root of the HPO ("All", HP:0000001). get_ancestors of any term reaches it.
HPO_ROOT = "HP:0000001"
# "Phenotypic abnormality" — the practical root downstream phenotype tools use.
PHENOTYPIC_ABNORMALITY = "HP:0000118"


class HpoGraph:
    """is_a graph over a single HPO release, with name lookup.

    Wraps an hpotk OntologyGraph built from the OBO is_a edges. Holds the
    id -> canonical-name map so closures can be labelled.
    """

    def __init__(self, graph, names: dict[str, str]):
        self._graph = graph
        self._names = names

    @property
    def graph(self):
        return self._graph

    def name(self, hpo_id: str) -> str:
        """Canonical label for an HP id, or empty string if unknown."""
        return self._names.get(hpo_id, "")

    def contains(self, hpo_id: str) -> bool:
        return hpo_id in self._names

    def ancestors(self, hpo_id: str, include_source: bool = False) -> set[str]:
        """Transitive ancestors of a term over the is_a graph.

        Returns HP id strings. Unknown ids return an empty set (or just the
        id itself when include_source is True) so callers never crash on a
        code that is absent from this release.
        """
        if hpo_id not in self._names:
            logger.warning("ancestors() called for unknown HPO id: %s", hpo_id)
            return {hpo_id} if include_source else set()
        anc = self._graph.get_ancestors(hpo_id, include_source=include_source)
        return {str(a) for a in anc}

    def closure(self, hpo_ids: list[str] | set[str]) -> set[str]:
        """Ancestor-closed set: the input terms plus all their ancestors.

        This is the true-path expansion of an annotation set. The root and
        "Phenotypic abnormality" fall out naturally because every phenotype
        term descends from them.
        """
        result: set[str] = set()
        for hpo_id in hpo_ids:
            result.add(hpo_id)
            result |= self.ancestors(hpo_id, include_source=False)
        return result


_GRAPH_CACHE: dict[str, HpoGraph] = {}


def load_hpo_graph(obo_path: str) -> HpoGraph:
    """Build (and cache) the HPO is_a graph from an OBO file.

    Parsing ~19k terms and building the CSR graph takes a couple of seconds,
    so the result is cached per OBO path for the life of the process.
    """
    if obo_path in _GRAPH_CACHE:
        return _GRAPH_CACHE[obo_path]

    terms = parse_obo(obo_path)
    names = {t["id"]: t["name"] for t in terms}

    edges: list[tuple[TermId, TermId]] = []
    for t in terms:
        child = TermId.from_curie(t["id"])
        for parent in t["parents"]:
            edges.append((child, TermId.from_curie(parent)))

    graph = IncrementalCsrGraphFactory().create_graph(edges)
    logger.info(
        "Built HPO is_a graph from %s: %d terms, %d edges, root %s",
        obo_path, len(names), len(edges), graph.root,
    )

    hpo = HpoGraph(graph, names)
    _GRAPH_CACHE[obo_path] = hpo
    return hpo


def propagate_matches(matches: list[dict], obo_path: str) -> list[dict]:
    """Expand a leaf match list to its ancestor closure.

    Each input match is a dict with at least hpo_id and hpo_term. The output
    is the true-path-closed set of terms: every input leaf is kept as-is, and
    every ancestor not already present is added as a derived term.

    Output rows carry:
      hpo_id, hpo_term, origin ("leaf" or "ancestor"),
      derived_from (sorted list of leaf ids that imply an ancestor term).

    Rows are sorted leaves first (input order), then ancestors by id, so the
    closure is deterministic.
    """
    hpo = load_hpo_graph(obo_path)

    leaf_ids = {m["hpo_id"] for m in matches}
    # leaf id -> the ancestors it implies (excluding itself)
    derived_by: dict[str, set[str]] = {}
    for m in matches:
        derived_by[m["hpo_id"]] = hpo.ancestors(m["hpo_id"], include_source=False)

    # Which leaves imply each ancestor (for traceability / debugging).
    ancestor_sources: dict[str, set[str]] = {}
    for leaf_id, ancs in derived_by.items():
        for anc in ancs:
            if anc in leaf_ids:
                continue  # already an explicit leaf; keep it as a leaf row
            ancestor_sources.setdefault(anc, set()).add(leaf_id)

    rows: list[dict] = []
    for m in matches:
        rows.append({
            "hpo_id": m["hpo_id"],
            "hpo_term": m.get("hpo_term", "") or hpo.name(m["hpo_id"]),
            "origin": "leaf",
            "derived_from": [],
        })

    for anc_id in sorted(ancestor_sources):
        rows.append({
            "hpo_id": anc_id,
            "hpo_term": hpo.name(anc_id),
            "origin": "ancestor",
            "derived_from": sorted(ancestor_sources[anc_id]),
        })

    logger.info(
        "True-path expansion: %d leaf term(s) -> %d term(s) (%d ancestor(s) added)",
        len(matches), len(rows), len(ancestor_sources),
    )
    return rows
