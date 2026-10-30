"""Gradio web UI for Phenoscribe.

Two tools on one calm page: remove the personal details, or remove them and
then find the HPO codes. The steps: a key (only for the codes), the files,
Start, a review of the personal details for each file, then the codes or the
clean texts. The app decides the work for each file (plan_batch): audio is
transcribed, a .txt or .docx is read as it is, and a saved transcript of the
same audio is reused. Only an approved review goes to the AI provider.
Results go to `PHENOSCRIBE_OUTPUT_DIR` (default `/app/output`; the start
scripts set `Documents/Phenoscribe results`).
"""

import html
import logging
import os
from datetime import datetime
from pathlib import Path

import anthropic
import gradio as gr
import openai
import requests

from phenoscribe.config import load_config
from phenoscribe.llm import use_api_key
from phenoscribe.match_hpo import finding_status
from phenoscribe.output import dedup_by_code
from phenoscribe.pipeline import (
    BatchResult,
    FilePlan,
    approve_clear_files,
    approve_the_rest,
    code_batch,
    finish_clean,
    plan_batch,
    prepare_batch,
    write_clean_files,
)
from phenoscribe.review import (
    APPROVED,
    AUTO,
    NEEDS_REVIEW,
    OPTIONAL_TYPES,
    TYPE_NAMES,
    ReviewError,
    add_value,
    approve,
    entries,
    highlighted,
    leaks,
    load_review,
    open_reviews,
    possibly_missed,
    save_review,
    set_keep_types,
    set_removed,
    summary_line,
    type_name,
    warnings,
)

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

OUTPUT_DIR = Path(os.environ.get("PHENOSCRIBE_OUTPUT_DIR", "/app/output"))
CONFIG_PATH = os.environ.get("PHENOSCRIBE_CONFIG", "config.yaml")
LOG_PATH = OUTPUT_DIR / "phenoscribe.log"

LANGUAGE_CHOICES = [
    ("French", "fr"),
    ("English", "en"),
    ("Dutch", "nl"),
    ("German", "de"),
    ("Spanish", "es"),
    ("Italian", "it"),
]

PROVIDER_CHOICES = [("OpenAI", "openai"), ("Anthropic", "anthropic")]
PROVIDER_NAMES = dict((value, label) for label, value in PROVIDER_CHOICES)

# Model for a provider that config.yaml does not name. The configured
# provider uses config.llm.model.
DEFAULT_MODELS = {
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-6",
}

PROVIDER_ENV_VAR = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

RESULT_HEADERS = ["Patient", "Symptom (patient words)", "HPO term", "Code", "Status", "When"]

# The browser keeps the key in local storage. Gradio encrypts the stored value
# with this secret; it must stay the same across restarts, or the saved key is
# lost. It protects nothing, so the page calls it local to this browser only.
KEY_STORAGE = {"storage_key": "phenoscribe_api_key", "secret": "phenoscribe-local"}

ACTION_TEXT = {
    "transcribe": ("Audio", "Transcribe, then remove personal details"),
    "reuse": ("Audio", "Saved transcript found"),
    "transcript": ("Transcript", "Remove personal details"),
}

# The two tools.
MODE_CODE, MODE_CLEAN = "code", "clean"
MODE_CHOICES = [
    ("Remove personal details, then find HPO codes", MODE_CODE),
    ("Remove personal details only", MODE_CLEAN),
]
REMOVE_CHOICES = [(label, key) for key, label in OPTIONAL_TYPES.items()]
ALWAYS_REMOVED = "names, places, organizations, phones, emails and ID numbers"
REMOVE_STORAGE = {"storage_key": "phenoscribe_remove_types", "secret": "phenoscribe-local"}

# Colors of the highlighted types in the review text (light and dark both read).
TYPE_COLORS = {
    "name": "#E8A87C", "place": "#85C1B9", "organization": "#A9B8E0", "phone": "#D4A5D9",
    "email": "#D4A5D9", "ID number": "#D4A5D9", "web address": "#D4A5D9", "date": "#E6D38A",
    "age": "#B5D99C", "gender": "#B5D99C", "job title": "#B5D99C", "other": "#C9C9C9",
}
ADD_TYPES = [(TYPE_NAMES[t][0].capitalize(), t) for t in TYPE_NAMES]

APPROVE_REST_QUESTION = (
    "Approve every file that still waits, with no check? A name that the app missed in "
    "these files goes to the AI provider."
)

REVIEW_HEADERS = ["Remove", "Found in the text", "Type", "Times"]
MISSED_HEADERS = ["Remove", "Word after a title", "Where"]


# --- look -----------------------------------------------------------------------

THEME = gr.themes.Base(
    primary_hue=gr.themes.Color(
        c50="#EEF4F3", c100="#D5E5E2", c200="#ABCBC6", c300="#7FAEA8", c400="#4F8C85",
        c500="#1F5C55", c600="#1A4F49", c700="#16443F", c800="#113632", c900="#0C2724",
        c950="#081A18",
    ),
    neutral_hue="gray",
    radius_size=gr.themes.sizes.radius_lg,
    font=[gr.themes.GoogleFont("Instrument Sans"), "system-ui", "sans-serif"],
    font_mono=[gr.themes.GoogleFont("IBM Plex Mono"), "ui-monospace", "monospace"],
).set(
    body_background_fill="#F4F5F2",
    body_background_fill_dark="#111614",
    body_text_color="#18201D",
    body_text_color_dark="#E6EAE7",
    block_background_fill="transparent",
    block_background_fill_dark="transparent",
    block_border_width="0px",
    block_shadow="none",
    block_label_background_fill="transparent",
    block_label_background_fill_dark="transparent",
    input_background_fill="#FFFFFF",
    input_background_fill_dark="#1B2220",
    input_border_width="1px",
    input_border_color="#C9D3CE",
    input_border_color_dark="#34403B",
    button_primary_background_fill="#1F5C55",
    button_primary_background_fill_hover="#16443F",
    button_primary_background_fill_dark="#2E7A71",
    button_primary_background_fill_hover_dark="#358A80",
    button_primary_text_color="#FFFFFF",
    table_even_background_fill="#FFFFFF",
    table_even_background_fill_dark="#1A201E",
    table_odd_background_fill="#F8FAF8",
    table_odd_background_fill_dark="#1E2523",
    table_border_color="#E1E4DF",
    table_border_color_dark="#2C3632",
    button_border_width="1px",
    button_secondary_background_fill="#FFFFFF",
    button_secondary_background_fill_dark="#1B2220",
    button_secondary_border_color="#C9D3CE",
    button_secondary_border_color_dark="#34403B",
)

HEAD = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    'family=Instrument+Serif&display=swap">'
)

CSS = """
:root { --ps-card: #FFFFFF; --ps-line: #E1E4DF; --ps-soft: #F0F2EE; --ps-muted: #5A6560;
        --ps-ink2: #3E4A45; --ps-accent: #1F5C55; --ps-ok: #1F7A4D; --ps-warn-bg: #FBF5EC;
        --ps-warn: #6B4A12; --ps-warn-tag: #F3E6CF; --ps-bad-bg: #FBF1EF; --ps-bad: #8A2E20;
        --ps-tag-audio: #E7EEF0; --ps-tag-text: #EEEBE3; --ps-new: #D5E5E2; }
.dark { --ps-card: #1A201E; --ps-line: #2C3632; --ps-soft: #222A27; --ps-muted: #A3ADA8;
        --ps-ink2: #C4CCC8; --ps-accent: #7FC3B8; --ps-ok: #6FCF97; --ps-warn-bg: #2A2416;
        --ps-warn: #E9C98B; --ps-warn-tag: #3D3420; --ps-bad-bg: #2C1B18; --ps-bad: #F0A595;
        --ps-tag-audio: #24323A; --ps-tag-text: #34302A; --ps-new: #1F4A45; }
.gradio-container { max-width: 880px !important; margin: 0 auto !important; }
footer { display: none !important; }
.ps-header { display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
             justify-content: space-between; padding: 8px 0 16px;
             border-bottom: 1px solid var(--ps-line); margin-bottom: 8px; }
.ps-title { font-family: 'Instrument Serif', Georgia, serif; font-size: 32px; letter-spacing: -0.01em; }
.ps-sub { font-size: 14px; color: var(--ps-muted); margin-left: 12px; }
.ps-pill { display: inline-flex; gap: 8px; align-items: center; font-size: 13px;
           color: var(--ps-ink2); background: var(--ps-soft); border-radius: 999px; padding: 6px 12px; }
.ps-card { background: var(--ps-card) !important; border: 1px solid var(--ps-line) !important;
           border-radius: 14px !important; padding: 20px 24px !important; }
.ps-step { display: flex; gap: 14px; align-items: center; font-weight: 600; }
.ps-num { flex: none; width: 28px; height: 28px; border-radius: 999px; background: var(--ps-accent);
          color: #FFFFFF; font-size: 14px; display: inline-flex; align-items: center; justify-content: center; }
.dark .ps-num { color: #0C2724; }
.ps-note { font-size: 13px; color: var(--ps-muted); }
.ps-ok { color: var(--ps-ok); font-weight: 600; }
.ps-seg .wrap { display: inline-flex !important; gap: 0 !important; background: var(--ps-soft);
                border-radius: 10px; padding: 3px; }
.ps-seg label { border: 0 !important; border-radius: 8px !important; background: transparent !important;
                box-shadow: none !important; }
.ps-seg label.selected { background: var(--ps-card) !important; box-shadow: 0 1px 2px rgba(0,0,0,.12) !important; }
.ps-seg input[type=radio] { display: none; }
.ps-card .form { background: transparent !important; border: 0 !important; box-shadow: none !important; }
.ps-card .ps-drop { border: 1.5px dashed #B9C6C0 !important; border-radius: 12px !important; }
.ps-list { border: 1px solid var(--ps-line); border-radius: 12px; overflow: hidden; }
.ps-row { display: flex; flex-wrap: wrap; gap: 6px 16px; align-items: center; padding: 12px 16px;
          border-top: 1px solid var(--ps-line); font-size: 14px; color: var(--ps-ink2); }
.ps-row:first-child { border-top: 0; }
.ps-row.skip { background: var(--ps-warn-bg); color: var(--ps-warn); }
.ps-row.bad { background: var(--ps-bad-bg); color: var(--ps-bad); }
.ps-file { font-family: 'IBM Plex Mono', ui-monospace, monospace; flex: 1 1 170px; color: inherit; }
.ps-tag { font-size: 12px; border-radius: 6px; padding: 2px 8px; background: var(--ps-tag-audio); }
.ps-tag.text { background: var(--ps-tag-text); }
.ps-tag.skip { background: var(--ps-warn-tag); }
.ps-what { flex: 1 1 260px; }
.ps-count { font-size: 15px; font-weight: 600; margin: 4px 2px 10px; }
.ps-done { margin-top: 10px; }
.ps-done summary { cursor: pointer; font-size: 15px; font-weight: 600; margin: 4px 2px 10px; }
@keyframes ps-flash { from { background: var(--ps-new); } to { background: transparent; } }
.ps-row.new { animation: ps-flash 1.6s ease-out; }
.ps-working { --font-mono: 'Instrument Sans', system-ui, sans-serif; }
.ps-working .pending { opacity: 1 !important; }
.ps-working .translucent { background: transparent !important; }
.ps-working .progress-bar-wrap { background: var(--ps-soft) !important; border: 1px solid var(--ps-line) !important;
                                 border-radius: 999px !important; height: 10px !important; }
.ps-working .progress-bar { background: var(--ps-accent) !important; border-radius: 999px !important; }
.ps-slim .wrap { font-size: 0 !important; flex-direction: row !important; min-height: 0 !important; }
.ps-slim .wrap > * { display: none !important; }
.ps-slim .wrap::after { content: "+ Drop more files here, or click to add"; font-size: 14px;
                        color: var(--ps-ink2); }
.ps-start button { min-height: 52px; font-size: 16px !important; }
.ps-table, .ps-table * { font-family: 'Instrument Sans', system-ui, sans-serif !important; }
.ps-h1 { font-family: 'Instrument Serif', Georgia, serif; font-size: 40px; line-height: 1.1; margin: 8px 0 0; }
.ps-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }
.ps-tile { background: var(--ps-card); border: 1px solid var(--ps-line); border-radius: 14px; padding: 14px 20px; }
.ps-tile b { display: block; font-family: 'Instrument Serif', Georgia, serif; font-weight: 400;
             font-size: 36px; line-height: 1.1; }
.ps-tile b.warn { color: #8A5A0B; }
.ps-queue .ps-row { cursor: default; }
.ps-row.done { color: var(--ps-ok); }
.ps-row.here { box-shadow: inset 4px 0 0 var(--ps-accent); font-weight: 600; }
.ps-line { font-size: 15px; font-weight: 600; }
.ps-leak-ok { color: var(--ps-ok); font-size: 13px; }
.ps-leak-bad { color: var(--ps-bad); font-size: 13px; font-weight: 600; }
.ps-text { max-height: 420px; overflow-y: auto; }
.dark .ps-tile b.warn { color: #E9C98B; }
"""


# --- logic ------------------------------------------------------------------------


def env_key_for(provider: str) -> str:
    return os.environ.get(PROVIDER_ENV_VAR.get(provider, ""), "")


def provider_for_key(api_key: str) -> str:
    """Anthropic keys start with "sk-ant-"; everything else is taken as OpenAI."""
    return "anthropic" if api_key.strip().startswith("sk-ant-") else "openai"


def model_for(provider: str, config) -> str:
    if provider == config.llm.provider:
        return config.llm.model
    return DEFAULT_MODELS[provider]


def plain_error(error: Exception) -> str:
    """One plain line for a failed file. The full trace is in the log file."""
    if isinstance(error, (openai.AuthenticationError, anthropic.AuthenticationError)):
        return "Key rejected"
    if isinstance(error, (openai.RateLimitError, anthropic.RateLimitError)):
        return "The AI account has no credit left, or hit its limit. Check the account billing."
    if isinstance(
        error,
        (openai.APIConnectionError, anthropic.APIConnectionError, requests.ConnectionError),
    ):
        return "No internet. Check the connection and start again."
    message = str(error)
    if "Unsupported file type" in message or "Failed to load audio" in message:
        return "Audio format not supported"
    if type(error).__module__.startswith("av."):
        return "Audio format not supported"
    return f"Failed ({type(error).__name__}). Details are in {LOG_PATH.name}"


def check_key(provider: str, api_key: str) -> str:
    """Make one small call (list the models). Returns "" when the key works."""
    try:
        if provider == "anthropic":
            anthropic.Anthropic(api_key=api_key).models.list(limit=1)
        else:
            openai.OpenAI(api_key=api_key).models.list()
    except Exception as e:  # noqa: BLE001 — every failure becomes one plain line
        logger.warning("Key check failed: %r", e)
        return plain_error(e)
    return ""


def key_summary(provider: str, how: str) -> str:
    return (
        f'<span class="ps-ok">✓</span> {PROVIDER_NAMES[provider]} key {how}'
    )


def load_saved_key(saved: str):
    """On page load: a saved key (or an env key) shows the short key line."""
    if saved:
        provider = provider_for_key(saved)
        return saved, provider, key_summary(provider, "saved in this browser"), *_key_view(True)
    for provider in PROVIDER_ENV_VAR:
        if env_key_for(provider):
            return "", provider, key_summary(provider, "from the environment"), *_key_view(True)
    return "", "openai", "", *_key_view(False)


def _key_view(have_key: bool):
    """(entry box, short line) visibility."""
    return gr.update(visible=not have_key), gr.update(visible=have_key)


def on_key_entered(api_key: str, remember: bool):
    """When the key box loses focus: check the key, then fold the box away."""
    api_key = api_key.strip()
    if not api_key:
        return gr.update(), "", "", gr.update(), *_key_view(False)
    provider = provider_for_key(api_key)
    problem = check_key(provider, api_key)
    if problem:
        return provider, "", problem, gr.update(), *_key_view(False)
    how = "works · kept in this browser only" if remember else "works · for this session only"
    saved = api_key if remember else ""
    return provider, key_summary(provider, how), "", saved, *_key_view(True)


def add_files(new, current: list[str]) -> tuple[None, list[str], list[str]]:
    """Add uploads to the selection. Returns (empty drop box, selection, new paths).

    The drop box empties at once; the plan list shows the files, with the new
    rows highlighted, so the user sees where they went.
    """
    new = [new] if isinstance(new, str) else (new or [])
    added = [p for p in new if p not in current]
    return None, current + added, added


def file_plan(files: list[str], language: str) -> list[FilePlan]:
    config = load_config(CONFIG_PATH)
    config.transcription.language = language
    return plan_batch(files, config, OUTPUT_DIR)


def count_line(plans: list[FilePlan]) -> str:
    """'✓ 3 files added · 1 skipped' above the list."""
    ready = sum(1 for p in plans if p.action != "skip")
    skipped = len(plans) - ready
    text = f"{ready} file{'s' if ready != 1 else ''} added"
    if skipped:
        text += f" · {skipped} skipped"
    return f'<div class="ps-count"><span class="ps-ok">✓</span> {text}</div>'


def plan_html(plans: list[FilePlan], fresh: list[str] = ()) -> str:
    """The file list. Rows for the paths in ``fresh`` (just added) flash once."""
    if not plans:
        return ""
    rows = []
    for p in plans:
        name = html.escape(p.name)
        new = " new" if p.path in fresh else ""
        if p.action == "skip":
            rows.append(
                f'<div class="ps-row skip{new}"><span class="ps-file">{name}</span>'
                f'<span class="ps-tag skip">Skipped</span>'
                f'<span class="ps-what">{html.escape(p.note)}</span></div>'
            )
            continue
        kind, what = ACTION_TEXT[p.action]
        tag = "ps-tag text" if kind == "Transcript" else "ps-tag"
        rows.append(
            f'<div class="ps-row{new}"><span class="ps-file">{name}</span>'
            f'<span class="{tag}">{kind}</span><span class="ps-what">{what}</span></div>'
        )
    return f'{count_line(plans)}<div class="ps-list">{"".join(rows)}</div>'


def selection_view(files: list[str], fresh: list[str], language: str, api_key: str, mode: str = MODE_CODE):
    """Everything that follows the file selection: list, language, reuse, Start,
    and the drop box, which shrinks to a slim bar once files are in the list.

    Start needs a key only for the HPO codes: the removal runs on this computer."""
    plans = file_plan(files, language)
    runnable = [p for p in plans if p.action != "skip"]
    has_audio = any(p.action in ("transcribe", "reuse") for p in plans)
    reusable = [p.name for p in plans if p.action == "reuse"]
    have_key = bool(api_key.strip()) or any(env_key_for(p) for p in PROVIDER_ENV_VAR)
    n = len(runnable)
    label = f"Start with {n} file{'s' if n != 1 else ''}" if n else "Start"
    can_start = bool(n) and (have_key or mode == MODE_CLEAN)
    drop_classes = ["ps-drop", "ps-slim"] if files else ["ps-drop"]
    return (
        plan_html(plans, fresh),
        gr.update(visible=has_audio),
        gr.update(choices=reusable, value=[], visible=bool(reusable)),
        gr.update(value=label, interactive=can_start),
        gr.update(visible=bool(files)),
        gr.update(height=56 if files else 150, elem_classes=drop_classes),
        gr.update(visible=not files),
        [],
    )


def result_rows(batch: BatchResult) -> list[list]:
    """The coded rows, one per finding, deduplicated the same way as the Excel."""
    rows = []
    for r in batch.files:
        for m in dedup_by_code(r.matches, "detailed"):
            status = finding_status(m)
            if m.get("needs_review"):
                status += " · Review"
            rows.append(
                [
                    r.patient_id,
                    m.get("patient_verbatim") or m.get("clinical_term") or "",
                    m["hpo_term"] if m.get("hpo_id") else "No good match",
                    m.get("hpo_id") or "—",
                    status,
                    when_text(m),
                ]
            )
    return rows


def when_text(match: dict) -> str:
    """'12 %' (first mention in the interview), plus '· 3×' for repeats."""
    first = match.get("first_position")
    if first is None:
        return ""
    n = match.get("n_mentions") or 1
    return f"{first:.0f} %" + (f" · {n}×" if n > 1 else "")


def filter_rows(rows: list[list], which: str) -> list[list]:
    return [r for r in rows if "Review" in r[4]] if which == "To review" else rows


def summary_html(batch: BatchResult, rows: list[list], when: datetime) -> str:
    codes = sum(1 for r in rows if r[3] != "—")
    review = sum(1 for r in rows if "Review" in r[4])
    done = sum(1 for r in batch.files if not r.error)
    total = len(batch.files)
    return (
        f'<div class="ps-h1">Results</div>'
        f'<div class="ps-note">Run of {when:%d %B %Y, %H:%M} · {total} file{"s" if total != 1 else ""}</div>'
        f'<div class="ps-tiles" style="margin-top:16px">'
        f'<div class="ps-tile">HPO codes<b>{codes}</b></div>'
        f'<div class="ps-tile">To review<b class="warn">{review}</b></div>'
        f'<div class="ps-tile">Files done<b>{done} of {total}</b></div></div>'
    )


def status_html(batch: BatchResult) -> str:
    """The failed files, open. Then the coded interviews, closed under one line."""
    bad, done = [], []
    for r in batch.files:
        who = f"{html.escape(r.filename)} → {html.escape(r.patient_id)}"
        if r.error:
            bad.append(
                f'<div class="ps-row bad"><span class="ps-file">✗ {who}</span>'
                f'<span>{html.escape(plain_error(r.error))}</span></div>'
            )
        else:
            coded = dedup_by_code(r.matches, "detailed")
            review = sum(1 for m in coded if m.get("needs_review"))
            done.append(
                f'<div class="ps-row"><span class="ps-file"><span class="ps-ok">✓</span> {who}</span>'
                f"<span>{len(coded)} findings · {review} to review</span></div>"
            )
    page = f'<div class="ps-list">{"".join(bad)}</div>' if bad else ""
    if done:
        n = len(done)
        page += (f'<details class="ps-done"><summary><span class="ps-ok">✓</span> '
                 f'{n} interview{"s" if n != 1 else ""} coded</summary>'
                 f'<div class="ps-list">{"".join(done)}</div></details>')
    return page


def working_html(plans: list[FilePlan]) -> str:
    """What the page shows while the files are read, above Gradio's progress bar."""
    runnable = [p for p in plans if p.action != "skip"]
    audio = sum(1 for p in runnable if p.action == "transcribe")
    n = len(runnable)
    lines = [f'<div class="ps-step">Reading {n} file{"s" if n != 1 else ""} and finding the personal details…</div>']
    if audio:
        lines.append(
            f'<div class="ps-note">{audio} recording{"s" if audio != 1 else ""} to transcribe. '
            "Transcription takes about as long as the recording, longer without a GPU.</div>"
        )
    lines.append(
        '<div class="ps-note">No text leaves this computer in this step. Keep this page and '
        "the Phenoscribe window open. The review of each file appears here when the step ends.</div>"
    )
    return "".join(lines)


def begin_run(files: list[str], language: str):
    """Before the run: lock Start and show the working panel (the progress bar goes there)."""
    plans = file_plan(files, language)
    return (
        gr.update(interactive=False, value="Working…"),
        gr.update(value=working_html(plans), visible=True),
        gr.update(visible=False),
        gr.update(visible=False),
    )


def keep_types_for(remove: list[str]) -> list[str]:
    """The optional types the user left unticked under "Also remove"."""
    return [t for t in OPTIONAL_TYPES if t not in (remove or [])]


def prepare_run(files, language, remove, retranscribe, progress=gr.Progress()):
    """Stage 1: transcribe and find the personal details. Then show the review."""
    config = load_config(CONFIG_PATH)
    config.transcription.language = language
    results = prepare_batch(
        files, config, OUTPUT_DIR,
        on_progress=lambda fraction, message: progress(fraction, desc=message),
        retranscribe=frozenset(retranscribe or []),
        keep_types=keep_types_for(remove),
    )
    approve_clear_files([r.review for r in results if r.review], OUTPUT_DIR)
    queue = [r.patient_id for r in results if r.review]
    failed = [[r.filename, plain_error(r.error)] for r in results if r.error]
    first = _next_to_review(queue) or next(iter(queue), None)
    return (gr.update(visible=False), gr.update(visible=True), queue, failed,
            *review_view(first, queue, failed))


def resume_reviews():
    """On page load: open the reviews that a closed page left (Continue later)."""
    queue = [r.patient_id for r in open_reviews(OUTPUT_DIR)]
    first = _next_to_review(queue) or next(iter(queue), None)
    return (gr.update(visible=bool(queue)), queue, [], *review_view(first, queue, []))


def _load(patient_id):
    try:
        return load_review(OUTPUT_DIR, patient_id) if patient_id else None
    except ReviewError as e:
        logger.warning("%s", e)
        return None


def _next_to_review(queue, after=None):
    """The next file that needs a review, after ``after`` (wraps around)."""
    if not queue:
        return None
    start = queue.index(after) + 1 if after in queue else 0
    for patient_id in queue[start:] + queue[:start]:
        review = _load(patient_id)
        if review and review.status == NEEDS_REVIEW:
            return patient_id
    return None


def queue_html(queue, failed, current) -> str:
    """The files to check first, each with its reasons, then the approved files."""
    doubtful, clear = [], []
    for patient_id in queue:
        review = _load(patient_id)
        if review is None:
            continue
        name = html.escape(review.source)
        here = " here" if patient_id == current else ""
        if review.status == NEEDS_REVIEW:
            why = " · ".join(warnings(review)) or summary_line(review)
            doubtful.append(f'<div class="ps-row skip{here}"><span class="ps-file">{name}</span>'
                            f'<span class="ps-tag skip">Check</span>'
                            f'<span class="ps-what">{html.escape(why)}</span></div>')
        else:
            how = "No warning" if review.approved_how == AUTO else "Approved"
            clear.append(f'<div class="ps-row done{here}"><span class="ps-file"><span class="ps-ok">✓</span> {name}</span>'
                         f'<span class="ps-tag">{how}</span>'
                         f'<span class="ps-what">{html.escape(summary_line(review))}</span></div>')
    bad = [f'<div class="ps-row bad"><span class="ps-file">✗ {html.escape(name)}</span>'
           f'<span class="ps-what">{html.escape(problem)}</span></div>' for name, problem in failed]
    rows = doubtful + bad + clear
    if not rows:
        return ""
    n = len(doubtful)
    head = (f'<div class="ps-count">{n} file{"s" if n != 1 else ""} to check · '
            f'{len(clear)} approved</div>' if n else
            f'<div class="ps-count"><span class="ps-ok">✓</span> All {len(clear)} files approved</div>')
    return f'{head}<div class="ps-list ps-queue">{"".join(rows)}</div>'


def review_rows(review) -> list[list]:
    return [[e.removed, e.value, type_name(e.category), e.count] for e in entries(review)]


def missed_rows(review) -> list[list]:
    return [[False, word, sample] for word, sample in possibly_missed(review)]


def leak_html(review) -> str:
    found = leaks(review)
    if found:
        return ('<span class="ps-leak-bad">✗ Still in the clean text: '
                f'{html.escape(", ".join(found))}. Approve stays off.</span>')
    return ('<span class="ps-leak-ok">✓ No removed value is left in the clean text. '
            "The check covers only the values in the table: read the text for names it missed.</span>")


def review_view(patient_id, queue, failed):
    """Everything in the review panel for one file."""
    review = _load(patient_id)
    choices = []
    for p in queue:
        r = _load(p)
        if r:
            choices.append((f"{r.source} · {'approved' if r.status != NEEDS_REVIEW else 'to check'}", p))
    approved = [p for p in queue if (r := _load(p)) and r.status != NEEDS_REVIEW]
    n = len(approved)
    plural = "s" if n != 1 else ""
    if review is None:
        return (queue_html(queue, failed, None), gr.update(choices=choices, value=None), "",
                [], gr.update(value=[]), [], [], gr.update(interactive=False, value="Approve"),
                gr.update(value=f"Find HPO codes in {n} approved file{plural}", interactive=bool(n)),
                gr.update(value=f"Save {n} clean text{plural}", interactive=bool(n)),
                _rest_button(queue))
    is_approved = review.status != NEEDS_REVIEW
    reasons = warnings(review) if not is_approved else []
    line = (f'<div class="ps-line">{html.escape(review.source)} · {html.escape(summary_line(review))}</div>'
            + "".join(f'<div class="ps-leak-bad">⚠ {html.escape(r)}</div>' for r in reasons)
            + leak_html(review))
    remove = [t for t in OPTIONAL_TYPES if t not in review.keep_types]
    return (
        queue_html(queue, failed, patient_id),
        gr.update(choices=choices, value=patient_id),
        line,
        highlighted(review),
        gr.update(value=remove),
        review_rows(review),
        missed_rows(review),
        gr.update(interactive=not is_approved and not leaks(review),
                  value="✓ Approved" if is_approved else f"Approve {review.source}"),
        gr.update(value=f"Find HPO codes in {n} approved file{plural}", interactive=bool(n)),
        gr.update(value=f"Save {n} clean text{plural}", interactive=bool(n)),
        _rest_button(queue),
    )


def _rest_button(queue):
    waiting = sum(1 for p in queue if (r := _load(p)) and r.status == NEEDS_REVIEW)
    return gr.update(value=f"Approve the {waiting} waiting files with no check", visible=waiting > 1)


def _edit(patient_id, queue, failed, change):
    review = _load(patient_id)
    if review is not None:
        change(review)
        save_review(review, OUTPUT_DIR)
    return review_view(patient_id, queue, failed)


def on_removed_edit(table, patient_id, queue, failed):
    """A tick changed in the table: put the value back, or remove it again."""
    def change(review):
        now = {e.value: e.removed for e in entries(review)}
        for row in table or []:
            value, removed = row[1], bool(row[0])
            if value in now and now[value] != removed:
                set_removed(review, value, removed)
    return _edit(patient_id, queue, failed, change)


def on_missed_edit(table, patient_id, queue, failed):
    """A ticked word after a title: remove it as a name, at every position."""
    def change(review):
        for row in table or []:
            if row[0]:
                add_value(review, row[1], "PERSON")
    return _edit(patient_id, queue, failed, change)


def on_add(value, category, patient_id, queue, failed):
    view = _edit(patient_id, queue, failed, lambda review: add_value(review, value or "", category))
    return ("", *view)


def on_types(remove, patient_id, queue, failed):
    return _edit(patient_id, queue, failed, lambda review: set_keep_types(review, keep_types_for(remove)))


def on_approve(patient_id, queue, failed):
    """Approve this file, write its clean text, and open the next file to review."""
    review = _load(patient_id)
    if review is not None and review.status == NEEDS_REVIEW:
        try:
            approve(review)
        except ReviewError as e:
            logger.warning("%s", e)
        else:
            save_review(review, OUTPUT_DIR)
            write_clean_files(review, OUTPUT_DIR)
    following = _next_to_review(queue, after=patient_id) or patient_id
    return review_view(following, queue, failed)


def _approved(queue) -> list[str]:
    return [p for p in queue if (r := _load(p)) and r.status == APPROVED]


def begin_coding(queue):
    """Lock the button and show the progress panel under it, with the files to code."""
    names = [r.source for p in _approved(queue) if (r := _load(p))]
    n = len(names)
    shown = ", ".join(html.escape(name) for name in names[:6]) + (f" and {n - 6} more" if n > 6 else "")
    panel = (f'<div class="ps-step">Finding the HPO codes in {n} file{"s" if n != 1 else ""}…</div>'
             f'<div class="ps-note">{shown}</div>'
             '<div class="ps-note">About 1 minute for each file: one AI call finds the symptoms, '
             "then one call for each symptom picks its code. Only the approved clean text goes "
             "to the AI provider.</div>")
    return gr.update(interactive=False, value="Working…"), gr.update(value=panel, visible=True)


def code_run(queue, provider, api_key, progress=gr.Progress()):
    """Stage 2 of the HPO tool: code the approved files into a new Excel."""
    config = load_config(CONFIG_PATH)
    config.llm.provider = provider
    config.llm.model = model_for(provider, config)
    with use_api_key(provider, api_key.strip()):
        batch = code_batch(
            _approved(queue), config, OUTPUT_DIR,
            on_progress=lambda fraction, message: progress(fraction, desc=message),
        )
    rows = result_rows(batch)
    if batch.excel_path:
        saved = (f"Saved in {OUTPUT_DIR} as {batch.excel_path.name}, with a \"Personal details\" sheet. "
                 f"The clean texts are in the clean folder. Error details go to {LOG_PATH.name}.")
    else:
        saved = f"No Excel file: every file failed. Details are in {LOG_PATH}."
    left = [p for p in queue if p not in _approved(queue) and (r := _load(p)) and r.status == NEEDS_REVIEW]
    return (
        gr.update(visible=False),
        gr.update(visible=bool(left)),
        left,
        gr.update(visible=True),
        summary_html(batch, rows, datetime.now()),
        status_html(batch),
        rows,
        rows,
        "All",
        gr.update(value=str(batch.excel_path) if batch.excel_path else None, visible=bool(batch.excel_path)),
        saved,
    )


def clean_run(queue):
    """The removal-only tool: one ZIP of the approved clean texts and the counts report."""
    archive = finish_clean(_approved(queue), OUTPUT_DIR)
    if archive is None:
        return gr.update(visible=False), "No approved file yet."
    left = sum(1 for p in queue if (r := _load(p)) and r.status == NEEDS_REVIEW)
    note = f"Saved in {OUTPUT_DIR}: {archive.name} and the clean folder."
    if left:
        note += f" {left} file{'s' if left != 1 else ''} still need{'s' if left == 1 else ''} a review."
    return gr.update(value=str(archive), visible=True), note


def on_approve_rest(confirmed, patient_id, queue, failed):
    """"Approve all the rest", after the browser confirm."""
    if confirmed:
        approve_the_rest(queue, OUTPUT_DIR)
    return review_view(patient_id, queue, failed)


# --- page -------------------------------------------------------------------------


def _step(number: int, title: str) -> str:
    return f'<div class="ps-step"><span class="ps-num">{number}</span>{html.escape(title)}</div>'


def build_app() -> gr.Blocks:
    with gr.Blocks(title="Phenoscribe", theme=THEME, css=CSS, head=HEAD) as app:
        gr.HTML(
            '<div class="ps-header"><div><span class="ps-title">Phenoscribe</span>'
            '<span class="ps-sub">Interviews to HPO codes</span></div>'
            '<span class="ps-pill"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" '
            'stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">'
            '<rect x="4" y="11" width="16" height="10" rx="2"></rect><path d="M8 11V7a4 4 0 0 1 8 0v4">'
            "</path></svg>Recordings stay on this computer</span></div>"
        )

        files = gr.State([])
        fresh = gr.State([])
        rows = gr.State([])
        queue = gr.State([])
        failed = gr.State([])
        confirm_box = gr.Checkbox(visible=False)
        saved_key = gr.BrowserState("", **KEY_STORAGE)
        saved_remove = gr.BrowserState([], **REMOVE_STORAGE)

        # 1 · key
        with gr.Column(elem_classes="ps-card"):
            with gr.Row(equal_height=True):
                gr.HTML(_step(1, "API key"))
                provider = gr.Radio(
                    choices=PROVIDER_CHOICES, value="openai", show_label=False,
                    container=False, elem_classes="ps-seg", scale=0, min_width=220,
                )
            with gr.Column(visible=False) as key_entry:
                api_key = gr.Textbox(
                    show_label=False, type="password",
                    placeholder="Paste your OpenAI or Anthropic API key",
                )
                remember = gr.Checkbox(value=True, label="Remember this key in this browser")
                key_problem = gr.Markdown()
            with gr.Row(visible=False, equal_height=True) as key_line:
                key_text = gr.HTML()
                change_key = gr.Button("Change key", size="sm", scale=0, min_width=120)

        # 2 · files
        with gr.Column(elem_classes="ps-card"):
            gr.HTML(_step(2, "Recordings and transcripts"))
            drop = gr.File(
                file_count="multiple", show_label=False, elem_classes="ps-drop", height=150,
                label="Drop audio files or transcripts here",
            )
            with gr.Row():
                folder = gr.UploadButton("Choose a folder", file_count="directory", size="sm", scale=0, min_width=160)
                clear = gr.Button("Clear the list", size="sm", scale=0, min_width=140, visible=False)
            hint = gr.HTML(
                '<div class="ps-note">Audio: .mp3 .wav .m4a .ogg .flac · Transcripts: .txt .docx · '
                "A saved transcript of the same audio is reused. A clean Word copy keeps the text "
                "and loses the layout.</div>"
            )
            plan = gr.HTML()
            retranscribe = gr.CheckboxGroup(label="Transcribe again", visible=False)
            language = gr.Dropdown(
                choices=LANGUAGE_CHOICES, value="fr", label="Language of the recordings",
                visible=False,
            )
            mode = gr.Radio(choices=MODE_CHOICES, value=MODE_CODE, label="What to do")
            remove = gr.CheckboxGroup(
                choices=REMOVE_CHOICES, value=list(OPTIONAL_TYPES), label="Also remove",
                info=f"Always removed: {ALWAYS_REMOVED}.",
            )
            gr.HTML(
                '<div class="ps-note">You check only the doubtful files: a word after a title, '
                "a removal the app is unsure about, or no removal at all. The app approves the "
                "other files. A name that it missed with no warning goes to the AI provider.</div>"
            )

        # 3 · start
        start = gr.Button("Start", variant="primary", interactive=False, elem_classes="ps-start")
        gr.HTML(
            f'<div class="ps-note">Results go to {html.escape(str(OUTPUT_DIR))}. '
            "The personal details are removed on this computer. You check the doubtful files "
            "before any text goes to the AI provider. The removal can miss details.</div>"
        )

        # while the files are read: Gradio draws the progress bar on this panel
        working = gr.HTML(visible=False, elem_classes=["ps-card", "ps-working"], min_height=140)

        # 4 · review
        with gr.Column(visible=False, elem_classes="ps-card") as review_panel:
            gr.HTML(_step(3, "Check the doubtful files"))
            queue_list = gr.HTML()
            pick = gr.Dropdown(label="File", choices=[])
            line = gr.HTML()
            text = gr.HighlightedText(
                show_label=False, combine_adjacent=False, show_legend=True,
                color_map=TYPE_COLORS, elem_classes="ps-text",
            )
            file_remove = gr.CheckboxGroup(choices=REMOVE_CHOICES, label="Also remove in this file")
            gr.HTML('<div class="ps-note">Untick a value that is not a personal detail, '
                    "for example a drug name. It goes back at every place in the text.</div>")
            found = gr.Dataframe(
                headers=REVIEW_HEADERS, datatype=["bool", "str", "str", "number"], type="array",
                interactive=True, static_columns=[1, 2, 3], wrap=True, elem_classes="ps-table",
                column_widths=["12%", "48%", "24%", "16%"],
            )
            missed = gr.Dataframe(
                headers=MISSED_HEADERS, datatype=["bool", "str", "str"], type="array",
                interactive=True, static_columns=[1, 2], wrap=True, elem_classes="ps-table",
                label="Possibly missed", column_widths=["12%", "24%", "64%"],
            )
            with gr.Row(equal_height=True):
                add_text = gr.Textbox(label="Remove a word the app missed", scale=3)
                add_type = gr.Dropdown(choices=ADD_TYPES, value="PERSON", label="Type", scale=1)
                add = gr.Button("Remove it", size="sm", scale=0, min_width=120)
            approve_btn = gr.Button("Approve", variant="primary", interactive=False)
            rest_btn = gr.Button("Approve the rest", size="sm", visible=False)
            with gr.Row():
                code_btn = gr.Button("Find HPO codes", variant="primary", interactive=False)
                clean_btn = gr.Button("Save the clean texts", interactive=False, visible=False)
            # while the codes are found: the progress bar shows here, under the button
            code_working = gr.HTML(visible=False, elem_classes=["ps-card", "ps-working"], min_height=120)
            clean_file = gr.DownloadButton("Download the clean texts (.zip)", visible=False)
            clean_note = gr.Markdown(elem_classes="ps-note")
            gr.HTML('<div class="ps-note">Each change is saved. You can close the page and '
                    "continue later: the files that need a review open again.</div>")

        # results
        with gr.Column(visible=False) as results:
            summary = gr.HTML()
            excel = gr.DownloadButton("Download Excel", variant="primary", visible=False)
            status = gr.HTML()
            with gr.Column(elem_classes="ps-card"):
                with gr.Row(equal_height=True):
                    gr.HTML('<div class="ps-step">Coded findings</div>')
                    show = gr.Radio(
                        ["All", "To review"], value="All", show_label=False, container=False,
                        elem_classes="ps-seg", scale=0, min_width=200,
                    )
                table = gr.Dataframe(
                    headers=RESULT_HEADERS, interactive=False, wrap=True, elem_classes="ps-table",
                    column_widths=["11%", "29%", "24%", "13%", "12%", "11%"],
                )
            saved = gr.Markdown(elem_classes="ps-note")

        # events
        key_outputs = [api_key, provider, key_text, key_entry, key_line]
        app.load(fn=load_saved_key, inputs=saved_key, outputs=key_outputs)
        app.load(fn=lambda saved: saved if saved else list(OPTIONAL_TYPES), inputs=saved_remove, outputs=remove)
        api_key.blur(
            fn=on_key_entered,
            inputs=[api_key, remember],
            outputs=[provider, key_text, key_problem, saved_key, key_entry, key_line],
        )
        change_key.click(
            fn=lambda: ("", "", *_key_view(False)),
            outputs=[api_key, saved_key, key_entry, key_line],
        )
        remove.change(fn=lambda chosen: chosen, inputs=remove, outputs=saved_remove)

        view_inputs = [files, fresh, language, api_key, mode]
        view_outputs = [plan, language, retranscribe, start, clear, drop, hint, fresh]
        drop.upload(fn=add_files, inputs=[drop, files], outputs=[drop, files, fresh]).then(
            fn=selection_view, inputs=view_inputs, outputs=view_outputs
        )
        folder.upload(
            fn=lambda new, cur: add_files(new, cur)[1:], inputs=[folder, files], outputs=[files, fresh]
        ).then(fn=selection_view, inputs=view_inputs, outputs=view_outputs)
        clear.click(fn=lambda: [], outputs=files).then(
            fn=selection_view, inputs=view_inputs, outputs=view_outputs
        )
        for trigger in (language.change, key_text.change, mode.change):
            trigger(fn=selection_view, inputs=view_inputs, outputs=view_outputs)

        review_outputs = [queue_list, pick, line, text, file_remove, found, missed,
                          approve_btn, code_btn, clean_btn, rest_btn]
        review_inputs = [pick, queue, failed]
        app.load(fn=resume_reviews, outputs=[review_panel, queue, failed, *review_outputs])
        start.click(
            fn=begin_run, inputs=[files, language], outputs=[start, working, review_panel, results],
        ).then(
            fn=prepare_run,
            inputs=[files, language, remove, retranscribe],
            outputs=[working, review_panel, queue, failed, *review_outputs],
            show_progress_on=[working],
        ).then(fn=selection_view, inputs=view_inputs, outputs=view_outputs)

        pick.input(fn=review_view, inputs=review_inputs, outputs=review_outputs)
        found.input(fn=on_removed_edit, inputs=[found, *review_inputs], outputs=review_outputs)
        missed.input(fn=on_missed_edit, inputs=[missed, *review_inputs], outputs=review_outputs)
        add.click(fn=on_add, inputs=[add_text, add_type, *review_inputs], outputs=[add_text, *review_outputs])
        add_text.submit(fn=on_add, inputs=[add_text, add_type, *review_inputs], outputs=[add_text, *review_outputs])
        file_remove.input(fn=on_types, inputs=[file_remove, *review_inputs], outputs=review_outputs)
        approve_btn.click(fn=on_approve, inputs=review_inputs, outputs=review_outputs)
        mode.change(
            fn=lambda m: (gr.update(visible=m == MODE_CODE), gr.update(visible=m == MODE_CLEAN)),
            inputs=mode, outputs=[code_btn, clean_btn],
        )

        code_btn.click(
            fn=begin_coding, inputs=queue, outputs=[code_btn, code_working],
        ).then(
            fn=code_run,
            inputs=[queue, provider, api_key],
            outputs=[code_working, review_panel, queue, results, summary, status, rows, table, show, excel, saved],
            show_progress_on=[code_working],
        ).then(fn=review_view, inputs=review_inputs, outputs=review_outputs)
        clean_btn.click(fn=clean_run, inputs=queue, outputs=[clean_file, clean_note]).then(
            fn=review_view, inputs=review_inputs, outputs=review_outputs
        )
        show.change(fn=filter_rows, inputs=[rows, show], outputs=table)
        rest_btn.click(
            fn=on_approve_rest, inputs=[confirm_box, *review_inputs], outputs=review_outputs,
            js=f"(c, p, q, f) => [confirm({APPROVE_REST_QUESTION!r}), p, q, f]",
        )

    return app


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log_file = logging.FileHandler(LOG_PATH, encoding="utf-8")
    log_file.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(log_file)

    build_app().launch(
        # Only this computer can open the page. Docker sets 0.0.0.0 so the
        # host can reach the container.
        server_name=os.environ.get("GRADIO_SERVER_NAME", "127.0.0.1"),
        server_port=7860,
        inbrowser=True,
        quiet=False,
        allowed_paths=[str(OUTPUT_DIR)],
        show_api=False,
    )


if __name__ == "__main__":
    main()
