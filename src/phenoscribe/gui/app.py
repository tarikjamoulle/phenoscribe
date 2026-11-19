"""The page: the Gradio components and the wiring of their events. main() starts the app."""

import html
import json
import logging
import os
import sys

import gradio as gr

from phenoscribe.config import LANGUAGE_NAMES, ConfigError, load_config
from phenoscribe.gui import handlers
from phenoscribe.gui.handlers import key_view
from phenoscribe.gui.theme import CSS, THEME, TYPE_COLORS
from phenoscribe.gui.views import PROVIDER_CHOICES, RESULT_HEADERS, filter_rows, step_html
from phenoscribe.review import OPTIONAL_TYPES, PII_WARNING, TYPE_NAMES

LANGUAGE_CHOICES = [(name, code) for code, name in LANGUAGE_NAMES.items()]

MODE_CHOICES = [
    ("Remove personal details, then find HPO codes", handlers.MODE_CODE),
    ("Remove personal details only", handlers.MODE_CLEAN),
]
REMOVE_CHOICES = [(label, key) for key, label in OPTIONAL_TYPES.items()]
ALWAYS_REMOVED = "names, places, organizations, phones, emails and ID numbers"
ADD_TYPES = [(TYPE_NAMES[t][0].capitalize(), t) for t in TYPE_NAMES]

# The browser keeps the key in local storage. Gradio encrypts the stored value
# with this secret; it must stay the same across restarts, or the saved key is
# lost. It protects nothing, so the page calls it local to this browser only.
KEY_STORAGE = {"storage_key": "phenoscribe_api_key", "secret": "phenoscribe-local"}
REMOVE_STORAGE = {"storage_key": "phenoscribe_remove_types", "secret": "phenoscribe-local"}

APPROVE_REST_QUESTION = (
    "Approve every file that still waits, with no check? A name that the app missed in "
    "these files goes to the AI provider. A file with a possible name left in the text "
    "stays: open it to check it."
)

REVIEW_HEADERS = ["Remove", "Found in the text", "Type", "Times"]
MISSED_HEADERS = ["Remove", "Possible name or place", "Where"]


def build_app() -> gr.Blocks:
    with gr.Blocks(title="Phenoscribe", theme=THEME, css=CSS) as app:
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
        findings = gr.State([])  # (patient ID, finding) of each row of the result table
        queue = gr.State([])
        failed = gr.State([])
        confirm_box = gr.Checkbox(visible=False)
        saved_key = gr.BrowserState("", **KEY_STORAGE)
        saved_remove = gr.BrowserState([], **REMOVE_STORAGE)

        # 1 · key
        with gr.Column(elem_classes="ps-card"):
            with gr.Row(equal_height=True):
                gr.HTML(step_html(1, "API key"))
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
            gr.HTML(step_html(2, "Recordings and transcripts"))
            drop = gr.File(
                file_count="multiple", show_label=False, elem_classes="ps-drop", height=handlers.DROP_HEIGHT,
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
                choices=LANGUAGE_CHOICES, value="fr", label="Language of the interviews",
                visible=False,
            )
            mode = gr.Radio(choices=MODE_CHOICES, value=handlers.MODE_CODE, label="What to do")
            remove = gr.CheckboxGroup(
                choices=REMOVE_CHOICES, value=list(OPTIONAL_TYPES), label="Also remove",
                info=f"Always removed: {ALWAYS_REMOVED}.",
            )
            gr.HTML(
                '<div class="ps-note">You check only the doubtful files: a word after a title, '
                "a first name or a town left in the text, or a removal the app is unsure about "
                "when only one model read the file. The app approves the other files. A name that it missed with no "
                "warning goes to the AI provider.</div>"
            )

        # Start
        start = gr.Button("Start", variant="primary", interactive=False, elem_classes="ps-start")
        gr.HTML(
            f'<div class="ps-note">Results go to {html.escape(str(handlers.OUTPUT_DIR))}. '
            "The personal details are removed on this computer. You check the doubtful files "
            "before any text goes to the AI provider. The removal can miss details.</div>"
        )

        # while the files are read: Gradio draws the progress bar on this panel
        working = gr.HTML(visible=False, elem_classes=["ps-card", "ps-working"], min_height=140)

        # 3 · review
        with gr.Column(visible=False, elem_classes="ps-card") as review_panel:
            gr.HTML(step_html(3, "Check the doubtful files"))
            gr.HTML(f'<div class="ps-note">{html.escape(PII_WARNING)}</div>')
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
                headers=REVIEW_HEADERS, datatype=["bool", "str", "str", "number"], type="array", row_count=1,
                interactive=True, static_columns=[1, 2, 3], wrap=True, elem_classes="ps-table",
                column_widths=["12%", "48%", "24%", "16%"],
            )
            missed = gr.Dataframe(
                headers=MISSED_HEADERS, datatype=["bool", "str", "str"], type="array", row_count=1,
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
                    headers=RESULT_HEADERS, interactive=False, wrap=True, elem_classes="ps-table", row_count=1,
                    column_widths=["11%", "28%", "23%", "16%", "12%", "10%"],
                )
            saved = gr.Markdown(elem_classes="ps-note")

        # events
        key_outputs = [api_key, provider, key_text, key_entry, key_line]
        app.load(fn=handlers.load_saved_key, inputs=saved_key, outputs=key_outputs)
        app.load(fn=lambda saved: saved if saved else list(OPTIONAL_TYPES), inputs=saved_remove, outputs=remove)
        api_key.blur(
            fn=handlers.on_key_entered,
            inputs=[api_key, remember],
            outputs=[provider, key_text, key_problem, saved_key, key_entry, key_line],
        )
        change_key.click(
            fn=lambda: ("", "", *key_view(False)),
            outputs=[api_key, saved_key, key_entry, key_line],
        )
        remove.change(fn=lambda chosen: chosen, inputs=remove, outputs=saved_remove)

        view_inputs = [files, fresh, language, api_key, mode]
        view_outputs = [plan, language, retranscribe, start, clear, drop, hint, fresh]
        drop.upload(fn=handlers.add_files, inputs=[drop, files], outputs=[drop, files, fresh]).then(
            fn=handlers.selection_view, inputs=view_inputs, outputs=view_outputs
        )
        folder.upload(
            fn=lambda new, cur: handlers.add_files(new, cur)[1:], inputs=[folder, files], outputs=[files, fresh]
        ).then(fn=handlers.selection_view, inputs=view_inputs, outputs=view_outputs)
        clear.click(fn=lambda: [], outputs=files).then(
            fn=handlers.selection_view, inputs=view_inputs, outputs=view_outputs
        )
        for trigger in (language.change, key_text.change, mode.change):
            trigger(fn=handlers.selection_view, inputs=view_inputs, outputs=view_outputs)

        review_outputs = [queue_list, pick, line, text, file_remove, found, missed,
                          approve_btn, code_btn, clean_btn, rest_btn]
        review_inputs = [pick, queue, failed]
        app.load(fn=handlers.resume_reviews, outputs=[review_panel, queue, failed, *review_outputs])
        start.click(
            fn=handlers.begin_run, inputs=[files, language], outputs=[start, working, review_panel, results],
        ).then(
            fn=handlers.prepare_run,
            inputs=[files, language, remove, retranscribe],
            outputs=[working, review_panel, queue, failed, *review_outputs],
            show_progress_on=[working],
        ).then(fn=handlers.selection_view, inputs=view_inputs, outputs=view_outputs)

        pick.input(fn=handlers.review_view, inputs=review_inputs, outputs=review_outputs)
        found.input(fn=handlers.on_removed_edit, inputs=[found, *review_inputs], outputs=review_outputs)
        missed.input(fn=handlers.on_missed_edit, inputs=[missed, *review_inputs], outputs=review_outputs)
        add.click(fn=handlers.on_add, inputs=[add_text, add_type, *review_inputs], outputs=[add_text, *review_outputs])
        add_text.submit(fn=handlers.on_add, inputs=[add_text, add_type, *review_inputs], outputs=[add_text, *review_outputs])
        file_remove.input(fn=handlers.on_types, inputs=[file_remove, *review_inputs], outputs=review_outputs)
        approve_btn.click(fn=handlers.on_approve, inputs=review_inputs, outputs=review_outputs)
        mode.change(
            fn=lambda m: (gr.update(visible=m == handlers.MODE_CODE), gr.update(visible=m == handlers.MODE_CLEAN)),
            inputs=mode, outputs=[code_btn, clean_btn],
        )

        code_btn.click(
            fn=handlers.begin_coding, inputs=queue, outputs=[code_btn, code_working],
        ).then(
            fn=handlers.code_run,
            inputs=[queue, provider, api_key],
            outputs=[code_working, review_panel, queue, results, summary, status, findings, table, show, excel, saved],
            show_progress_on=[code_working],
        ).then(fn=handlers.review_view, inputs=review_inputs, outputs=review_outputs)
        clean_btn.click(fn=handlers.clean_run, inputs=queue, outputs=[clean_file, clean_note]).then(
            fn=handlers.review_view, inputs=review_inputs, outputs=review_outputs
        )
        show.change(fn=filter_rows, inputs=[findings, show], outputs=table)
        rest_btn.click(
            fn=handlers.on_approve_rest, inputs=[confirm_box, *review_inputs], outputs=review_outputs,
            js=approve_rest_js(),
        )

    return app


def approve_rest_js() -> str:
    """The browser code that asks before "Approve all the rest". JSON is a valid JS string literal."""
    return f"(c, p, q, f) => [confirm({json.dumps(APPROVE_REST_QUESTION)}), p, q, f]"


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        load_config(handlers.CONFIG_PATH)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    handlers.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log_file = logging.FileHandler(handlers.LOG_PATH, encoding="utf-8")
    log_file.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(log_file)

    build_app().launch(
        # Only this computer can open the page. Docker sets 0.0.0.0 so the
        # host can reach the container.
        server_name=os.environ.get("GRADIO_SERVER_NAME", "127.0.0.1"),
        # None: Gradio reads GRADIO_SERVER_PORT (default 7860) and takes the
        # next free port when that one is busy.
        server_port=None,
        inbrowser=True,
        quiet=False,
        allowed_paths=[str(handlers.OUTPUT_DIR)],
        show_api=False,
    )
