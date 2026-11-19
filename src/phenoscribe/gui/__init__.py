"""The Gradio web app: two tools on one page.

Remove the personal details, or remove them and then find the HPO codes. The
steps: a key (only for the codes), the files, Start, a review of the personal
details of the doubtful files, then the codes or the clean texts. The app
decides the work for each file (batch.plan_batch). Only an approved review
goes to the AI provider. Results go to `PHENOSCRIBE_OUTPUT_DIR` (default
`output` in the current folder; the start script sets `Documents/Phenoscribe
results`, the Docker image `/app/output`).

- theme.py: the look (Gradio theme, CSS, colours).
- views.py: the HTML pieces and table rows, as pure functions.
- handlers.py: the event handlers; they read and write the results folder.
- app.py: the components, their wiring, and main().
"""

from phenoscribe.gui.app import APPROVE_REST_QUESTION, approve_rest_js, build_app, main
from phenoscribe.gui.handlers import (
    LOG_PATH,
    MODE_CLEAN,
    MODE_CODE,
    OUTPUT_DIR,
    add_files,
    begin_coding,
    begin_run,
    check_key,
    clean_run,
    code_run,
    file_plan,
    load_saved_key,
    model_for,
    on_add,
    on_approve,
    on_approve_rest,
    on_key_entered,
    on_missed_edit,
    on_removed_edit,
    prepare_run,
    provider_for_key,
    resume_reviews,
    review_view,
    selection_view,
)
from phenoscribe.gui.views import (
    coded_findings,
    count_line,
    filter_rows,
    plain_error,
    plan_html,
    result_rows,
    status_html,
    summary_html,
    working_html,
)

__all__ = [
    "APPROVE_REST_QUESTION", "LOG_PATH", "MODE_CLEAN", "MODE_CODE", "OUTPUT_DIR", "add_files",
    "approve_rest_js", "begin_coding", "begin_run", "build_app", "check_key", "clean_run", "code_run",
    "coded_findings", "count_line", "file_plan", "filter_rows", "load_saved_key", "main", "model_for",
    "on_add", "on_approve", "on_approve_rest", "on_key_entered", "on_missed_edit", "on_removed_edit",
    "plain_error", "plan_html", "prepare_run", "provider_for_key", "result_rows", "resume_reviews",
    "review_view", "selection_view", "status_html", "summary_html", "working_html",
]
