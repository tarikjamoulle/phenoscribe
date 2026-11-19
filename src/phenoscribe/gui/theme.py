"""The look of the page: the Gradio theme, the CSS and the colours of the highlighted types.

The page loads no font from the internet: a privacy tool makes no request to a
third party. The fonts are the system fonts of the computer.
"""

import gradio as gr

SANS = ["system-ui", "-apple-system", "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "sans-serif"]
SERIF = ["Iowan Old Style", "Palatino Linotype", "Palatino", "Georgia", "serif"]
MONO = ["ui-monospace", "SF Mono", "Menlo", "Consolas", "monospace"]
# CSS keywords, not font names. Gradio puts quotes around each name of a theme
# font, which breaks a keyword, so the theme gets the named fonts only.
_KEYWORDS = {"system-ui", "-apple-system", "ui-monospace"}


def _stack(fonts: list[str]) -> str:
    """A CSS font-family value: a name with a space in quotes."""
    return ", ".join(f"'{f}'" if " " in f else f for f in fonts)


# Colors of the highlighted types in the review text (light and dark both read).
TYPE_COLORS = {
    "name": "#E8A87C", "place": "#85C1B9", "organization": "#A9B8E0", "phone": "#D4A5D9",
    "email": "#D4A5D9", "ID number": "#D4A5D9", "web address": "#D4A5D9", "date": "#E6D38A",
    "age": "#B5D99C", "gender": "#B5D99C", "job title": "#B5D99C", "other": "#C9C9C9",
}

THEME = gr.themes.Base(
    primary_hue=gr.themes.Color(
        c50="#EEF4F3", c100="#D5E5E2", c200="#ABCBC6", c300="#7FAEA8", c400="#4F8C85",
        c500="#1F5C55", c600="#1A4F49", c700="#16443F", c800="#113632", c900="#0C2724",
        c950="#081A18",
    ),
    neutral_hue="gray",
    radius_size=gr.themes.sizes.radius_lg,
    font=[f for f in SANS if f not in _KEYWORDS],
    font_mono=[f for f in MONO if f not in _KEYWORDS],
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

# The font stacks as CSS variables, for the classes below.
_FONT_VARIABLES = f":root {{ --ps-sans: {_stack(SANS)}; --ps-serif: {_stack(SERIF)}; --ps-mono: {_stack(MONO)}; }}"

CSS = _FONT_VARIABLES + """
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
.ps-title { font-family: var(--ps-serif); font-size: 32px; letter-spacing: -0.01em; }
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
.ps-file { font-family: var(--ps-mono); flex: 1 1 170px; color: inherit; }
.ps-tag { font-size: 12px; border-radius: 6px; padding: 2px 8px; background: var(--ps-tag-audio); }
.ps-tag.text { background: var(--ps-tag-text); }
.ps-tag.skip { background: var(--ps-warn-tag); }
.ps-what { flex: 1 1 260px; }
.ps-count { font-size: 15px; font-weight: 600; margin: 4px 2px 10px; }
.ps-done { margin-top: 10px; }
.ps-done summary { cursor: pointer; font-size: 15px; font-weight: 600; margin: 4px 2px 10px; }
@keyframes ps-flash { from { background: var(--ps-new); } to { background: transparent; } }
.ps-row.new { animation: ps-flash 1.6s ease-out; }
.ps-working { --font-mono: var(--ps-sans); }
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
.ps-table, .ps-table * { font-family: var(--ps-sans) !important; }
.ps-h1 { font-family: var(--ps-serif); font-size: 40px; line-height: 1.1; margin: 8px 0 0; }
.ps-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }
.ps-tile { background: var(--ps-card); border: 1px solid var(--ps-line); border-radius: 14px; padding: 14px 20px; }
.ps-tile b { display: block; font-family: var(--ps-serif); font-weight: 400;
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
