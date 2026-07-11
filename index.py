"""Interactive class calendar with timetable import, commitments, and Groq chat CRUD."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import uuid4

import streamlit as st
from streamlit_calendar import calendar

from chatbot import interpret_calendar_request
from timetable_parser import extract_classes_from_pdf


DAY_NUMBERS = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
CLASS_COLOR = "#2563EB"
ACTIVITY_COLOR = "#7C3AED"
STUDY_COLOR = "#059669"

st.set_page_config(page_title="Class Concierge", page_icon="📚", layout="wide")


def as_iso(day: date, value: str) -> str:
    hour, minute = (int(part) for part in value.split(":"))
    return datetime.combine(day, time(hour, minute)).isoformat(timespec="minutes")


def seed_events() -> list[dict]:
    monday = date.today() - timedelta(days=date.today().weekday())
    return [
        {"id": "sample-bio", "title": "Biology 101 - Lab", "start": as_iso(monday, "10:00"), "end": as_iso(monday, "11:30"), "description": "Fictional sample class", "color": CLASS_COLOR, "kind": "class"},
        {"id": "sample-lit", "title": "World Literature", "start": as_iso(monday + timedelta(days=1), "13:00"), "end": as_iso(monday + timedelta(days=1), "14:00"), "description": "Fictional sample class", "color": CLASS_COLOR, "kind": "class"},
    ]


def initialise() -> None:
    st.session_state.setdefault("events", seed_events())
    st.session_state.setdefault("messages", [{"role": "assistant", "content": "Hi! I can update events, add a weekly commitment, or add a prioritised study activity."}])
    st.session_state.setdefault("imported_classes", [])
    st.session_state.setdefault("week_one", date.today() - timedelta(days=date.today().weekday()))
    st.session_state.setdefault("recurring_commitments", [])
    # Keep planner data separate from the `activities` text-area widget key.
    st.session_state.setdefault("planner_activities", [])
    st.session_state.setdefault("term_weeks", 10)
    st.session_state.setdefault("selected_event_id", None)


# Only send events near the visible date to the calendar widget itself.
# FullCalendar only ever displays one week/month at a time, so shipping an
# entire multi-week term plan (100-300+ events) into the component's props
# in one shot is unnecessary — and some streamlit_calendar builds render
# blank (or a collapsed near-zero-width iframe) with no console error when
# the payload/event count gets large, especially right after a full remount.
# NOTE: an earlier version of this window was 45+120=165 days, which for a
# 10-20 week term (70-140 days) covered almost the entire schedule anyway
# and didn't actually reduce the payload. Kept tight here on purpose, plus a
# hard cap as a backstop regardless of how dense a given window is.
CALENDAR_WINDOW_PAST_DAYS = 3
CALENDAR_WINDOW_FUTURE_DAYS = 10
CALENDAR_MAX_EVENTS = 80


def event_by_id(event_id: str) -> dict | None:
    return next((event for event in st.session_state.events if event["id"] == event_id), None)


def _valid_event_datetime(value: object) -> bool:
    """Reject anything that isn't a full ISO datetime (date+time).

    streamlit_calendar's JS component fails silently (blank, no Python
    traceback) if it receives a malformed date like "2026-07-11" with no
    time component. Catching that here, before it reaches session state,
    stops one bad chat-generated operation from breaking the whole calendar.
    """
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        datetime.fromisoformat(value)
        return True
    except ValueError:
        return False


def calendar_datetime(value: str) -> str:
    """Store calendar datetimes as local wall-clock values, without an offset.

    FullCalendar converts ISO values with an offset into the browser timezone.
    The timetable plan deliberately uses local, offset-free values, so chat
    operations must follow the same convention to avoid a shifted event.
    """
    return datetime.fromisoformat(value).replace(tzinfo=None).isoformat(timespec="minutes")


def apply_operations(operations: list[dict]) -> int:
    changes = 0
    planner_changed = False
    for operation in operations:
        action, event_id = operation.get("action"), operation.get("event_id")
        if action == "create_recurring_commitment":
            title, day, start, end = operation.get("title"), str(operation.get("day", "")).upper(), operation.get("start"), operation.get("end")
            try:
                valid_times = all(datetime.strptime(value, "%H:%M") for value in (start, end))
            except (TypeError, ValueError):
                valid_times = False
            if isinstance(title, str) and title.strip() and day in DAY_NUMBERS and valid_times and start < end:
                st.session_state.recurring_commitments.append({"title": title.strip(), "day": day, "start": start, "end": end})
                planner_changed = True
                changes += 1
        elif action == "create_activity":
            title = operation.get("title")
            try:
                hours = min(20.0, max(0.5, float(operation.get("hours"))))
                priority = min(5, max(1, int(operation.get("priority"))))
            except (TypeError, ValueError):
                continue
            if isinstance(title, str) and title.strip():
                st.session_state.planner_activities.append({"title": title.strip(), "hours": hours, "priority": priority})
                planner_changed = True
                changes += 1
        elif action == "create" and all(operation.get(key) for key in ("title", "start", "end")):
            if not (_valid_event_datetime(operation.get("start")) and _valid_event_datetime(operation.get("end"))):
                continue
            event = {key: value for key, value in operation.items() if value not in (None, "")}
            event["start"] = calendar_datetime(operation["start"])
            event["end"] = calendar_datetime(operation["end"])
            event.update({"id": str(uuid4()), "color": ACTIVITY_COLOR, "kind": "commitment"})
            st.session_state.events.append(event)
            changes += 1
        elif action == "update" and event_id and (event := event_by_id(event_id)):
            for key in ("start", "end"):
                if key in operation and not _valid_event_datetime(operation[key]):
                    operation.pop(key)
                elif key in operation:
                    operation[key] = calendar_datetime(operation[key])
            event.update({key: value for key, value in operation.items() if key not in {"action", "event_id"} and value not in (None, "")})
            changes += 1
        elif action == "delete" and event_id:
            before = len(st.session_state.events)
            st.session_state.events = [event for event in st.session_state.events if event["id"] != event_id]
            changes += int(len(st.session_state.events) != before)
    if planner_changed and st.session_state.imported_classes:
        st.session_state.events = build_term_plan(
            st.session_state.imported_classes,
            st.session_state.recurring_commitments,
            st.session_state.planner_activities,
            st.session_state.week_one,
            st.session_state.term_weeks,
        )
    return changes


def free_slots(day: date, occupied: list[dict], duration_minutes: int) -> tuple[str, str] | None:
    intervals = []
    for event in occupied:
        start, end = datetime.fromisoformat(event["start"]), datetime.fromisoformat(event["end"])
        if start.date() == day:
            intervals.append((start, end))
    intervals.sort()
    cursor = datetime.combine(day, time(8, 0))
    latest = datetime.combine(day, time(21, 0))
    for start, end in intervals + [(latest, latest)]:
        if start - cursor >= timedelta(minutes=duration_minutes):
            return cursor.isoformat(timespec="minutes"), (cursor + timedelta(minutes=duration_minutes)).isoformat(timespec="minutes")
        cursor = max(cursor, end)
    return None


def build_term_plan(classes: list[dict], commitments: list[dict], activities: list[dict], week_one: date, term_weeks: int) -> list[dict]:
    """Expand alternating classes then place ranked recurring commitments and study."""
    plan: list[dict] = []
    for week_index in range(term_weeks):
        week_kind = "odd" if week_index % 2 == 0 else "even"
        monday = week_one + timedelta(weeks=week_index)
        for lesson in classes:
            if lesson["week"] == week_kind:
                day = monday + timedelta(days=DAY_NUMBERS[lesson["day"]])
                plan.append({"id": f"class-{week_index}-{lesson['day']}-{lesson['start']}-{lesson['title']}", "title": lesson["title"], "start": as_iso(day, lesson["start"]), "end": as_iso(day, lesson["end"]), "color": CLASS_COLOR, "kind": "class", "description": f"{week_kind.title()} week class"})

        for item in commitments:
            day = monday + timedelta(days=DAY_NUMBERS[item["day"]])
            plan.append({
                "id": f"commitment-{week_index}-{item['day']}-{item['start']}-{item['title']}",
                "title": item["title"],
                "start": as_iso(day, item["start"]),
                "end": as_iso(day, item["end"]),
                "color": ACTIVITY_COLOR,
                "kind": "commitment",
                "description": "Recurring commitment",
            })

        for item in sorted(activities, key=lambda item: int(item["priority"])):
            sessions = max(1, round(float(item["hours"])) )
            label, color, kind = f"Study: {item['title']}", STUDY_COLOR, "study"
            for _ in range(sessions):
                for offset in range(7):
                    day = monday + timedelta(days=offset)
                    slot = free_slots(day, plan, 60)
                    if slot:
                        plan.append({"id": str(uuid4()), "title": label, "start": slot[0], "end": slot[1], "color": color, "kind": kind, "description": f"Priority {item['priority']}"})
                        break
    return plan


def handle_calendar_change(payload: dict | None) -> None:
    if not payload:
        return
    callback = payload.get("callback")
    raw_event = payload.get(callback, {}).get("event", {})
    if callback == "eventClick" and isinstance(raw_event, dict):
        st.session_state.selected_event_id = str(raw_event.get("id", "")) or None
        return
    if callback not in {"eventDrop", "eventResize", "eventChange"}:
        return
    event = event_by_id(str(raw_event.get("id", ""))) if isinstance(raw_event, dict) else None
    if event and raw_event.get("start"):
        event["start"], event["end"] = raw_event["start"], raw_event.get("end") or event["end"]


def recurring_commitments_table() -> list[dict]:
    """Collect fixed weekly commitments as weekday and time ranges."""
    raw = st.text_area(
        "One item per line: name | weekday | start | end",
        value="CCA / training | MON | 15:00 | 17:00",
        key="commitments",
        height=100,
        help="Example: Basketball training | WED | 15:30 | 17:00. These are placed at the stated time every week.",
    )
    rows: list[dict] = []
    for line in raw.splitlines():
        parts = [part.strip() for part in line.split("|")]
        if not parts or not parts[0] or len(parts) != 4:
            if line.strip():
                st.warning(f"Skipped invalid commitment: {line}")
            continue
        try:
            start, end = parts[2], parts[3]
            start_time, end_time = (datetime.strptime(value, "%H:%M").time() for value in (start, end))
            if parts[1].upper() not in DAY_NUMBERS or start_time >= end_time:
                raise ValueError
        except ValueError:
            st.warning(f"Skipped invalid commitment: {line}")
            continue
        rows.append({"title": parts[0], "day": parts[1].upper(), "start": start, "end": end})
    return rows


def activities_table() -> list[dict]:
    """Collect study priorities without DataFrame/PyArrow-backed Streamlit widgets."""
    raw = st.text_area(
        "One activity per line: name | study hours each week | priority (1 = highest)",
        value="Math revision | 2 | 1",
        key="activities",
        height=100,
        help="Example: Chemistry revision | 3 | 1",
    )
    rows: list[dict] = []
    for line in raw.splitlines():
        parts = [part.strip() for part in line.split("|")]
        if not parts or not parts[0]:
            continue
        try:
            hours = min(20.0, max(0.5, float(parts[1]))) if len(parts) > 1 else 1.0
            priority = min(5, max(1, int(parts[2]))) if len(parts) > 2 else 3
        except ValueError:
            st.warning(f"Skipped invalid activity: {line}")
            continue
        rows.append({"title": parts[0], "hours": hours, "priority": priority})
    return rows


def defer_todays_study_blocks() -> tuple[int, int]:
    """Move today's study/activity blocks to upcoming free time, preserving commitments."""
    today = date.today()
    targets = sorted(
        (event for event in st.session_state.events
         if event.get("kind") in {"study", "activity"} and datetime.fromisoformat(event["start"]).date() == today),
        key=lambda event: event["start"],
    )
    occupied = [event for event in st.session_state.events if event not in targets]
    moved = 0
    for event in targets:
        start, end = datetime.fromisoformat(event["start"]), datetime.fromisoformat(event["end"])
        duration = max(1, round((end - start).total_seconds() / 60))
        for offset in range(1, 15):
            slot = free_slots(today + timedelta(days=offset), occupied, duration)
            if slot:
                event["start"], event["end"] = slot
                occupied.append(event)
                moved += 1
                break
    return moved, len(targets) - moved


def spread_study_blocks() -> tuple[int, int]:
    """Redistribute study/activity blocks over all seven days of each term week."""
    targets = [event for event in st.session_state.events if event.get("kind") in {"study", "activity"}]
    occupied = [event for event in st.session_state.events if event not in targets]
    targets.sort(key=lambda event: event["start"])
    moved = 0
    unscheduled = 0
    for index, event in enumerate(targets):
        start, end = datetime.fromisoformat(event["start"]), datetime.fromisoformat(event["end"])
        duration = max(1, round((end - start).total_seconds() / 60))
        monday = start.date() - timedelta(days=start.weekday())
        # Rotate the first choice for every block so a multi-week term reaches
        # weekends too, instead of always filling Monday first.
        first_day = (index + (monday.toordinal() // 7)) % 7
        for step in range(7):
            day = monday + timedelta(days=(first_day + step) % 7)
            slot = free_slots(day, occupied, duration)
            if slot:
                event["start"], event["end"] = slot
                occupied.append(event)
                moved += 1
                break
        else:
            occupied.append(event)
            unscheduled += 1
    return moved, unscheduled


def is_today_unavailable_request(message: str) -> bool:
    text = message.casefold()
    return "tired today" in text or "don't have time today" in text or "do not have time today" in text


def is_spread_study_request(message: str) -> bool:
    text = message.casefold()
    return "spread" in text and ("activity" in text or "activities" in text or "study" in text)


initialise()
st.title("📚 Class Concierge")
st.caption("Import an alternating-week timetable, add commitments, and build a realistic term plan.")
page = st.radio("Page", ["Schedule", "Set Commitments"], horizontal=True, label_visibility="collapsed")

if page == "Set Commitments":
    st.subheader("Set Commitments")
    st.write("Import a timetable, tell us when Week 1 starts, then rank what matters. The generated plan fits activities and study into free time.")
    timetable = st.file_uploader("Upload your timetable PDF", type="pdf", help="Use a searchable timetable PDF with separate odd and even week sections. FT Time and HBL Day are included.")
    week_one = st.date_input("Week 1 begins", value=st.session_state.week_one, help="Set this to the first Monday of Week 1.")
    term_weeks = st.number_input("Term length (weeks)", min_value=1, max_value=20, value=10)
    st.markdown("**Recurring commitments**")
    commitments = recurring_commitments_table()
    st.markdown("**Subjects or activities you want to prioritise**")
    activities = activities_table()
    if st.button("Submit commitments and generate schedule", type="primary", width="stretch"):
        try:
            if timetable:
                with st.spinner("Transcribing lesson names and timings from the timetable…"):
                    st.session_state.imported_classes = extract_classes_from_pdf(timetable.getvalue())
            if not st.session_state.imported_classes:
                raise ValueError("Upload a timetable PDF before generating your plan.")
            st.session_state.week_one = week_one
            st.session_state.recurring_commitments = commitments
            st.session_state.planner_activities = activities
            st.session_state.term_weeks = int(term_weeks)
            st.session_state.events = build_term_plan(
                st.session_state.imported_classes,
                st.session_state.recurring_commitments,
                st.session_state.planner_activities,
                week_one,
                st.session_state.term_weeks,
            )
            st.success(f"Generated {len(st.session_state.events)} class, commitment, and study events. Open Schedule to review and edit them.")
        except Exception as error:
            st.error(f"Couldn't generate the schedule: {error}")
    if st.session_state.imported_classes:
        with st.expander("Review imported classes"):
            st.code("\n".join(
                f"{item['week'].title()} | {item['day']} | {item['start']}-{item['end']} | {item['title']}"
                for item in st.session_state.imported_classes
            ), language=None)

if page == "Schedule":
    left, right = st.columns([2.15, 1], gap="large")
    with left:
        # Defensive: drop any event with a malformed start/end (e.g. left over
        # from a bad chat operation before validation was added) so a single
        # corrupted event can't silently blank out the entire calendar.
        valid_events = [
            event for event in st.session_state.events
            if _valid_event_datetime(event.get("start")) and _valid_event_datetime(event.get("end"))
        ]
        if len(valid_events) != len(st.session_state.events):
            st.warning(f"Hid {len(st.session_state.events) - len(valid_events)} event(s) with an invalid date — you may want to delete and re-add them.")
        initial_date = min(
            (event["start"][:10] for event in valid_events),
            default=date.today().isoformat(),
        )
        # Cap what's actually shipped to the widget to a window around the
        # visible date (see CALENDAR_WINDOW_* above). Use prev/next/today in
        # the toolbar to browse outside this window — the full term is still
        # in st.session_state.events for the manual editor and chat below.
        window_start = datetime.fromisoformat(initial_date) - timedelta(days=CALENDAR_WINDOW_PAST_DAYS)
        window_end = datetime.fromisoformat(initial_date) + timedelta(days=CALENDAR_WINDOW_FUTURE_DAYS)

        def _naive(value: datetime) -> datetime:
            # Chat-created events may carry a timezone offset (the chatbot
            # prompt asks for one "when a time is known"), while generated
            # term-plan events are naive. Comparing the two directly raises
            # "can't compare offset-naive and offset-aware datetimes", so
            # strip any offset before comparing.
            return value.replace(tzinfo=None) if value.tzinfo else value

        renderable_events = [
            event for event in valid_events
            if window_start <= _naive(datetime.fromisoformat(event["start"])) <= window_end
        ]
        # Hard cap as a backstop: even a tight window can still be dense
        # (e.g. many commitments/study sessions packed into a single week).
        # Keep the events closest to initial_date if still over the cap.
        if len(renderable_events) > CALENDAR_MAX_EVENTS:
            anchor = datetime.fromisoformat(initial_date)
            renderable_events.sort(
                key=lambda event: abs((_naive(datetime.fromisoformat(event["start"])) - anchor).total_seconds())
            )
            renderable_events = renderable_events[:CALENDAR_MAX_EVENTS]
            renderable_events.sort(key=lambda event: event["start"])
        if len(renderable_events) != len(valid_events):
            st.caption(f"Showing {len(renderable_events)} of {len(valid_events)} events near this date range. Use prev/next to browse further out.")
        result = calendar(
            events=renderable_events,
            options={"initialView": "timeGridWeek", "initialDate": initial_date, "headerToolbar": {"left": "prev,next today", "center": "title", "right": "dayGridMonth,timeGridWeek,timeGridDay"}, "editable": True, "eventDurationEditable": True, "height": 700},
            callbacks=["eventClick", "eventDrop", "eventResize", "eventChange"],
            # Keep this key stable. Regenerating while the Schedule tab is
            # hidden must update the component's events, not remount
            # FullCalendar in a zero-width tab container.
            key="class-calendar",
        )
        handle_calendar_change(result)
        st.caption("Blue = class · purple = recurring commitment · green = study. Drag or resize any event to make manual changes.")
        if selected_event := event_by_id(st.session_state.selected_event_id or ""):
            start = datetime.fromisoformat(selected_event["start"])
            end = datetime.fromisoformat(selected_event["end"])
            with st.expander(f"Event details: {selected_event['title']}", expanded=True):
                st.write(f"**When:** {start.strftime('%A, %d %b %Y · %H:%M')}–{end.strftime('%H:%M')}")
                st.write(f"**Type:** {selected_event.get('kind', 'event').replace('_', ' ').title()}")
                if description := selected_event.get("description"):
                    st.write(f"**Notes:** {description}")
                st.divider()
                st.caption("Update or delete this event")
                with st.form(f"event-details-{selected_event['id']}"):
                    title = st.text_input("Title", value=selected_event["title"])
                    event_date = st.date_input("Date", value=start.date())
                    start_time = st.time_input("Starts", value=start.time())
                    end_time = st.time_input("Ends", value=end.time())
                    description = st.text_area("Notes", value=selected_event.get("description", ""))
                    save, delete = st.columns(2)
                    save_clicked = save.form_submit_button("Update event", type="primary")
                    delete_clicked = delete.form_submit_button("Delete event")
                if save_clicked:
                    updated_start = datetime.combine(event_date, start_time).isoformat(timespec="minutes")
                    updated_end = datetime.combine(event_date, end_time).isoformat(timespec="minutes")
                    if not title.strip() or updated_end <= updated_start:
                        st.error("Provide a title and an end time after the start time.")
                    else:
                        selected_event.update({"title": title.strip(), "start": updated_start, "end": updated_end, "description": description})
                        st.rerun()
                if delete_clicked:
                    st.session_state.events = [event for event in st.session_state.events if event["id"] != selected_event["id"]]
                    st.session_state.selected_event_id = None
                    st.rerun()
    with right:
        st.subheader("Create an event")
        default_start = datetime.now().replace(minute=0, second=0, microsecond=0)
        default_end = default_start + timedelta(hours=1)
        with st.form("manual-create", clear_on_submit=True):
            title = st.text_input("Title")
            event_date = st.date_input("Date", value=default_start.date())
            start_time = st.time_input("Starts", value=default_start.time())
            end_time = st.time_input("Ends", value=default_end.time())
            description = st.text_area("Notes")
            save_clicked = st.form_submit_button("Create event", type="primary")
        if save_clicked:
            start, end = datetime.combine(event_date, start_time).isoformat(timespec="minutes"), datetime.combine(event_date, end_time).isoformat(timespec="minutes")
            if not title.strip() or end <= start:
                st.error("Provide a title and an end time after the start time.")
            else:
                st.session_state.events.append({"id": str(uuid4()), "title": title.strip(), "start": start, "end": end, "description": description, "color": ACTIVITY_COLOR, "kind": "commitment"})
                st.rerun()

    st.divider()
    st.subheader("Chat with your calendar")
    st.caption("Try: ‘Add basketball every Saturday from 2 pm to 4 pm’ or ‘Add chemistry revision for 3 hours a week, priority 1.’")
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.write(message["content"])
    if prompt := st.chat_input("e.g. Add basketball every Saturday from 2 pm to 4 pm"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Updating your calendar…"):
                try:
                    if is_spread_study_request(prompt):
                        moved, unscheduled = spread_study_blocks()
                        if moved:
                            answer = f"Spread {moved} study/activity block{'s' if moved != 1 else ''} across available days, including weekends when free."
                        else:
                            answer = "You have no study or activity blocks to spread."
                        if unscheduled:
                            answer += f" {unscheduled} block{'s' if unscheduled != 1 else ''} could not be moved because no free slot was available that week."
                        answer += " Your classes and recurring commitments were left unchanged."
                    elif is_today_unavailable_request(prompt):
                        moved, remaining = defer_todays_study_blocks()
                        if moved == 0 and remaining == 0:
                            answer = "You have no study or activity blocks scheduled today, so there was nothing to move."
                        else:
                            answer = f"Moved {moved} study/activity block{'s' if moved != 1 else ''} from today to later free slots."
                        if remaining:
                            answer += f" {remaining} block{'s' if remaining != 1 else ''} could not be rescheduled in the next two weeks."
                        answer += " Your classes and recurring commitments were left unchanged."
                    else:
                        response = interpret_calendar_request(prompt, st.session_state.events)
                        count = apply_operations(response["operations"])
                        answer = response["reply"] + (f" ({count} calendar change{'s' if count != 1 else ''} applied.)" if count else "")
                except Exception as error:
                    answer = f"I couldn't update the calendar: {error}"
            st.write(answer)
        st.session_state.messages.append({"role": "assistant", "content": answer})
        st.rerun()
