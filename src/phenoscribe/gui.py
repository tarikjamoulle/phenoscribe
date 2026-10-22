"""Gradio web UI for Phenoscribe.

The user drags audio files onto the page, pastes an API key one time and
clicks Start. Results go to a new dated Excel in `PHENOSCRIBE_OUTPUT_DIR`
(default `/app/output`; the start scripts set `Documents/Phenoscribe`).
"""

import logging
import os
from pathlib import Path

import anthropic
import gradio as gr
import openai
import requests

from phenoscribe.config import load_config
from phenoscribe.llm import use_api_key
from phenoscribe.output import dedup_by_code
from phenoscribe.pipeline import BatchResult, run_batch
from phenoscribe.transcribe import AUDIO_EXTENSIONS, TEXT_EXTENSIONS

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

SUPPORTED_EXTENSIONS = AUDIO_EXTENSIONS | TEXT_EXTENSIONS

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

RESULT_HEADERS = ["Patient", "HPO term", "HPO code", "Present/Absent", "Review"]

# The browser keeps the key in local storage. Gradio encrypts the stored value
# with this secret; it must stay the same across restarts, or the saved key is
# lost. It protects nothing, so the page calls it plain storage.
KEY_STORAGE = {"storage_key": "phenoscribe_api_key", "secret": "phenoscribe-local"}


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
    if isinstance(
        error,
        (openai.APIConnectionError, anthropic.APIConnectionError, requests.ConnectionError),
    ):
        return "No internet"
    message = str(error)
    if "Unsupported file type" in message or "Failed to load audio" in message:
        return "Audio format not supported"
    if type(error).__module__.startswith("av."):
        return "Audio format not supported"
    return f"Failed ({type(error).__name__}). Details are in {LOG_PATH}"


def check_key(provider: str, api_key: str) -> str:
    """Make one small call (list the models) to see if the key works."""
    api_key = api_key.strip() or env_key_for(provider)
    if not api_key:
        return "Paste a key first."
    try:
        if provider == "anthropic":
            anthropic.Anthropic(api_key=api_key).models.list(limit=1)
        else:
            openai.OpenAI(api_key=api_key).models.list()
    except Exception as e:  # noqa: BLE001 — every failure becomes one plain line
        logger.warning("Key check failed: %r", e)
        return plain_error(e)
    return "Key works"


def on_key_change(api_key: str, remember: bool):
    """Pick the provider from the key prefix, and save or forget the key."""
    provider = gr.update(value=provider_for_key(api_key)) if api_key.strip() else gr.update()
    return provider, api_key if remember else ""


def status_lines(batch: BatchResult) -> str:
    lines = []
    for r in batch.files:
        if r.error:
            lines.append(f"- {r.filename}: {plain_error(r.error)}")
        else:
            lines.append(f"- {r.filename} ({r.patient_id}): {r.n_codes} findings")
    return "\n".join(lines)


def result_rows(batch: BatchResult) -> list[list]:
    """The coded rows, one per finding, deduplicated the same way as the Excel."""
    rows = []
    for r in batch.files:
        for m in dedup_by_code(r.matches, "detailed"):
            rows.append(
                [
                    r.patient_id,
                    m["hpo_term"],
                    m["hpo_id"] or "",
                    "Absent" if m.get("negated") else "Present",
                    "REVIEW" if m.get("needs_review") else "",
                ]
            )
    return rows


def run_pipeline(
    files: list[str] | None,
    language: str,
    provider: str,
    api_key: str,
    skip_transcription: bool,
    progress=gr.Progress(),
):
    if not files:
        return "Add one or more files first.", [], None

    pasted_key = api_key.strip()
    if not pasted_key and not env_key_for(provider):
        return "Paste your API key first.", [], None

    config = load_config(CONFIG_PATH)
    config.llm.provider = provider
    config.llm.model = model_for(provider, config)
    config.transcription.language = language

    with use_api_key(provider, pasted_key):
        batch = run_batch(
            files,
            config,
            OUTPUT_DIR,
            on_progress=lambda fraction, message: progress(fraction, desc=message),
            skip_transcription=skip_transcription,
        )

    excel = str(batch.excel_path) if batch.excel_path else None
    return status_lines(batch), result_rows(batch), excel


def build_app() -> gr.Blocks:
    with gr.Blocks(title="Phenoscribe") as app:
        gr.Markdown("# Phenoscribe")
        gr.Markdown(
            "Turns patient interviews into HPO phenotype codes. "
            "The recordings stay on this computer. Only text with the personal "
            "details removed goes to the AI provider."
        )
        gr.Markdown(f"Results are saved in `{OUTPUT_DIR}`.")

        with gr.Row():
            with gr.Column(scale=2):
                files = gr.File(
                    file_count="multiple",
                    file_types=sorted(SUPPORTED_EXTENSIONS),
                    label="Recordings (drag the files here)",
                )

            with gr.Column(scale=1):
                api_key = gr.Textbox(
                    label="API key",
                    type="password",
                    placeholder="Paste your OpenAI or Anthropic API key",
                )
                remember = gr.Checkbox(
                    value=True,
                    label="Remember this key in this browser",
                    info=(
                        "The key is kept as plain data in this browser's profile. "
                        "The app writes no key to disk."
                    ),
                )
                with gr.Row():
                    provider = gr.Radio(
                        choices=PROVIDER_CHOICES, value="openai", label="Provider"
                    )
                    test_btn = gr.Button("Test key", size="sm")
                key_status = gr.Markdown()
                language = gr.Dropdown(
                    choices=LANGUAGE_CHOICES,
                    value="fr",
                    label="Language of the recordings",
                )
                with gr.Accordion("Advanced", open=False):
                    skip_transcription = gr.Checkbox(
                        value=False,
                        label="I already have the transcripts (skip transcription)",
                    )

        start_btn = gr.Button("Start", variant="primary", size="lg")

        gr.Markdown("### Results")
        status = gr.Markdown()
        results = gr.Dataframe(headers=RESULT_HEADERS, interactive=False, wrap=True)
        excel_download = gr.File(label="Excel file", interactive=False)

        saved_key = gr.BrowserState("", **KEY_STORAGE)
        app.load(fn=lambda key: key, inputs=saved_key, outputs=api_key)
        api_key.change(
            fn=on_key_change, inputs=[api_key, remember], outputs=[provider, saved_key]
        )
        remember.change(
            fn=lambda key, keep: key if keep else "",
            inputs=[api_key, remember],
            outputs=saved_key,
        )
        test_btn.click(fn=check_key, inputs=[provider, api_key], outputs=key_status)
        start_btn.click(
            fn=run_pipeline,
            inputs=[files, language, provider, api_key, skip_transcription],
            outputs=[status, results, excel_download],
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
    )


if __name__ == "__main__":
    main()
