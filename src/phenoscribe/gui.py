"""Gradio web UI for Phenoscribe.

Three steps on one calm page: a key, the files, Start. The app decides the work
for each file (plan_batch): audio is transcribed, a .txt is coded as it is, and a
saved transcript of the same audio is reused. Results go to a new dated Excel in
`PHENOSCRIBE_OUTPUT_DIR` (default `/app/output`; the start scripts set
`Documents/Phenoscribe results`).
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
from phenoscribe.output import dedup_by_code
from phenoscribe.pipeline import BatchResult, FilePlan, plan_batch, run_batch

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

RESULT_HEADERS = ["Patient", "Symptom (patient words)", "HPO term", "Code", "Status"]

# The browser keeps the key in local storage. Gradio encrypts the stored value
# with this secret; it must stay the same across restarts, or the saved key is
# lost. It protects nothing, so the page calls it local to this browser only.
KEY_STORAGE = {"storage_key": "phenoscribe_api_key", "secret": "phenoscribe-local"}

ACTION_TEXT = {
    "transcribe": ("Audio", "Transcribe, then code"),
    "reuse": ("Audio", "Saved transcript found, code only"),
    "transcript": ("Transcript", "Code only"),
}


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


def selection_view(files: list[str], fresh: list[str], language: str, api_key: str):
    """Everything that follows the file selection: list, language, reuse, Start,
    and the drop box, which shrinks to a slim bar once files are in the list."""
    plans = file_plan(files, language)
    runnable = [p for p in plans if p.action != "skip"]
    has_audio = any(p.action in ("transcribe", "reuse") for p in plans)
    reusable = [p.name for p in plans if p.action == "reuse"]
    have_key = bool(api_key.strip()) or any(env_key_for(p) for p in PROVIDER_ENV_VAR)
    n = len(runnable)
    label = f"Start coding {n} file{'s' if n != 1 else ''}" if n else "Start coding"
    drop_classes = ["ps-drop", "ps-slim"] if files else ["ps-drop"]
    return (
        plan_html(plans, fresh),
        gr.update(visible=has_audio),
        gr.update(choices=reusable, value=[], visible=bool(reusable)),
        gr.update(value=label, interactive=bool(n) and have_key),
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
            status = "Absent" if m.get("negated") else "Present"
            if m.get("needs_review"):
                status += " · Review"
            rows.append(
                [
                    r.patient_id,
                    m.get("patient_verbatim") or m.get("clinical_term") or "",
                    m["hpo_term"] if m.get("hpo_id") else "No good match",
                    m.get("hpo_id") or "—",
                    status,
                ]
            )
    return rows


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
    rows = []
    for r in batch.files:
        who = f"{html.escape(r.filename)} → {html.escape(r.patient_id)}"
        if r.error:
            rows.append(
                f'<div class="ps-row bad"><span class="ps-file">✗ {who}</span>'
                f'<span>{html.escape(plain_error(r.error))}</span></div>'
            )
        else:
            coded = dedup_by_code(r.matches, "detailed")
            review = sum(1 for m in coded if m.get("needs_review"))
            rows.append(
                f'<div class="ps-row"><span class="ps-file"><span class="ps-ok">✓</span> {who}</span>'
                f"<span>{len(coded)} findings · {review} to review</span></div>"
            )
    return f'<div class="ps-list">{"".join(rows)}</div>'


def working_html(plans: list[FilePlan]) -> str:
    """What the page shows while a run works, above Gradio's progress bar."""
    runnable = [p for p in plans if p.action != "skip"]
    audio = sum(1 for p in runnable if p.action == "transcribe")
    n = len(runnable)
    lines = [f'<div class="ps-step">Coding {n} file{"s" if n != 1 else ""}…</div>']
    if audio:
        lines.append(
            f'<div class="ps-note">{audio} recording{"s" if audio != 1 else ""} to transcribe. '
            "Transcription takes about 10 minutes for each 20 minutes of audio, "
            "longer without a GPU.</div>"
        )
    lines.append(
        '<div class="ps-note">Keep this page and the Phenoscribe window open. '
        "The results appear here when the run ends.</div>"
    )
    return "".join(lines)


def begin_run(files: list[str], language: str):
    """Before the run: lock Start and show the working panel (the progress bar goes there)."""
    plans = file_plan(files, language)
    return (
        gr.update(interactive=False, value="Coding…"),
        gr.update(value=working_html(plans), visible=True),
        gr.update(visible=False),
    )


def run_pipeline(
    files: list[str],
    language: str,
    provider: str,
    api_key: str,
    retranscribe: list[str] | None,
    progress=gr.Progress(),
):
    config = load_config(CONFIG_PATH)
    config.llm.provider = provider
    config.llm.model = model_for(provider, config)
    config.transcription.language = language

    with use_api_key(provider, api_key.strip()):
        batch = run_batch(
            files,
            config,
            OUTPUT_DIR,
            on_progress=lambda fraction, message: progress(fraction, desc=message),
            retranscribe=frozenset(retranscribe or []),
        )

    rows = result_rows(batch)
    if batch.excel_path:
        saved = f"Saved in {OUTPUT_DIR} as {batch.excel_path.name}. Error details go to {LOG_PATH.name} in the same folder."
    else:
        saved = f"No Excel file: every file failed. Details are in {LOG_PATH}."
    return (
        gr.update(visible=False),
        gr.update(visible=True),
        summary_html(batch, rows, datetime.now()),
        status_html(batch),
        rows,
        rows,
        "All",
        gr.update(value=str(batch.excel_path) if batch.excel_path else None, visible=bool(batch.excel_path)),
        saved,
    )


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
        saved_key = gr.BrowserState("", **KEY_STORAGE)

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
                '<div class="ps-note">Audio: .mp3 .wav .m4a .ogg .flac · Transcripts: .txt · '
                "A saved transcript of the same audio is reused.</div>"
            )
            plan = gr.HTML()
            retranscribe = gr.CheckboxGroup(label="Transcribe again", visible=False)
            language = gr.Dropdown(
                choices=LANGUAGE_CHOICES, value="fr", label="Language of the recordings",
                visible=False,
            )

        # 3 · start
        start = gr.Button("Start coding", variant="primary", interactive=False, elem_classes="ps-start")
        gr.HTML(
            f'<div class="ps-note">Results go to {html.escape(str(OUTPUT_DIR))}. Each run makes a new Excel file. '
            "Names and other personal details are removed on this computer before the text "
            "goes to the AI provider. The removal can miss details.</div>"
        )

        # while a run works: Gradio draws the progress bar on this panel
        working = gr.HTML(visible=False, elem_classes=["ps-card", "ps-working"], min_height=140)

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
                    column_widths=["12%", "31%", "26%", "16%", "15%"],
                )
            saved = gr.Markdown(elem_classes="ps-note")

        # events
        key_outputs = [api_key, provider, key_text, key_entry, key_line]
        app.load(fn=load_saved_key, inputs=saved_key, outputs=key_outputs)
        api_key.blur(
            fn=on_key_entered,
            inputs=[api_key, remember],
            outputs=[provider, key_text, key_problem, saved_key, key_entry, key_line],
        )
        change_key.click(
            fn=lambda: ("", "", *_key_view(False)),
            outputs=[api_key, saved_key, key_entry, key_line],
        )

        view_inputs = [files, fresh, language, api_key]
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
        for trigger in (language.change, key_text.change):
            trigger(fn=selection_view, inputs=view_inputs, outputs=view_outputs)

        start.click(
            fn=begin_run, inputs=[files, language], outputs=[start, working, results],
        ).then(
            fn=run_pipeline,
            inputs=[files, language, provider, api_key, retranscribe],
            outputs=[working, results, summary, status, rows, table, show, excel, saved],
            show_progress_on=[working],
        ).then(fn=selection_view, inputs=view_inputs, outputs=view_outputs)
        show.change(fn=filter_rows, inputs=[rows, show], outputs=table)

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
