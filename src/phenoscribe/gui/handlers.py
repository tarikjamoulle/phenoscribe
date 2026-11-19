"""The event handlers of the page: the key, the files, the review, and the two runs.

The handlers read and write the results folder (OUTPUT_DIR) and return Gradio
updates. The HTML and the table rows come from views.py.
"""

import logging
import os
from datetime import datetime
from pathlib import Path

import anthropic
import gradio as gr
import openai

from phenoscribe.batch import (
    FilePlan,
    approve_clear_files,
    approve_the_rest,
    code_batch,
    finish_clean,
    plan_batch,
    prepare_batch,
)
from phenoscribe.config import DEFAULT_MODELS, load_config
from phenoscribe.gui.views import (
    LOG_NAME,
    QueueItem,
    coded_findings,
    coding_html,
    file_line,
    key_summary,
    missed_category,
    missed_rows,
    plain_error,
    plan_html,
    plural,
    queue_html,
    review_rows,
    status_html,
    summary_html,
    table_rows,
    working_html,
)
from phenoscribe.llm import API_KEY_ENV, use_api_key
from phenoscribe.pipeline import write_clean_files
from phenoscribe.review import (
    APPROVED,
    NEEDS_REVIEW,
    OPTIONAL_TYPES,
    ReviewError,
    add_value,
    approve,
    blocking_warnings,
    entries,
    highlighted,
    leaks,
    load_review,
    open_reviews,
    reopen_if_blocked,
    save_review,
    set_keep_types,
    set_removed,
    warning_signs,
)

logger = logging.getLogger(__name__)

# Every handler reads the folder through this module, so one patch moves it.
OUTPUT_DIR = Path(os.environ.get("PHENOSCRIBE_OUTPUT_DIR", "output"))
# The drop box: full height while the list is empty, a slim bar once it has files.
DROP_HEIGHT = 150
SLIM_DROP_HEIGHT = 56
CONFIG_PATH = os.environ.get("PHENOSCRIBE_CONFIG", "config.yaml")
LOG_PATH = OUTPUT_DIR / LOG_NAME

# The two tools.
MODE_CODE, MODE_CLEAN = "code", "clean"


# --- the key ----------------------------------------------------------------------------


def env_key_for(provider: str) -> str:
    return os.environ.get(API_KEY_ENV.get(provider, ""), "")


def provider_for_key(api_key: str) -> str:
    """Anthropic keys start with "sk-ant-"; everything else is taken as OpenAI."""
    return "anthropic" if api_key.strip().startswith("sk-ant-") else "openai"


def model_for(provider: str, config) -> str:
    """The model of config.yaml for its provider, else the default model of the provider."""
    if provider == config.llm.provider:
        return config.llm.model
    return DEFAULT_MODELS[provider]


def check_key(provider: str, api_key: str) -> str:
    """Make one small call (list the models). Returns "" when the key works."""
    try:
        if provider == "anthropic":
            anthropic.Anthropic(api_key=api_key).models.list(limit=1)
        else:
            openai.OpenAI(api_key=api_key).models.list()
    except Exception as e:  # every failure becomes one plain line
        logger.warning("Key check failed: %r", e)
        return plain_error(e)
    return ""


def key_view(have_key: bool):
    """(entry box, short line) visibility."""
    return gr.update(visible=not have_key), gr.update(visible=have_key)


def load_saved_key(saved: str):
    """On page load: a saved key (or an env key) shows the short key line."""
    if saved:
        provider = provider_for_key(saved)
        return saved, provider, key_summary(provider, "saved in this browser"), *key_view(True)
    for provider in API_KEY_ENV:
        if env_key_for(provider):
            return "", provider, key_summary(provider, "from the environment"), *key_view(True)
    return "", "openai", "", *key_view(False)


def on_key_entered(api_key: str, remember: bool):
    """When the key box loses focus: check the key, then fold the box away."""
    api_key = api_key.strip()
    if not api_key:
        return gr.update(), "", "", gr.update(), *key_view(False)
    provider = provider_for_key(api_key)
    problem = check_key(provider, api_key)
    if problem:
        return provider, "", problem, gr.update(), *key_view(False)
    how = "works · kept in this browser only" if remember else "works · for this session only"
    saved = api_key if remember else ""
    return provider, key_summary(provider, how), "", saved, *key_view(True)


# --- the files ----------------------------------------------------------------------------


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


def selection_view(files: list[str], fresh: list[str], language: str, api_key: str, mode: str = MODE_CODE):
    """Everything that follows the file selection: list, language, reuse, Start,
    and the drop box, which shrinks to a slim bar once files are in the list.

    Start needs a key only for the HPO codes: the removal runs on this computer."""
    plans = file_plan(files, language)
    runnable = [p for p in plans if p.action != "skip"]
    reusable = [p.name for p in plans if p.action == "reuse"]
    have_key = bool(api_key.strip()) or any(env_key_for(p) for p in API_KEY_ENV)
    n = len(runnable)
    can_start = bool(n) and (have_key or mode == MODE_CLEAN)
    if n and not can_start:
        label = "Paste an API key to start"  # the grey button says what is missing
    else:
        label = f"Start with {plural(n, 'file')}" if n else "Start"
    drop_classes = ["ps-drop", "ps-slim"] if files else ["ps-drop"]
    return (
        plan_html(plans, fresh),
        gr.update(visible=bool(runnable)),  # audio and text: the language picks the privacy models
        gr.update(choices=reusable, value=[], visible=bool(reusable)),
        gr.update(value=label, interactive=can_start),
        gr.update(visible=bool(files)),
        gr.update(height=SLIM_DROP_HEIGHT if files else DROP_HEIGHT, elem_classes=drop_classes),
        gr.update(visible=not files),
        [],
    )


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


def prepare_run(files, language, remove, retranscribe, progress=gr.Progress()):  # noqa: B008 - Gradio's progress API
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


# --- the review ---------------------------------------------------------------------------


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


def _queue_items(queue) -> list[QueueItem]:
    """Each readable review of the queue, loaded once, with its warning signs.

    An approved file with a blocking sign goes back to "to check" (reopen_if_blocked).
    """
    items = []
    for patient_id in queue:
        review = _load(patient_id)
        if review is None:
            continue
        signs = warning_signs(review)
        blocking = bool(blocking_warnings(review, signs))
        if reopen_if_blocked(review, signs):
            save_review(review, OUTPUT_DIR)
        items.append(QueueItem(patient_id, review, signs, blocking))
    return items


def _item(patient_id) -> QueueItem | None:
    review = _load(patient_id)
    if review is None:
        return None
    signs = warning_signs(review)
    return QueueItem(patient_id, review, signs, bool(blocking_warnings(review, signs)))


def review_view(patient_id, queue, failed):
    """Everything in the review panel, for one file or for no file."""
    items = _queue_items(queue)
    current = next((i for i in items if i.patient_id == patient_id), None) or _item(patient_id)
    approved = sum(1 for i in items if not i.waits)
    # A file with a blocking warning sign is never approved in bulk (approve_the_rest).
    unblocked = sum(1 for i in items if i.waits and not i.blocking)
    return (
        queue_html(items, failed, current.patient_id if current else None),
        gr.update(choices=[(f"{i.review.source} · {'to check' if i.waits else 'approved'}", i.patient_id)
                           for i in items],
                  value=current.patient_id if current else None),
        *_file_view(current),
        gr.update(value=f"Find HPO codes in {plural(approved, 'approved file')}", interactive=bool(approved)),
        gr.update(value=f"Save {plural(approved, 'clean text')}", interactive=bool(approved)),
        gr.update(value=f"Approve the {unblocked} waiting files with no check", visible=unblocked > 1),
    )


def _file_view(item: QueueItem | None):
    """(line, text, also remove, found rows, missed rows, Approve button) of one file."""
    if item is None:
        return "", [], gr.update(value=[]), [], [], gr.update(interactive=False, value="Approve")
    review = item.review
    found = leaks(review)
    return (
        file_line(item, found),
        highlighted(review),
        gr.update(value=[t for t in OPTIONAL_TYPES if t not in review.keep_types]),
        review_rows(review),
        missed_rows(review),
        gr.update(interactive=item.waits and not found,
                  value=f"Approve {review.source}" if item.waits else "✓ Approved"),
    )


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
    """A ticked possible name or place: remove it, at every position."""
    def change(review):
        for row in table or []:
            if row[0]:
                add_value(review, row[1], missed_category(row[1]))
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


def on_approve_rest(confirmed, patient_id, queue, failed):
    """"Approve all the rest", after the browser confirm."""
    if confirmed:
        approve_the_rest(queue, OUTPUT_DIR)
    return review_view(patient_id, queue, failed)


# --- the runs -----------------------------------------------------------------------------


def _approved(queue) -> list[str]:
    return [p for p in queue if (r := _load(p)) and r.status == APPROVED]


def begin_coding(queue):
    """Lock the button and show the progress panel under it, with the files to code."""
    names = [r.source for p in _approved(queue) if (r := _load(p))]
    return gr.update(interactive=False, value="Working…"), gr.update(value=coding_html(names), visible=True)


def code_run(queue, provider, api_key, progress=gr.Progress()):  # noqa: B008 - Gradio's progress API
    """Stage 2 of the HPO tool: code the approved files into a new Excel."""
    config = load_config(CONFIG_PATH)
    config.llm.provider = provider
    config.llm.model = model_for(provider, config)
    approved = _approved(queue)
    with use_api_key(provider, api_key.strip()):
        batch = code_batch(
            approved, config, OUTPUT_DIR,
            on_progress=lambda fraction, message: progress(fraction, desc=message),
        )
    findings = coded_findings(batch)
    if batch.excel_path:
        saved = (f"Saved in {OUTPUT_DIR} as {batch.excel_path.name}, with a \"Personal details\" sheet. "
                 f"The clean texts are in the clean folder. Error details go to {LOG_PATH.name}.")
    else:
        saved = f"No Excel file: every file failed. Details are in {LOG_PATH}."
    left = [p for p in queue if p not in approved and (r := _load(p)) and r.status == NEEDS_REVIEW]
    return (
        gr.update(visible=False),
        gr.update(visible=bool(left)),
        left,
        gr.update(visible=True),
        summary_html(batch, findings, datetime.now()),
        status_html(batch),
        findings,
        table_rows(findings),
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
        note += f" {plural(left, 'file')} still need{'s' if left == 1 else ''} a review."
    return gr.update(value=str(archive), visible=True), note
