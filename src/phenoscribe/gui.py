"""Gradio web UI for Phenoscribe.

Folder-mounted UX: files come from `PHENOSCRIBE_INPUT_DIR` (default `/app/input`),
results go to `PHENOSCRIBE_OUTPUT_DIR` (default `/app/output`). The user picks
which files to process, hits Run, watches per-file progress, downloads the Excel.
"""

import logging
import os
from pathlib import Path

import gradio as gr

from phenoscribe.config import load_config
from phenoscribe.llm import use_api_key
from phenoscribe.pipeline import run_batch
from phenoscribe.transcribe import AUDIO_EXTENSIONS, TEXT_EXTENSIONS

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

SUPPORTED_EXTENSIONS = AUDIO_EXTENSIONS | TEXT_EXTENSIONS

INPUT_DIR = Path(os.environ.get("PHENOSCRIBE_INPUT_DIR", "/app/input"))
OUTPUT_DIR = Path(os.environ.get("PHENOSCRIBE_OUTPUT_DIR", "/app/output"))
CONFIG_PATH = os.environ.get("PHENOSCRIBE_CONFIG", "config.yaml")

LANGUAGE_CHOICES = [
    ("French", "fr"),
    ("English", "en"),
    ("Dutch", "nl"),
    ("German", "de"),
    ("Spanish", "es"),
    ("Italian", "it"),
]

PROVIDER_MODELS = {
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
    "anthropic": ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"],
}


def list_input_files() -> list[str]:
    if not INPUT_DIR.is_dir():
        return []
    return [
        f.name
        for f in sorted(INPUT_DIR.iterdir())
        if f.is_file()
        and f.suffix.lower() in SUPPORTED_EXTENSIONS
        and not f.stem.endswith("_pseudo")
    ]


PROVIDER_ENV_VAR = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def env_key_for(provider: str) -> str:
    return os.environ.get(PROVIDER_ENV_VAR.get(provider, ""), "")


def refresh_files():
    return gr.update(choices=list_input_files(), value=[])


def update_model_choices(provider: str):
    models = PROVIDER_MODELS.get(provider, [])
    return gr.update(choices=models, value=models[0] if models else None)


def update_api_key_field(provider: str):
    if env_key_for(provider):
        return gr.update(value="", placeholder="(loaded from environment)", interactive=False)
    return gr.update(value="", placeholder=f"Paste your {provider} API key", interactive=True)


def run_pipeline(
    selected_files: list[str],
    do_transcribe: bool,
    do_diarize: bool,
    language: str,
    provider: str,
    model: str,
    api_key: str,
    progress=gr.Progress(),
):
    if not selected_files:
        return [["(no files selected)", "", 0, ""]], None

    env_var = PROVIDER_ENV_VAR.get(provider)
    pasted_key = api_key.strip()
    if env_var and not pasted_key and not os.environ.get(env_var):
        return [["(no API key)", f"set {env_var} or paste it in the box", 0, ""]], None

    config = load_config(CONFIG_PATH)
    config.llm.provider = provider
    config.llm.model = model
    config.transcription.language = language
    config.diarization.enabled = do_diarize

    with use_api_key(provider, pasted_key):
        batch = run_batch(
            [str(INPUT_DIR / f) for f in selected_files],
            config,
            OUTPUT_DIR,
            on_progress=lambda fraction, message: progress(fraction, desc=message),
            skip_transcription=not do_transcribe,
        )

    rows = [
        [r.filename, "failed" if r.error else "done", r.n_codes, str(r.error or "")]
        for r in batch.files
    ]
    return rows, str(batch.excel_path) if batch.excel_path else None


def build_app() -> gr.Blocks:
    providers = list(PROVIDER_MODELS.keys())
    hf_present = bool(os.environ.get("HF_TOKEN"))
    initial_provider = providers[0]
    initial_models = PROVIDER_MODELS[initial_provider]
    initial_env_key = env_key_for(initial_provider)

    with gr.Blocks(title="Phenoscribe") as app:
        gr.Markdown("# Phenoscribe")
        gr.Markdown(
            "Process patient interviews into HPO phenotype codes. "
            "Files stay on this machine; only pseudonymized text is sent to the LLM."
        )
        gr.Markdown(f"**Input:** `{INPUT_DIR}`  •  **Output:** `{OUTPUT_DIR}`")

        with gr.Row():
            with gr.Column(scale=2):
                gr.Markdown("### Files")
                refresh_btn = gr.Button("Refresh file list", size="sm")
                files = gr.CheckboxGroup(
                    choices=list_input_files(),
                    label="Select files to process",
                )

            with gr.Column(scale=1):
                gr.Markdown("### Options")
                do_transcribe = gr.Checkbox(
                    value=True,
                    label="Transcribe audio",
                    info="Off = reuse saved transcript from a previous run",
                )
                do_diarize = gr.Checkbox(
                    value=False,
                    label="Speaker diarization",
                    info=(
                        "Splits doctor/patient turns"
                        if hf_present
                        else "Disabled — set HF_TOKEN in .env to enable"
                    ),
                    interactive=hf_present,
                )
                language = gr.Dropdown(
                    choices=LANGUAGE_CHOICES,
                    value="fr",
                    label="Audio language",
                )
                provider = gr.Dropdown(
                    choices=providers,
                    value=initial_provider,
                    label="LLM provider",
                )
                model = gr.Dropdown(
                    choices=initial_models,
                    value=initial_models[0] if initial_models else None,
                    label="LLM model",
                )
                api_key = gr.Textbox(
                    label="API key",
                    type="password",
                    placeholder=(
                        "(loaded from environment)"
                        if initial_env_key
                        else f"Paste your {initial_provider} API key"
                    ),
                    interactive=not initial_env_key,
                    info="Used for this run only — passed directly to the LLM client and dropped when the run ends. Not stored on disk.",
                )

        run_btn = gr.Button("Run", variant="primary", size="lg")

        gr.Markdown("### Results")
        results = gr.Dataframe(
            headers=["File", "Status", "HPO codes", "Error"],
            interactive=False,
            wrap=True,
        )
        excel_download = gr.File(label="results.xlsx", interactive=False)

        refresh_btn.click(fn=refresh_files, outputs=files)
        provider.change(fn=update_model_choices, inputs=provider, outputs=model)
        provider.change(fn=update_api_key_field, inputs=provider, outputs=api_key)
        run_btn.click(
            fn=run_pipeline,
            inputs=[
                files,
                do_transcribe,
                do_diarize,
                language,
                provider,
                model,
                api_key,
            ],
            outputs=[results, excel_download],
        )

    return app


def main():
    build_app().launch(
        server_name="0.0.0.0",
        server_port=7860,
        inbrowser=False,
        quiet=False,
        allowed_paths=[str(OUTPUT_DIR)],
    )


if __name__ == "__main__":
    main()
