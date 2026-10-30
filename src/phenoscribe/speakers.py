"""Who says what: the doctor (D) or the patient (P).

The transcripts have no speaker labels, and little punctuation. The poster
claims the symptoms come from the patient's own words, so the pipeline must
know who says each quote. There is no diarization (the user's decision): the
model works it out from the text, in a pass of its own, before the symptom
extraction.

Pass 1 (``label_speakers``): the model copies the clean text, one turn for
each line, each line with the prefix ``D:`` or ``P:``. The code checks that
the copy keeps the words of the original (``copy_similarity``); a chunk that
loses words is asked again, and a second failure gives the plain text back,
with a warning, so the file is still coded.

The extractor then reads the labeled text and quotes only P lines
(extract_symptoms.SYSTEM_PROMPT). ``check_speakers`` finds each quote in the
labeled text and records who says it ("Spoken by" in the Excel). A finding
whose quotes are all in D lines is not the patient's: it becomes "doctor"
(status "Doctor only") and needs review. A finding whose quotes are nowhere
in the text also needs review.
"""

import logging
import re
from difflib import SequenceMatcher

from phenoscribe.llm import llm_call

logger = logging.getLogger(__name__)

SPEAKER_PROMPT = """\
You read a French medical interview transcript between a doctor and a patient. \
The transcript has no speaker labels and little punctuation. One speaker's words often \
run into the next speaker's words without a break.

Split the text into turns and say who speaks. Rules:
- The doctor asks the questions, suggests symptoms, and sums up. The patient answers: \
yes, no, or a description of what they feel. A question is almost always the doctor's.
- Write one turn on each line. Start the line with "D: " (doctor) or "P: " (patient).
- Copy the words exactly as they are, in the same order. Do not drop, add, correct or \
translate a word. Keep the "[mm:ss]" time markers where they are.
- A new line starts only where the speaker changes.
- Write nothing else: no title, no comment, no code fence.

Example input:
dis moi le principal problème que tu as je fais des malaises des malaises oui explique \
j'ai des vertiges

Example output:
D: dis moi le principal problème que tu as
P: je fais des malaises
D: des malaises oui explique
P: j'ai des vertiges"""

CONTEXT_NOTE = (
    "The last turns before this part, already labeled (do not repeat them):\n{context}\n\n"
    "Label this part:\n{text}"
)

# One model call per chunk: the model copies the text, so a long transcript
# would hit the output limit in one call (MGA.434 is 35,000 characters).
CHUNK_CHARS = 7000

# A labeled chunk must keep this share of the words of the original (0 to 1).
MIN_COPY_SIMILARITY = 0.9

_LABEL = re.compile(r"^\s*(D|P|docteur|médecin|medecin|patiente?)\s*:\s*", re.IGNORECASE)
_WORD = re.compile(r"\w+")


class SpeakerLabelError(ValueError):
    """The model's copy of a chunk lost or changed too many words."""


def label_speakers(
    text: str,
    provider: str = "openai",
    model: str = "gpt-4o",
    ollama_base_url: str = "http://localhost:11434",
) -> str:
    """The clean text with one turn on each line, prefixed ``D:`` or ``P:``.

    The text goes to the model in chunks (CHUNK_CHARS). Each chunk after the
    first gets the last two labeled turns of the chunk before, so the model
    keeps track of who speaks. A chunk whose copy fails the word check is
    asked once more; a second failure raises SpeakerLabelError.
    """
    labeled_chunks: list[str] = []
    for chunk in split_chunks(text, CHUNK_CHARS):
        context = "\n".join(labeled_chunks[-1].splitlines()[-2:]) if labeled_chunks else ""
        prompt = CONTEXT_NOTE.format(context=context, text=chunk) if context else chunk
        labeled = ""
        for attempt in range(2):
            answer = llm_call(
                system_prompt=SPEAKER_PROMPT,
                user_prompt=prompt,
                provider=provider,
                model=model,
                ollama_base_url=ollama_base_url,
                temperature=0.0,
            )
            labeled = _strip_fence(answer)
            score = copy_similarity(chunk, labeled)
            if score >= MIN_COPY_SIMILARITY:
                break
            logger.warning(
                "Speaker labels: the copy of a chunk keeps only %.0f%% of the words (try %d).",
                100 * score, attempt + 1,
            )
        else:
            raise SpeakerLabelError(
                f"speaker labels: the model's copy lost too many words ({100 * score:.0f}% kept)"
            )
        labeled_chunks.append(labeled.strip())
    return "\n".join(labeled_chunks)


def split_chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Pieces of about ``size`` characters, cut at a sentence end or a space."""
    chunks: list[str] = []
    rest = text
    while len(rest) > size:
        window = rest[:size]
        cut = max(window.rfind(". "), window.rfind("? "), window.rfind("! "))
        if cut < size // 2:
            cut = window.rfind(" ")
        if cut < size // 2:
            cut = size
        else:
            cut += 1
        chunks.append(rest[:cut].strip())
        rest = rest[cut:]
    if rest.strip():
        chunks.append(rest.strip())
    return chunks


def copy_similarity(original: str, labeled: str) -> float:
    """How much of the original's word sequence the labeled copy keeps (0 to 1)."""
    from phenoscribe.review import fold

    a = _WORD.findall(fold(original))
    b = _WORD.findall(fold(strip_labels(labeled)))
    if not a:
        return 1.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def _strip_fence(answer: str) -> str:
    lines = [line for line in answer.strip().splitlines() if not line.strip().startswith("```")]
    return "\n".join(lines)


def strip_labels(labeled: str) -> str:
    """The labeled text without the ``D:`` / ``P:`` prefixes."""
    return "\n".join(_LABEL.sub("", line) for line in labeled.splitlines())


def parse_turns(labeled: str) -> tuple[str, list[tuple[str, int, int]]]:
    """The text without labels, and each turn as (speaker, start, end) in that text.

    The speaker is "doctor", "patient" or "" (a line with no label, which
    continues the turn before it when there is one).
    """
    text_parts: list[str] = []
    turns: list[tuple[str, int, int]] = []
    offset = 0
    for line in labeled.splitlines():
        m = _LABEL.match(line)
        if m:
            speaker = "doctor" if m.group(1)[0].upper() == "D" or m.group(1).lower().startswith("m") else "patient"
            body = line[m.end():]
        else:
            speaker = turns[-1][0] if turns else ""
            body = line
        start = offset
        text_parts.append(body)
        offset += len(body) + 1  # the newline
        if turns and turns[-1][0] == speaker and not m:
            turns[-1] = (speaker, turns[-1][1], offset)
        else:
            turns.append((speaker, start, offset))
    return "\n".join(text_parts), turns


def speaker_at(turns: list[tuple[str, int, int]], start: int, end: int) -> str:
    """The speaker whose turn covers most of the span ``start``-``end``."""
    best, best_overlap = "", 0
    for speaker, s, e in turns:
        overlap = min(e, end) - max(s, start)
        if overlap > best_overlap:
            best, best_overlap = speaker, overlap
    return best


# The first words of a patient turn that answer the doctor's question. A
# turn that starts with one of these is an answer, so the finding the doctor
# names just before it is "confirmed" (yes or no), not "doctor only".
_ANSWER = re.compile(  # on review.fold text: no accents, no capitals
    r"^(oui|ouais|non|si|surtout|un peu|pas |normal|jamais|parfois|toujours|exact|"
    r"c'est ca|voila|tout a fait|ca continue|ca va|rien|beaucoup|moins|plus|des fois|"
    r"de temps en temps|souvent)\b"
)


def check_speakers(matches: list[dict], labeled: str) -> None:
    """Record who says the quotes of each finding, and correct the source.

    Sets ``spoken_by`` on each match: "patient" when at least one quote is in
    a patient turn, "patient answer" when the quotes are the doctor's words
    and the patient's next turn answers them ("Oui, j'ai l'impression."),
    "doctor" when every quote found is in a doctor turn with no answer, or
    "not found" when no quote is in the text.

    - "patient answer": the source becomes "confirmed", and the patient's
      answer becomes the verbatim. The model's negation stays.
    - "doctor": the patient's words for this finding do not exist, so the
      source becomes "doctor" (status "Doctor only"); the row needs review
      when the model said otherwise.
    - "patient" with source "doctor": the model contradicts itself; the
      source becomes "confirmed" and the row needs review.
    - "not found": the row needs review; the status stays.
    """
    from phenoscribe.timeline import Transcript

    text, turns = parse_turns(labeled)
    transcript = Transcript(text)
    for m in matches:
        quotes = [q for q in (m.get("mentions") or []) if q and q.strip()]
        if m.get("patient_verbatim"):
            quotes.append(m["patient_verbatim"])
        speakers = set()
        places: list[tuple[int, int]] = []
        for quote in quotes:
            for start, end, _ in transcript.locate_all(quote):
                speakers.add(speaker_at(turns, start, end))
                places.append((start, end))
        speakers.discard("")
        if "patient" in speakers:
            m["spoken_by"] = "patient"
            if m.get("source") == "doctor":
                m["source"] = "confirmed"
                m["needs_review"] = True
        elif "doctor" in speakers:
            answer = next((a for a in (patient_answer(text, turns, p) for p in places) if a), "")
            if answer:
                m["spoken_by"] = "patient answer"
                m["source"] = "confirmed"
                m["patient_verbatim"] = answer
            else:
                m["spoken_by"] = "doctor"
                if m.get("source") != "doctor":
                    m["source"] = "doctor"
                    m["needs_review"] = True
        else:
            m["spoken_by"] = "not found"
            m["needs_review"] = True


def patient_answer(text: str, turns: list[tuple[str, int, int]], place: tuple[int, int]) -> str:
    """The patient's answer right after the doctor turn at ``place``, or "".

    The next patient turn counts as an answer when it starts with an answer
    word (_ANSWER): "Oui, j'ai l'impression.", "Non, rien à l'arrière.",
    "Surtout oui." A patient turn that starts another subject is not one.
    """
    from phenoscribe.review import fold

    for i, (speaker, s, e) in enumerate(turns):
        if speaker != "doctor" or not (s <= place[0] < e):
            continue
        for next_speaker, ns, ne in turns[i + 1:]:
            if next_speaker == "patient":
                answer = text[ns:ne].strip()
                return answer if _ANSWER.match(fold(answer).strip().lower()) else ""
            if next_speaker == "doctor":
                break
    return ""
