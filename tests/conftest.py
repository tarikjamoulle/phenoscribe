"""Shared test setup: repository paths, data fixtures, the script loader and common helpers.

Every test runs in a fresh working folder that holds a small ontology at the
default config paths (data/hpo/hp.obo), so no test reads the repository's
data/ or config.yaml by accident. A test of the real ontology or the real
search index asks for the ``hpo_obo`` or ``chroma_db`` fixture; it skips until
``phenoscribe setup`` has run.
"""

import importlib.util
import os
import sys
from pathlib import Path

import openpyxl
import pytest
from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

from phenoscribe import pipeline
from phenoscribe.config import DEFAULT_MODELS, LLMConfig
from phenoscribe.hpo_index import DEFAULT_HPO_RELEASE, index_ready, release_date, release_tag, seed_chromadb
from phenoscribe.output import write_excel

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SCRIPTS = REPO_ROOT / "scripts"
SHIPPED_CONFIG = REPO_ROOT / "config.yaml"
HPO_OBO = REPO_ROOT / "data" / "hpo" / "hp.obo"
CHROMA_DB = REPO_ROOT / "data" / "chroma_db"
SETUP_HINT = "run `uv run phenoscribe setup --no-models` first"

# ChromaDB, hpo-toolkit and the IC table download a model or the pinned release on first use.
INDEX_MODEL_DIR = Path(ONNXMiniLM_L6_V2.DOWNLOAD_PATH) / "onnx"
HPO_TOOLKIT_FILE = Path.home() / ".hpo-toolkit" / "HP" / f"hp.{release_tag(DEFAULT_HPO_RELEASE)}.json"
IC_FILE = Path.home() / ".cache" / "phenoscribe" / "semantic_similarity" / f"ic_{release_date(DEFAULT_HPO_RELEASE)}.json"

# A small ontology of the pinned release: the root, Phenotypic abnormality, and
# five terms under it, two with an EXACT synonym.
MINI_OBO_TERMS = 7
MINI_OBO = (
    "format-version: 1.2\n"
    f"data-version: {DEFAULT_HPO_RELEASE}\n\n"
    "[Term]\nid: HP:0000001\nname: All\n\n"
    "[Term]\nid: HP:0000118\nname: Phenotypic abnormality\nis_a: HP:0000001\n\n"
    "[Term]\nid: HP:0001945\nname: Fever\n" 'synonym: "Pyrexia" EXACT []\nis_a: HP:0000118\n\n'
    "[Term]\nid: HP:0002315\nname: Headache\n" 'synonym: "Cephalgia" EXACT []\nis_a: HP:0000118\n\n'
    "[Term]\nid: HP:0012378\nname: Fatigue\nis_a: HP:0000118\n\n"
    "[Term]\nid: HP:0000458\nname: Anosmia\nis_a: HP:0000118\n\n"
    "[Term]\nid: HP:0002027\nname: Abdominal pain\nis_a: HP:0000118\n"
)

_NEEDS_DATA = {"hpo_obo", "chroma_db"}


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Mark the tests that need local data, and the tests that would download a release."""
    downloads = set()
    if not INDEX_MODEL_DIR.is_dir():
        downloads.add("index_model")
    if not HPO_TOOLKIT_FILE.is_file():
        downloads |= {"hpo", "ic_map"}
    if not IC_FILE.is_file():
        downloads.add("ic_map")
    for item in items:
        names = set(getattr(item, "fixturenames", ()))
        if names & _NEEDS_DATA:
            item.add_marker(pytest.mark.needs_hpo_data)
        if names & downloads:
            item.add_marker(pytest.mark.network)


# --- the working folder and the speaker pass -------------------------------------------


@pytest.fixture(scope="session")
def _work_folder(tmp_path_factory):
    folder = tmp_path_factory.mktemp("work")
    obo = folder / "data" / "hpo" / "hp.obo"
    obo.parent.mkdir(parents=True)
    obo.write_text(MINI_OBO, encoding="utf-8")
    return folder


@pytest.fixture
def mini_hpo_obo(_work_folder) -> str:
    """The path of MINI_OBO, the ontology at the default config path of each test."""
    return str(_work_folder / "data" / "hpo" / "hp.obo")


@pytest.fixture(autouse=True)
def run_in_a_work_folder(monkeypatch, _work_folder):
    monkeypatch.chdir(_work_folder)


@pytest.fixture(autouse=True)
def no_speaker_pass(request, monkeypatch):
    """Stub the speaker pass, an LLM step, unless the test is marked ``real_speaker_pass``."""
    if request.node.get_closest_marker("real_speaker_pass") is None:
        monkeypatch.setattr(pipeline, "find_speakers", lambda *a, **k: "")


# --- local data and downloads -----------------------------------------------------------


@pytest.fixture(scope="session")
def hpo_obo() -> str:
    """The real hp.obo of the pinned release (data/hpo/hp.obo)."""
    if not HPO_OBO.is_file():
        pytest.skip(f"needs {HPO_OBO.relative_to(REPO_ROOT)}: {SETUP_HINT}")
    return str(HPO_OBO)


@pytest.fixture(scope="session")
def chroma_db() -> str:
    """The real search index (data/chroma_db)."""
    if not index_ready(str(CHROMA_DB), DEFAULT_HPO_RELEASE):
        pytest.skip(f"needs the search index in {CHROMA_DB.relative_to(REPO_ROOT)}: {SETUP_HINT}")
    return str(CHROMA_DB)


@pytest.fixture(scope="session")
def index_model() -> None:
    """The ONNX model that embeds the search index (downloaded once into ~/.cache/chroma).

    A test that builds an index asks for this fixture, so it carries the
    ``network`` marker until the model is in the cache.
    """


@pytest.fixture
def mini_index(tmp_path, mini_hpo_obo, index_model) -> str:
    """A search index of MINI_OBO in tmp_path/chroma, built with the default model."""
    chroma_path = str(tmp_path / "chroma")
    seed_chromadb(mini_hpo_obo, chroma_path, embedding_model="default")
    return chroma_path


@pytest.fixture(scope="session")
def hpo():
    """The pinned release through hpo-toolkit (downloaded once into ~/.hpo-toolkit)."""
    from phenoscribe.validate import load_hpo

    return load_hpo()


@pytest.fixture(scope="session")
def ic_map(hpo):
    """The information content of each term (computed once into ~/.cache/phenoscribe)."""
    from phenoscribe.semantic_similarity import get_ic_map

    return get_ic_map(hpo)


@pytest.fixture
def live_llm() -> LLMConfig:
    """The LLM of a live test: Anthropic or OpenAI, whichever key is set."""
    for provider, variable in (("anthropic", "ANTHROPIC_API_KEY"), ("openai", "OPENAI_API_KEY")):
        if os.environ.get(variable):
            return LLMConfig(provider=provider, model=DEFAULT_MODELS[provider])
    pytest.skip("live LLM test: set ANTHROPIC_API_KEY or OPENAI_API_KEY")


# --- helpers ------------------------------------------------------------------------------


def load_script(name: str):
    """Import scripts/<name>.py (for example "eval_pii" or "dev/eval_recall"); scripts/ is no package."""
    module_name = Path(name).name
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def write_codes(path, patient_id: str, codes: list[str]) -> None:
    """A semicolon workbook with one patient and these HPO codes."""
    matches = [{"hpo_term": "t", "hpo_id": c, "patient_verbatim": ""} for c in codes]
    write_excel(patient_id, matches, str(path), fmt="semicolon")


def read_sheet(path, sheet: str | None = None) -> tuple[list, list[tuple]]:
    """(header, data rows) of the first sheet of a workbook, or of ``sheet``."""
    wb = openpyxl.load_workbook(path)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    header = [c.value for c in ws[1]]
    return header, list(ws.iter_rows(min_row=2, values_only=True))


def finding(term="Fatigue", code="HP:0012378", source="", verbatim="", mentions=None, negated=False, **more):
    """One coded finding, the way match_hpo returns it."""
    return {"hpo_id": code, "hpo_term": term, "clinical_term": term.lower(), "source": source,
            "patient_verbatim": verbatim, "mentions": mentions or [], "negated": negated, **more}
