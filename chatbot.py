# # """Groq-backed natural language commands for the calendar.
# """
# The app owns persistence and applies the operations returned by this module.
# Keeping that boundary here makes it easy to replace Groq or add a database later.
# """

# from __future__ import annotations

# import json
# import os
# from datetime import datetime, timedelta
# from typing import Any

# from groq import Groq

# # How far around "now" we show the model by default. Keeps the prompt small
# # even when the full term plan has 100+ events, since token usage would
# # otherwise scale with the whole schedule and blow past Groq's TPM limit.
# WINDOW_PAST_DAYS = 7
# WINDOW_FUTURE_DAYS = 60


# def _api_key() -> str | None:
#     """Read a Groq key from Streamlit secrets when available, then the environment."""
#     try:
#         import streamlit as st

#         return st.secrets.get("GROQ_API_KEY") or os.getenv("GROQ_API_KEY")
#     except Exception:
#         return os.getenv("GROQ_API_KEY")


# def _windowed_events(events: list[dict[str, Any]], now: datetime) -> tuple[list[dict[str, Any]], int]:
#     """Return events near `now`, plus how many were excluded, to keep prompts small."""
#     window_start = now - timedelta(days=WINDOW_PAST_DAYS)
#     window_end = now + timedelta(days=WINDOW_FUTURE_DAYS)
#     in_window = []
#     excluded = 0
#     for event in events:
#         try:
#             start = datetime.fromisoformat(event["start"])
#         except (KeyError, ValueError):
#             continue
#         if start.tzinfo is None:
#             start = start.replace(tzinfo=now.tzinfo)
#         if window_start <= start <= window_end:
#             in_window.append(event)
#         else:
#             excluded += 1
#     return in_window, excluded


# def _compact(events: list[dict[str, Any]]) -> tuple[str, dict[int, str]]:
#     """Serialize events with a short numeric ref instead of the long generated id.

#     This is the main token saver: generated ids like
#     "class-2-MON-10:00-Biology 101 - Lab" repeat the title and time, and
#     color/kind/description add nothing the model needs to route a request.
#     """
#     ref_to_id: dict[int, str] = {}
#     slim = []
#     for ref, event in enumerate(events):
#         ref_to_id[ref] = event["id"]
#         slim.append({"ref": ref, "title": event.get("title", ""), "start": event.get("start"), "end": event.get("end")})
#     return json.dumps(slim, ensure_ascii=False), ref_to_id


# def interpret_calendar_request(message: str, events: list[dict[str, Any]]) -> dict[str, Any]:
#     """Ask Groq for safe, structured calendar operations.

#     ISO 8601 timestamps are returned so FullCalendar can consume them directly.
#     """
#     key = _api_key()
#     if not key:
#         raise RuntimeError(
#             "No Groq key found. Add GROQ_API_KEY to .streamlit/secrets.toml or your environment."
#         )

#     now_dt = datetime.now().astimezone()
#     now = now_dt.isoformat(timespec="minutes")

#     visible_events, excluded_count = _windowed_events(events, now_dt)
#     context_events, ref_to_id = _compact(visible_events)
#     window_note = (
#         f"\n({excluded_count} older/further-out events are hidden from this list. "
#         "If the request seems to target one of those, return action=none and ask the user "
#         "for an approximate date so it can be brought into view.)"
#         if excluded_count
#         else ""
#     )

#     prompt = f"""You are a helpful school-calendar assistant. Today is {now}.
# Convert the user's request into calendar operations. The existing events near this date are:
# {context_events}{window_note}

# Rules:
# - Return only a valid JSON object with exactly two keys: reply and operations.
# - operations is an array; each item has action (create, update, delete, or none) plus applicable event fields.
# - Dates and times must be complete ISO 8601 strings with timezone offsets when a time is known.
# - For a new event, use action=create and leave ref blank.
# - For edits/cancellations, match an existing event and use its exact ref (the small integer shown above), not its title.
# - Do not invent dates or times. If essential details are missing, return action=none and ask a short question in reply.
# - Preserve existing fields on updates when the user does not change them; include all known fields in the update.
# - A delete operation only needs action and ref.
# - The reply should briefly state what you changed (or what clarification you need).
# User request: {message}"""

#     client = Groq(api_key=key)
#     response = client.chat.completions.create(
#         model=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
#         messages=[
#             {"role": "system", "content": "You convert calendar requests into JSON operations."},
#             {"role": "user", "content": prompt},
#         ],
#         response_format={"type": "json_object"},
#         temperature=0.1,
#     )
#     result = json.loads(response.choices[0].message.content or "{}")
#     result.setdefault("reply", "Done.")
#     result.setdefault("operations", [])
#     result["operations"] = _resolve_refs(result["operations"], ref_to_id)
#     return result


# def _resolve_refs(operations: list[dict[str, Any]], ref_to_id: dict[int, str]) -> list[dict[str, Any]]:
#     """Translate the model's small integer refs back into real event ids.

#     Keeps `app.py`'s apply_operations() unchanged, since it still expects
#     event_id on update/delete operations.
#     """
#     resolved = []
#     for operation in operations:
#         operation = dict(operation)
#         ref = operation.pop("ref", None)
#         if operation.get("action") in {"update", "delete"}:
#             try:
#                 event_id = ref_to_id.get(int(ref))
#             except (TypeError, ValueError):
#                 event_id = None
#             if event_id is None:
#                 # Can't safely apply an edit/delete without a real id.
#                 continue
#             operation["event_id"] = event_id
#         resolved.append(operation)
#     return resolved

"""Groq-backed natural language commands for the calendar.

The app owns persistence and applies the operations returned by this module.
Keeping that boundary here makes it easy to replace Groq or add a database later.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any

from groq import Groq

# How far around "now" we show the model by default, plus a hard cap on how
# many events go in the prompt. Keeps the prompt small even when the full
# term plan has 100+ events, since token usage would otherwise scale with the
# whole schedule and blow past Groq's TPM limit. Tune via env if needed.
WINDOW_PAST_DAYS = int(os.getenv("CHATBOT_WINDOW_PAST_DAYS", "3"))
WINDOW_FUTURE_DAYS = int(os.getenv("CHATBOT_WINDOW_FUTURE_DAYS", "14"))
MAX_EVENTS = int(os.getenv("CHATBOT_MAX_EVENTS", "40"))


def _api_key() -> str | None:
    """Read a Groq key from Streamlit secrets when available, then the environment."""
    try:
        import streamlit as st

        return st.secrets.get("GROQ_API_KEY") or os.getenv("GROQ_API_KEY")
    except Exception:
        return os.getenv("GROQ_API_KEY")


def _windowed_events(events: list[dict[str, Any]], now: datetime) -> tuple[list[dict[str, Any]], int]:
    """Return events near `now` (capped at MAX_EVENTS, closest first), plus how many were excluded.

    Windowing alone wasn't enough: a dense generated term plan can still pack
    dozens of events into even a two-week window. The hard cap guarantees an
    upper bound on prompt size regardless of how busy the schedule is.
    """
    window_start = now - timedelta(days=WINDOW_PAST_DAYS)
    window_end = now + timedelta(days=WINDOW_FUTURE_DAYS)
    dated: list[tuple[datetime, dict[str, Any]]] = []
    skipped = 0
    for event in events:
        try:
            start = datetime.fromisoformat(event["start"])
        except (KeyError, ValueError):
            skipped += 1
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=now.tzinfo)
        if window_start <= start <= window_end:
            dated.append((start, event))
        else:
            skipped += 1
    dated.sort(key=lambda pair: abs((pair[0] - now).total_seconds()))
    kept = [event for _, event in dated[:MAX_EVENTS]]
    excluded = skipped + max(0, len(dated) - MAX_EVENTS)
    # Re-sort the kept events chronologically so they read naturally in the prompt.
    kept.sort(key=lambda event: event["start"])
    return kept, excluded


def _compact(events: list[dict[str, Any]]) -> tuple[str, dict[int, str]]:
    """Serialize events as short pipe-delimited lines instead of JSON.

    This is the main token saver: JSON repeats the keys ("ref", "title",
    "start", "end") on every single event, and generated ids like
    "class-2-MON-10:00-Biology 101 - Lab" repeat the title and time again.
    A numeric ref plus plain "ref|title|start|end" lines carry the same
    information at a fraction of the tokens.
    """
    ref_to_id: dict[int, str] = {}
    lines = []
    for ref, event in enumerate(events):
        ref_to_id[ref] = event["id"]
        title = (event.get("title") or "").replace("|", "/")
        lines.append(f"{ref}|{title}|{event.get('start')}|{event.get('end')}")
    return "\n".join(lines), ref_to_id


def interpret_calendar_request(message: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Ask Groq for safe, structured calendar operations.

    ISO 8601 timestamps are returned so FullCalendar can consume them directly.
    """
    key = _api_key()
    if not key:
        raise RuntimeError(
            "No Groq key found. Add GROQ_API_KEY to .streamlit/secrets.toml or your environment."
        )

    now_dt = datetime.now().astimezone()
    now = now_dt.isoformat(timespec="minutes")

    visible_events, excluded_count = _windowed_events(events, now_dt)
    context_events, ref_to_id = _compact(visible_events)
    window_note = (
        f"\n({excluded_count} older/further-out events are hidden from this list. "
        "If the request seems to target one of those, return action=none and ask the user "
        "for an approximate date so it can be brought into view.)"
        if excluded_count
        else ""
    )

    prompt = f"""You are a helpful school-calendar assistant. Today is {now}.
Convert the user's request into calendar operations. The existing events near this date are listed
one per line as ref|title|start|end :
{context_events}{window_note}

Rules:
- Return only a valid JSON object with exactly two keys: reply and operations.
- operations is an array; each item has action (create, update, delete, create_recurring_commitment, create_activity, or none) plus applicable fields.
- Dates and times must be complete local ISO 8601 datetimes in the form YYYY-MM-DDTHH:MM. Do not include a timezone offset or Z suffix.
- For a new event, use action=create and leave ref blank.
- To add a weekly fixed commitment, use action=create_recurring_commitment with title, day (MON through SUN), start (HH:MM), and end (HH:MM). Ask a follow-up question if its weekday or times are missing.
- To add a prioritised study activity, use action=create_activity with title, hours (weekly study hours), and priority (1 = highest, 5 = lowest). Ask a follow-up question if hours or priority are missing.
- For edits/cancellations, match an existing event and use its exact ref (the small integer shown above), not its title.
- Do not invent dates or times. If essential details are missing, return action=none and ask a short question in reply.
- Preserve existing fields on updates when the user does not change them; include all known fields in the update.
- A delete operation only needs action and ref.
- The reply should briefly state what you changed (or what clarification you need).
User request: {message}"""

    client = Groq(api_key=key)
    response = client.chat.completions.create(
        model=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
        messages=[
            {"role": "system", "content": "You convert calendar requests into JSON operations."},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    result = json.loads(response.choices[0].message.content or "{}")
    result.setdefault("reply", "Done.")
    result.setdefault("operations", [])
    result["operations"] = _resolve_refs(result["operations"], ref_to_id)
    return result


def _resolve_refs(operations: list[dict[str, Any]], ref_to_id: dict[int, str]) -> list[dict[str, Any]]:
    """Translate the model's small integer refs back into real event ids.

    Keeps `app.py`'s apply_operations() unchanged, since it still expects
    event_id on update/delete operations.
    """
    resolved = []
    for operation in operations:
        operation = dict(operation)
        ref = operation.pop("ref", None)
        if operation.get("action") in {"update", "delete"}:
            try:
                event_id = ref_to_id.get(int(ref))
            except (TypeError, ValueError):
                event_id = None
            if event_id is None:
                # Can't safely apply an edit/delete without a real id.
                continue
            operation["event_id"] = event_id
        resolved.append(operation)
    return resolved
