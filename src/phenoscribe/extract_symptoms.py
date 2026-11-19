"""Symptom extraction from clean transcripts (LLM Call 1).

The prompt has one set of rules in English. French and English add their own
negation cues and worked examples; any other language runs the rules with no
examples, and is not validated.
"""

import json
import logging

from phenoscribe.config import LANGUAGE_NAMES, LLMConfig
from phenoscribe.findings import SOURCES, Finding
from phenoscribe.llm import llm_call
from phenoscribe.text import strip_code_fence

logger = logging.getLogger(__name__)

# The rules of the extraction prompt, shared by every language. The slots
# {language}, {patient_example}, ... come from PROMPT_PARTS.
RULES = """\
You are a clinical phenotyping expert. You read {language} medical interview transcripts \
and extract every symptom, complaint, or clinical finding about the patient. \
The transcript has two speakers, the doctor and the patient. Each line starts with \
"D:" (the doctor speaks) or "P:" (the patient speaks). When the lines have no such label, \
tell the speakers apart by what they say: the doctor asks the questions, and the patient \
answers them, with yes, no, or a description. A question is almost always the doctor's.

For each finding, output:
- patient_verbatim: the patient's exact words in {language}, copied from a "P:" line. Never \
copy the doctor's question here. For a "doctor" finding only, the doctor's words
- source: who first names the finding, one of:
  - "patient": the patient names it without a question about it{patient_example}
  - "confirmed": the doctor names it in a question or a suggestion, and the patient answers \
yes, no, or with a description
  - "doctor": only the doctor names it, and the patient does not answer about it (a summary \
of an earlier visit, or a question with no answer)
- clinical_term: the standardized English medical term for this finding
- negated: true if the finding is denied or absent, false if the patient has it
- frequency: how often it occurs, normalized to one of: very frequent, frequent, occasional, very rare. Empty string if not stated.
- onset: when it began, normalized to one of: congenital, neonatal, antenatal, childhood, juvenile, adult. Empty string if not stated.
- severity: how bad it is, normalized to one of: borderline, mild, moderate, severe, profound. Empty string if not stated.
- context: any remaining temporal or qualifying detail that does not fit the fields above (e.g., "since March 2021", "worse after meals")
- mentions: the places in the transcript where this finding comes up, in transcript order, \
at most 5. Each item is a short quote (3 to 10 words) copied character for character from the \
transcript, without the [mm:ss] time markers and without the "D:" or "P:" label. Quote \
"P:" lines{answer_quote}, not the doctor's question. Include the \
quote of patient_verbatim.

Negation is critical. A denied or absent finding must have negated=true.{negation_cues}
A finding the patient actively reports having is negated=false.

Rules:
- Extract ALL findings mentioned, present or absent, even if mild or historical
- Set negated correctly for every finding. Do NOT drop denied findings; mark them negated=true
- Use standard English medical terminology for clinical_term (e.g., "headache", "fatigue", "abdominal pain")
- Keep patient_verbatim in the original {language}
- For frequency, onset, severity: only fill them when the text states it; otherwise use an empty string
- If the same finding is mentioned multiple times, include it once with combined detail, \
and list each place in mentions
- One symptom for each finding. {two_findings} Keep a fixed medical phrase whole \
("nausea and vomiting")
- Do NOT include diagnoses (e.g., "COVID-19"), treatments, or procedures — only symptoms and findings
- A comparison to a past event or illness is not a finding. {comparison}
- {activity}
- Do NOT invent findings not mentioned in the text
- {answers}

Output ONLY a JSON array. No other text. Put each finding on one line, with no indentation. Example:
{output_example}"""

# The language-specific parts of the prompt. French is the benchmarked text;
# GENERIC serves a language with no examples of its own.
PROMPT_PARTS = {
    "fr": {
        "patient_example": ' ("je me sens vidée chaque après-midi")',
        "answer_quote": ' (the patient\'s answer "Oui, le soir.")',
        "negation_cues": """ French negation cues include:
ne...pas, n'...pas, pas de, pas d', plus de, aucun, aucune, sans, absence de, jamais, ni...ni, \
nie, dément, ne présente pas, n'a pas de. Examples:
- "je n'ai pas de fièvre" -> fever, negated=true
- "aucune douleur thoracique" -> chest pain, negated=true
- "sans perte de poids" -> weight loss, negated=true
- "il nie toute céphalée" -> headache, negated=true""",
        "two_findings": '"nausées et diarrhée" gives two findings, nausea and diarrhea, \
with the same patient_verbatim and mentions.',
        "comparison": '"comme après ma commotion cérébrale" \
is not "post-concussion syndrome": extract the symptoms the patient describes, not the comparison',
        "activity": 'When the patient ties a daily activity to a symptom ("quand je passe l\'aspirateur, \
la pièce tourne"), extract the symptom (vertigo), not the activity ("difficulty vacuuming")',
        "answers": """Source and negation of an answer to the doctor. Examples:
  - "Des palpitations ?" "Oui, le soir." -> palpitations, source "confirmed", negated=false
  - "Comme des fourmis dans les mains ?" "Oui, c'est ça." -> paresthesia, source "confirmed", \
negated=false. The doctor's words name the symptom, so it is "confirmed", not "patient"
  - "Et de la fièvre ?" "Non." -> fever, source "confirmed", negated=true
  - "Le goût, l'odorat ?" "Normal." -> two findings, taste and smell disorder, source \
"confirmed", negated=true
  - "Ces vertiges, c'est fini ?" "Non, ça continue." -> vertigo, negated=false. The "non" \
denies that the symptom stopped, so the patient still has it
  - "La dernière fois, vous aviez aussi des nausées." and the patient does not answer about \
them -> nausea, source "doctor\"""",
        "output_example": """[
  {"patient_verbatim": "j'ai mal au ventre", "clinical_term": "abdominal pain", "negated": false, "source": "patient", "frequency": "", "onset": "adult", "severity": "severe", "context": "worse after meals", "mentions": ["j'ai mal au ventre", "le ventre me fait encore mal après le repas"]},
  {"patient_verbatim": "je n'ai pas de fièvre", "clinical_term": "fever", "negated": true, "source": "confirmed", "frequency": "", "onset": "", "severity": "", "context": "", "mentions": ["je n'ai pas de fièvre"]},
  {"patient_verbatim": "je suis fatigué", "clinical_term": "fatigue", "negated": false, "source": "patient", "frequency": "frequent", "onset": "", "severity": "", "context": "since COVID infection, March 2021", "mentions": ["je suis fatigué", "toujours cette fatigue le soir"]}
]""",
    },
    "en": {
        "patient_example": ' ("I feel drained every afternoon")',
        "answer_quote": ' (the patient\'s answer "Yes, in the evening.")',
        "negation_cues": """ English negation cues include:
no, not, never, none, nothing, without, no longer, denies, doesn't, don't, didn't, haven't, \
hasn't, isn't, neither...nor, free of, negative for. Examples:
- "I don't have a fever" -> fever, negated=true
- "no chest pain" -> chest pain, negated=true
- "without any weight loss" -> weight loss, negated=true
- "he denies any headache" -> headache, negated=true""",
        "two_findings": '"nausea and diarrhea" gives two findings, nausea and diarrhea, \
with the same patient_verbatim and mentions.',
        "comparison": '"like after my concussion" is not "post-concussion syndrome": \
extract the symptoms the patient describes, not the comparison',
        "activity": 'When the patient ties a daily activity to a symptom ("when I vacuum, the room \
spins"), extract the symptom (vertigo), not the activity ("difficulty vacuuming")',
        "answers": """Source and negation of an answer to the doctor. Examples:
  - "Any palpitations?" "Yes, in the evening." -> palpitations, source "confirmed", negated=false
  - "Like pins and needles in your hands?" "Yes, that's it." -> paresthesia, source "confirmed", \
negated=false. The doctor's words name the symptom, so it is "confirmed", not "patient"
  - "And any fever?" "No." -> fever, source "confirmed", negated=true
  - "Your taste, your smell?" "Normal." -> two findings, taste and smell disorder, source \
"confirmed", negated=true
  - "Has the dizziness stopped?" "No, it goes on." -> vertigo, negated=false. The "no" \
denies that the symptom stopped, so the patient still has it
  - "Last time you also had some nausea." and the patient does not answer about \
it -> nausea, source "doctor\"""",
        "output_example": """[
  {"patient_verbatim": "my stomach hurts", "clinical_term": "abdominal pain", "negated": false, "source": "patient", "frequency": "", "onset": "adult", "severity": "severe", "context": "worse after meals", "mentions": ["my stomach hurts", "my stomach still hurts after the meal"]},
  {"patient_verbatim": "I don't have a fever", "clinical_term": "fever", "negated": true, "source": "confirmed", "frequency": "", "onset": "", "severity": "", "context": "", "mentions": ["I don't have a fever"]},
  {"patient_verbatim": "I'm tired", "clinical_term": "fatigue", "negated": false, "source": "patient", "frequency": "frequent", "onset": "", "severity": "", "context": "since COVID infection, March 2021", "mentions": ["I'm tired", "always this tiredness in the evening"]}
]""",
    },
}

GENERIC = {
    "patient_example": "",
    "answer_quote": " (the patient's answer)",
    "negation_cues": "",
    "two_findings": "Two symptoms named together give two findings, with the same \
patient_verbatim and mentions.",
    "comparison": "Extract the symptoms the patient describes, not the comparison",
    "activity": "When the patient ties a daily activity to a symptom, extract the symptom, \
not the activity",
    "answers": 'Source and negation of an answer to the doctor: the doctor names the finding, \
so the source is "confirmed". A "yes" gives negated=false and a "no" gives negated=true. A "no" \
that denies that a symptom stopped means that the patient still has it',
    "output_example": """[
  {"patient_verbatim": "<the patient's words>", "clinical_term": "abdominal pain", "negated": false, "source": "patient", "frequency": "", "onset": "adult", "severity": "severe", "context": "worse after meals", "mentions": ["<a quote>", "<another quote>"]},
  {"patient_verbatim": "<the patient's words>", "clinical_term": "fever", "negated": true, "source": "confirmed", "frequency": "", "onset": "", "severity": "", "context": "", "mentions": ["<a quote>"]}
]""",
}

def language_name(language: str) -> str:
    """The English name of a language code ("fr" -> "French"). An unknown code stays as it is."""
    return LANGUAGE_NAMES.get(language, language)


def is_validated(language: str) -> bool:
    """True for a language with its own prompt examples and tests (French, English)."""
    if language in PROMPT_PARTS:
        return True
    logger.warning("The prompts for %s have no examples of their own and are not validated.",
                   language_name(language))
    return False


def system_prompt(language: str = "fr") -> str:
    """The extraction prompt for the interview language."""
    parts = PROMPT_PARTS[language] if is_validated(language) else GENERIC
    return RULES.format(language=language_name(language), **parts)


# A long interview can make the answer longer than the model's output limit.
# The cut JSON does not parse. The second try asks for no mention quotes, which
# makes the answer much shorter. At temperature 0 the model can also loop and
# give the same "fatigue" line hundreds of times, until the limit.
# RETRY_TEMPERATURE breaks the loop.
RETRY_TEMPERATURE = 0.3
NO_MENTIONS = (
    "\n\nThis transcript is long. Give an empty mentions list ([]) for every finding, "
    "to keep the answer short."
)


def extract_symptoms(transcript: str, llm: LLMConfig, runs: int = 1, language: str = "fr") -> list[Finding]:
    """The findings of a clean transcript (with the speaker labels when they exist).

    The extraction runs ``runs`` times and merge_runs joins the results, so
    each finding carries runs_found and n_runs.
    """
    def ask(system_prompt: str, temperature: float = 0.0) -> str:
        return llm_call(system_prompt=system_prompt, user_prompt=transcript, llm=llm,
                        temperature=temperature)

    prompt = system_prompt(language)

    def one_run() -> list[Finding]:
        first = ask(prompt)
        try:
            return _parse_response(first)
        except ExtractionParseError:
            logger.warning(
                "Symptom answer not valid JSON (%d chars, probably cut off). "
                "Trying again with no mention quotes.",
                len(first),
            )
        second = ask(prompt + NO_MENTIONS, temperature=RETRY_TEMPERATURE)
        try:
            return _parse_response(second)
        except ExtractionParseError:
            # Keep the complete finding lines of both answers, when there are some.
            kept = _salvage(first + "\n" + second)
            if not kept:
                raise
            logger.warning(
                "Second symptom answer also not valid JSON. Kept %d complete findings.",
                len(kept),
            )
            return kept

    return merge_runs([one_run() for _ in range(max(1, runs))])


def merge_runs(runs: list[list[Finding]]) -> list[Finding]:
    """One list from several extraction runs on the same text.

    Two findings are the same when they have the same clinical term (any
    case) and the same negation. The first run's fields stay; the mentions
    of the later runs are added. Each finding gets ``runs_found`` (the run
    numbers, from 1) and ``n_runs``, so match_hpo.flag_unstable can mark a
    finding that is not in every run.
    """
    merged: dict[tuple, dict] = {}
    for number, findings in enumerate(runs, start=1):
        for f in findings:
            key = ((f.get("clinical_term") or "").strip().lower(), bool(f.get("negated", False)))
            if key not in merged:
                merged[key] = dict(f, runs_found=[number], n_runs=len(runs))
                continue
            kept = merged[key]
            if number not in kept["runs_found"]:
                kept["runs_found"].append(number)
            for quote in f.get("mentions") or []:
                if quote not in kept.setdefault("mentions", []):
                    kept["mentions"].append(quote)
            if not kept.get("patient_verbatim") and f.get("patient_verbatim"):
                kept["patient_verbatim"] = f["patient_verbatim"]
    return list(merged.values())


class ExtractionParseError(ValueError):
    """The extractor's response is not a JSON array of findings.

    Raised so the file fails and a rerun codes it again. An empty list here
    would let a truncated answer "complete" with zero rows.
    """


def _parse_response(response: str) -> list[Finding]:
    """The findings of the LLM answer: a JSON array, maybe in a code fence or after a preamble.

    Raises ExtractionParseError when no JSON array can be recovered.
    """
    text = strip_code_fence(response)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to find a JSON array in the response
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError as err:
                raise ExtractionParseError(
                    f"symptom extraction: response is not valid JSON: {text[:200]}"
                ) from err
        else:
            raise ExtractionParseError(
                f"symptom extraction: no JSON array in response: {text[:200]}"
            ) from None

    if not isinstance(data, list):
        raise ExtractionParseError(f"symptom extraction: response is a {type(data).__name__}, not a list")
    return _valid_findings(data)


def _salvage(response: str) -> list[Finding]:
    """The complete finding lines of a cut answer, each finding one time.

    The prompt asks for one finding on each line. A line that parses is kept;
    the cut last line and the repeats of a loop are dropped.
    """
    items, seen = [], set()
    for line in response.splitlines():
        line = line.strip().rstrip(",")
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        key = (str(item.get("clinical_term", "")).strip().lower(), _coerce_bool(item.get("negated", False)))
        if key not in seen:
            seen.add(key)
            items.append(item)
    return _valid_findings(items) if items else []


def _valid_findings(data: list) -> list[Finding]:
    """The findings with a clinical term, with each field in its expected type."""
    valid = []
    for item in data:
        if isinstance(item, dict) and "clinical_term" in item:
            valid.append(
                {
                    "patient_verbatim": item.get("patient_verbatim", ""),
                    "clinical_term": item["clinical_term"],
                    "negated": _coerce_bool(item.get("negated", False)),
                    "source": _coerce_source(item.get("source")),
                    "frequency": _coerce_str(item.get("frequency", "")),
                    "onset": _coerce_str(item.get("onset", "")),
                    "severity": _coerce_str(item.get("severity", "")),
                    "context": _coerce_str(item.get("context", "")),
                    "mentions": _coerce_quotes(item.get("mentions"), item.get("patient_verbatim", "")),
                }
            )

    n_negated = sum(1 for v in valid if v["negated"])
    logger.info(
        "Extracted %d findings from transcript (%d negated/absent).",
        len(valid),
        n_negated,
    )
    return valid


def _coerce_bool(value) -> bool:
    """Coerce a JSON value to bool, tolerating string truthiness from the LLM."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "negated", "absent")
    return bool(value)


def _coerce_source(value) -> str:
    """Who brings up the finding: patient, confirmed or doctor; '' when the LLM gives no valid value."""
    text = _coerce_str(value).lower()
    return text if text in SOURCES else ""


def _coerce_str(value) -> str:
    """Coerce a JSON value to a trimmed string; None/non-strings become ''."""
    if value is None:
        return ""
    return str(value).strip()


def _coerce_quotes(value, verbatim) -> list[str]:
    """The mention quotes as a list of strings; the verbatim when there are none."""
    if isinstance(value, str):
        value = [value]
    quotes = [str(q).strip() for q in (value or []) if q is not None and str(q).strip()]
    if not quotes and verbatim:
        quotes = [str(verbatim).strip()]
    return quotes
