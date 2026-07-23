"""Transcribe alternating-week timetable PDFs into reusable class records."""

from __future__ import annotations

import io
import logging
import os
import re
from typing import Any

from groq import Groq
from pypdf import PdfReader


EXCLUDED_LABELS = {"BREAK", "LUNCH", "MORNING ASSEMBLY", "ASSEMBLY"}
# A school timetable normally needs only a few hundred output tokens. Keeping this
# comfortably below Groq's common 8K TPM starter limit also leaves room for the
# extracted PDF text in the same request.
DEFAULT_MAX_COMPLETION_TOKENS = 1_500
logger = logging.getLogger(__name__)
LINE_PATTERN = re.compile(
    r"^\s*(ODD|EVEN)\s*\|\s*(MON|TUE|WED|THU|FRI)\s*\|\s*"
    r"(\d{1,2}:\d{2})\s*\|\s*(\d{1,2}:\d{2})\s*\|\s*(.+?)\s*$",
    re.IGNORECASE,
)


def _key() -> str | None:
    try:
        import streamlit as st

        return st.secrets.get("GROQ_API_KEY") or os.getenv("GROQ_API_KEY")
    except Exception:
        return os.getenv("GROQ_API_KEY")


def _read_pdf(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join(page.extract_text(extraction_mode="layout") or "" for page in reader.pages)
    if not text.strip():
        raise ValueError("This PDF has no readable text. Use a text-based timetable PDF, or add OCR first.")
    return text


def extract_classes_from_pdf(pdf_bytes: bytes) -> list[dict[str, Any]]:
    """Return week/day/title/start/end records from Groq's plain-text transcript."""
    api_key = _key()
    if not api_key:
        raise RuntimeError("Add GROQ_API_KEY to .streamlit/secrets.toml before importing a timetable.")

    prompt = f"""Read this school timetable. Transcribe every teachable lesson in both Odd Week and Even Week.

Output one line per lesson, with no heading, Markdown, explanation, teacher name, room, or venue:
WEEK|DAY|START|END|LESSON NAME

Use only ODD or EVEN for WEEK; MON, TUE, WED, THU, or FRI for DAY; and 24-hour HH:MM times.
Include FT TIME and HBL DAY as lessons. For HBL DAY, use the first and final timetable times shown that day.
Exclude BREAK, LUNCH, MORNING ASSEMBLY, and ASSEMBLY.
Do not merge lessons that are separated by another timetable item.

TIMETABLE:
{_read_pdf(pdf_bytes)}"""

    client = Groq(api_key=api_key)
    transcript = _request_transcript(client, prompt)
    classes = _parse_transcript(transcript)
    if not classes:
        raise ValueError(
            "The timetable reader returned text but no usable lesson lines. "
            "Please try again; the app will only accept ODD|MON|08:00|09:00|Lesson-style lines."
        )
    return classes


def _request_transcript(client: Groq, prompt: str) -> str:
    """Use ordinary text completion, with a second model as an automatic fallback."""
    models = [
        os.getenv("TIMETABLE_MODEL", "openai/gpt-oss-120b"),
        os.getenv("TIMETABLE_FALLBACK_MODEL", "llama-3.3-70b-versatile"),
    ]
    failures: list[str] = []
    for model in dict.fromkeys(models):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Transcribe timetable lessons exactly in the requested pipe-delimited format."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_completion_tokens=int(
                    os.getenv("TIMETABLE_MAX_COMPLETION_TOKENS", DEFAULT_MAX_COMPLETION_TOKENS)
                ),
            )
            content = response.choices[0].message.content or ""
            if _parse_transcript(content):
                return content
            failures.append(f"{model}: response contained no valid lesson lines")
        except Exception as error:
            # Preserve the useful Groq status/message in the server logs without
            # ever logging the API key or the uploaded timetable contents.
            logger.warning("Timetable transcription failed for %s: %s", model, error)
            failures.append(f"{model}: {type(error).__name__}: {error}")

    detail = "; ".join(failures) or "No model response was received."
    raise ValueError(
        "Groq could not transcribe the timetable. "
        f"Details: {detail}"
    )


def _parse_transcript(transcript: str) -> list[dict[str, Any]]:
    """Read the deliberately simple transcript format without model JSON parsing."""
    classes: list[dict[str, Any]] = []
    for line in transcript.splitlines():
        line = line.strip().strip("`")
        match = LINE_PATTERN.match(line)
        if not match:
            continue
        week, day, start, end, title = match.groups()
        title = re.sub(r"\s+", " ", title).strip(" -")
        if title.upper() in EXCLUDED_LABELS or not _valid_time(start) or not _valid_time(end) or _minutes(start) >= _minutes(end):
            continue
        classes.append({"week": week.lower(), "day": day.upper(), "title": title, "start": _normalise_time(start), "end": _normalise_time(end)})
    return classes


def _valid_time(value: str) -> bool:
    try:
        hour, minute = (int(piece) for piece in value.split(":"))
        return 0 <= hour <= 23 and 0 <= minute <= 59
    except (ValueError, AttributeError):
        return False


def _minutes(value: str) -> int:
    hour, minute = (int(piece) for piece in value.split(":"))
    return hour * 60 + minute


def _normalise_time(value: str) -> str:
    hour, minute = (int(piece) for piece in value.split(":"))
    return f"{hour:02d}:{minute:02d}"
