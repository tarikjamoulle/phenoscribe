"""The pieces of the page as HTML strings and table rows. Pure functions: no Gradio, no files."""

import html
from dataclasses import dataclass
from datetime import datetime

import anthropic
import openai
import requests

from phenoscribe.batch import BatchResult, FilePlan
from phenoscribe.findings import Finding, finding_status
from phenoscribe.output import dedup_by_code
from phenoscribe.review import (
    AUTO,
    NEEDS_REVIEW,
    Review,
    Sign,
    entries,
    first_names,
    fold,
    possibly_left,
    possibly_missed,
    summary_line,
    towns,
    type_name,
)

LOG_NAME = "phenoscribe.log"

PROVIDER_CHOICES = [("OpenAI", "openai"), ("Anthropic", "anthropic")]
PROVIDER_NAMES = {value: label for label, value in PROVIDER_CHOICES}

RESULT_HEADERS = ["Patient", "Symptom (patient words)", "HPO term", "Code", "Status", "When"]

ACTION_TEXT = {
    "transcribe": ("Audio", "Transcribe, then remove personal details"),
    "reuse": ("Audio", "Saved transcript found"),
    "transcript": ("Transcript", "Remove personal details"),
}


def plural(n: int, word: str) -> str:
    """'1 file', '3 files'."""
    return f"{n} {word}{'s' if n != 1 else ''}"


def step_html(number: int, title: str) -> str:
    return f'<div class="ps-step"><span class="ps-num">{number}</span>{html.escape(title)}</div>'


# --- the key ----------------------------------------------------------------------------


def key_summary(provider: str, how: str) -> str:
    return f'<span class="ps-ok">✓</span> {PROVIDER_NAMES[provider]} key {how}'


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
    return f"Failed ({type(error).__name__}). Details are in {LOG_NAME}"


# --- the files ----------------------------------------------------------------------------


def count_line(plans: list[FilePlan]) -> str:
    """'✓ 3 files added · 1 skipped' above the list."""
    ready = sum(1 for p in plans if p.action != "skip")
    skipped = len(plans) - ready
    text = f"{plural(ready, 'file')} added"
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


def working_html(plans: list[FilePlan]) -> str:
    """What the page shows while the files are read, above Gradio's progress bar."""
    runnable = [p for p in plans if p.action != "skip"]
    audio = sum(1 for p in runnable if p.action == "transcribe")
    lines = [f'<div class="ps-step">Reading {plural(len(runnable), "file")} and finding the personal details…</div>']
    if audio:
        lines.append(
            f'<div class="ps-note">{plural(audio, "recording")} to transcribe. '
            "Transcription takes about as long as the recording, longer without a GPU.</div>"
        )
    lines.append(
        '<div class="ps-note">No text leaves this computer in this step. Keep this page and '
        "the Phenoscribe window open. The review of each file appears here when the step ends.</div>"
    )
    return "".join(lines)


# --- the review ---------------------------------------------------------------------------


@dataclass
class QueueItem:
    """One file of the review queue, loaded once for a render, with its warning signs."""

    patient_id: str
    review: Review
    signs: list[Sign]
    blocking: bool

    @property
    def waits(self) -> bool:
        return self.review.status == NEEDS_REVIEW


def queue_html(items: list[QueueItem], failed: list, current: str | None) -> str:
    """The files to check first, each with its reasons, then the approved files."""
    doubtful, clear = [], []
    for item in items:
        review = item.review
        name = html.escape(review.source)
        here = " here" if item.patient_id == current else ""
        if item.waits:
            why = " · ".join(s.text for s in item.signs) or summary_line(review)
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
    head = (f'<div class="ps-count">{plural(len(doubtful), "file")} to check · '
            f'{len(clear)} approved</div>' if doubtful else
            f'<div class="ps-count"><span class="ps-ok">✓</span> All {plural(len(clear), "file")} approved</div>')
    return f'{head}<div class="ps-list ps-queue">{"".join(rows)}</div>'


def file_line(item: QueueItem, leaked: list[str]) -> str:
    """The line above the text of one file: its counts, its warning signs and the leak check."""
    review = item.review
    reasons = item.signs if item.waits else []
    return (f'<div class="ps-line">{html.escape(review.source)} · {html.escape(summary_line(review))}</div>'
            + "".join(f'<div class="ps-leak-bad">⚠ {html.escape(s.text)}</div>' for s in reasons)
            + leak_html(leaked))


def leak_html(found: list[str]) -> str:
    """The result of the leak check (review.leaks): the removed values still in the clean text."""
    if found:
        return ('<span class="ps-leak-bad">✗ Still in the clean text: '
                f'{html.escape(", ".join(found))}. Approve stays off.</span>')
    return ('<span class="ps-leak-ok">✓ No removed value is left in the clean text. '
            "The check covers only the values in the table: read the text for names it missed.</span>")


def review_rows(review: Review) -> list[list]:
    return [[e.removed, e.value, type_name(e.category), e.count] for e in entries(review)]


def missed_rows(review: Review) -> list[list]:
    """Words after a title, then the words of the leak check (a first name or a town)."""
    rows = [[False, word, sample] for word, sample in possibly_missed(review)]
    shown = {fold(row[1]) for row in rows}
    return rows + [[False, word, sample] for word, sample in possibly_left(review) if fold(word) not in shown]


def missed_category(word: str) -> str:
    """A town of the list that is no first name is a place; any other word a name."""
    key = fold(word)
    return "LOCATION" if key in towns() and key not in first_names() else "PERSON"


def coding_html(names: list[str]) -> str:
    """The panel under "Find HPO codes" while the codes are found."""
    n = len(names)
    shown = ", ".join(html.escape(name) for name in names[:6]) + (f" and {n - 6} more" if n > 6 else "")
    return (f'<div class="ps-step">Finding the HPO codes in {plural(n, "file")}…</div>'
            f'<div class="ps-note">{shown}</div>'
            '<div class="ps-note">About 1 minute for each file: one AI call finds the symptoms, '
            "then one call for each symptom picks its code. Only the approved clean text goes "
            "to the AI provider.</div>")


# --- the results --------------------------------------------------------------------------


def coded_findings(batch: BatchResult) -> list[tuple[str, Finding]]:
    """(patient ID, finding) for each row of the Excel, deduplicated the same way."""
    return [(r.patient_id, f) for r in batch.files for f in dedup_by_code(r.matches, "detailed")]


def table_rows(findings: list[tuple[str, Finding]]) -> list[list]:
    """The rows of the result table, one for each finding."""
    rows = []
    for patient_id, f in findings:
        status = finding_status(f)
        if f.get("needs_review"):
            status += " · Review"
        rows.append([
            patient_id,
            f.get("patient_verbatim") or f.get("clinical_term") or "",
            f["hpo_term"] if f.get("hpo_id") else "No good match",
            f.get("hpo_id") or "—",
            status,
            when_text(f),
        ])
    return rows


def result_rows(batch: BatchResult) -> list[list]:
    """The coded rows, one per finding, deduplicated the same way as the Excel."""
    return table_rows(coded_findings(batch))


def filter_rows(findings: list[tuple[str, Finding]], which: str) -> list[list]:
    """The table rows of all findings, or of the findings to review."""
    if which == "To review":
        findings = [(p, f) for p, f in findings if f.get("needs_review")]
    return table_rows(findings)


def when_text(finding: Finding) -> str:
    """'12 %' (first mention in the interview), plus '· 3×' for repeats."""
    first = finding.get("first_position")
    if first is None:
        return ""
    n = finding.get("n_mentions") or 1
    return f"{first:.0f} %" + (f" · {n}×" if n > 1 else "")


def summary_html(batch: BatchResult, findings: list[tuple[str, Finding]], when: datetime) -> str:
    codes = sum(1 for _, f in findings if f.get("hpo_id"))
    review = sum(1 for _, f in findings if f.get("needs_review"))
    done = sum(1 for r in batch.files if not r.error)
    total = len(batch.files)
    return (
        f'<div class="ps-h1">Results</div>'
        f'<div class="ps-note">Run of {when:%d %B %Y, %H:%M} · {plural(total, "file")}</div>'
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
        page += (f'<details class="ps-done"><summary><span class="ps-ok">✓</span> '
                 f'{plural(len(done), "interview")} coded</summary>'
                 f'<div class="ps-list">{"".join(done)}</div></details>')
    return page
