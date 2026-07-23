# ai_adaptive_scheduler

Class Concierge is an AI-assisted academic scheduler that converts a student’s timetable, recurring commitments, and study priorities into an editable term calendar.

Instead of manually copying every lesson into a calendar, students can upload an alternating odd even-week timetable PDF. The application extracts lesson information, expands it across the selected term, protects fixed commitments, and places study sessions into available time slots.

## Demo Video and other necessary documents

https://drive.google.com/drive/folders/1I4-8AAzMUSuIn2OCxjEyN9k4WD3rmKi-?usp=sharing

## What the project does

The application supports the following workflow:

1. Upload a school timetable PDF
2. Use AI to extract lesson names, weekdays, start times, end times, and odd even-week patterns
3. Add recurring commitments such as CCA, training, tuition, or work
4. Add study activities with weekly hour targets and priority levels
5. Generate a multi-week schedule that places study blocks around lessons and commitments
6. Review the schedule in an interactive calendar
7. Create, update, or delete events manually
8. Converse with an AI chatbot to 

Example chat requests include:

- “Add figure skating every Tuesday from 4 pm to 6 pm.”
- “I’m tired today, move my study sessions.”
- “Spread my study blocks around the week.”

Classes, commitments, and study sessions are colour-coded so the generated plan can be understood quickly.

## Key features

- AI-assisted timetable PDF transcription
- Support for alternating odd and even school weeks
- Automatic generation of schedules for terms 
- Recurring fixed commitments
- Priority-based weekly study activities
- Natural-language calendar creation, updates, and deletion
- Automatic rescheduling of today’s study blocks
- Redistribution of study blocks across weekdays and weekends
- Interactive week, day, and month calendar views
- Manual event creation, editing, and deletion
- Validation of AI-generated dates, times, event references, and inputs
- Model fallback when timetable transcription fails

## Technology used

- Python
- Streamlit
- `streamlit-calendar` / FullCalendar
- Groq API
- GPT-OSS for timetable transcription and calendar commands
- Llama 3.3 70B as timetable-transcription fallback
- `pypdf` for extracting text from PDF documents

## AI tools and coding agents used

Two different forms of AI were used in this project:

### Runtime AI

The application uses models through the Groq API for two focused tasks:

- Timetable transcription: converts extracted PDF text into a strict `WEEK|DAY|START|END|LESSON` format.
- Calendar command interpretation: converts natural-language requests into structured create, update, delete, recurring-commitment, or study-activity operations.

The model does not directly modify application state. It returns structured operations, which are validated and applied by deterministic Python code.

### Coding agent

Codex was used as a coding and review agent during development. It assisted with:

- Breaking the application into timetable parsing, scheduling, calendar, and chat responsibilities
- Planning the data structures used for classes, commitments, study blocks, and calendar events
- Implementing and reviewing calendar CRUD behaviour
- Diagnosing blank calendar rendering and invalid datetime problems
- Reducing the amount of schedule data sent to the calendar component
- Reducing the number of events included in AI prompts
- Refactoring timetable parsing and AI-operation handling into separate modules
- Adding validation and defensive checks around model output
- Preparing project documentation and handoff material

The agent accelerated implementation, but its suggestions were treated as proposals rather than automatically trusted changes.

## How AI helped—and where it failed

AI was particularly useful for translating ambiguous timetable text and natural-language calendar requests into predictable records. It was also useful during debugging because it helped trace failures across Streamlit session state, FullCalendar event data, datetime formats, and model-generated operations.

However, early AI-assisted versions introduced or failed to anticipate several issues:

- Large term schedules could cause the embedded calendar to render blank.
- Date-only or malformed model output could silently break calendar rendering.
- Mixing timezone-aware and timezone-free timestamps caused comparison and display problems.
- Sending the entire term schedule to the model created unnecessarily large prompts.
- Windowing events by date alone was insufficient for unusually dense schedules.
- Generated identifiers and repeated JSON field names consumed avoidable prompt tokens.

These problems were corrected by:

- Validating every event before it reaches the calendar
- Normalising events to local, timezone-free ISO datetimes
- Limiting the calendar to a small visible event window with a hard event cap
- Limiting chatbot context by date and maximum event count
- Replacing verbose event JSON with compact pipe-delimited lines
- Mapping short numeric model references back to real event IDs
- Keeping application state changes in Python instead of allowing the model to apply them directly
- Falling back to deterministic code for common rescheduling requests

This experience demonstrated that AI output still requires validation, bounded context, and deterministic safeguards.

## Features added and bugs fixed

### Features added

- Odd/even-week timetable PDF import
- Multiweek term plan generation
- Recurrence for fixed weekly commitments
- Priority based study scheduling
- Natural language calendar CRUD
- Manual event CRUD
- Study-session deferral when the student is tired/unavailable
- Week-wide study redistribution
- Timetable model fallback
- Imported-class review before relying on the generated schedule

### Bugs and reliability issues fixed

- Blank or collapsed calendar when too many events were rendered
- Invalid or incomplete dates silently breaking FullCalendar
- Timezone conversion shifting calendar events
- Comparisons between timezone-aware and timezone-free datetimes
- Excessive chatbot token usage from full-term schedules
- Unsafe updates or deletions when the model returned an invalid event reference
- Planner data conflicting with Streamlit widget session-state keys
- Study rescheduling accidentally affecting fixed classes or commitments
- Invalid commitment, activity, and time-range input
- meta-llama/llama-4-scout-17b-16e-instruct became unaccessible a week after deployment of the site, replaced with openai/gpt-oss-120b

## Technical choices

The project deliberately uses a small number of modules:

- `index.py` owns the interface, session state, scheduling, and calendar operations.
- `timetable_parser.py` extracts and validates timetable lessons.
- `chatbot.py` converts natural-language requests into structured calendar operations.

Calendar events use ordinary Python dictionaries and ISO datetime strings. This keeps the prototype easy to inspect and avoids introducing a database or complex domain framework before either is necessary.

The scheduler is deterministic. AI extracts or interprets user intent, while Python decides whether the returned data is valid and how application state changes.

For safety and maintainability:

- The Groq API key is read from Streamlit secrets or an environment variable.
- Secrets are not stored in source code.
- Model output is parsed into a small operation schema.
- Invalid event references are ignored.
- Dates and times are validated before storage or rendering.
- Study duration and priority values are bounded.
- Fixed classes and commitments are preserved during automatic rescheduling.
- Prompt and calendar payload sizes have explicit limits.

## Scope decisions and trade-offs

To keep the project focused, several larger features were intentionally cut or simplified:

- No user accounts or shared calendars
- No database or long-term persistence
- No notifications or reminders
- No OCR for scanned or image-only PDFs
- No constraint-solving or optimisation library
- No automatic travel-time calculation
- No room, teacher, or venue extraction
- No background jobs
- No conversational memory outside the current Streamlit session

The scheduler uses a simple greedy strategy: it places higher-priority activities first and selects the earliest available one-hour slots. This was chosen because it is understandable, fast, and appropriate for a prototype. A full optimisation engine would add complexity and make the generated result harder to explain within the available development time.

## Weakest part of the implementation

The weakest part is the scheduling algorithm.

It uses a greedy first-available-slot strategy rather than evaluating the overall quality of the timetable. It avoids direct conflicts, but it does not yet account for:

- A student’s preferred study hours
- Breaks between demanding sessions
- Balanced workload across the week
- Deadlines and exam dates
- Maximum daily study time
- Different session-length preferences
- Travel time
- Subject difficulty
- Student energy levels
- Rescheduling dependencies after manual edits

As a result, a schedule can be technically valid without being the most comfortable or effective schedule for the student.

## What I would improve next

The next improvement would be a constraint-based scheduler with an explicit scoring system. Each candidate schedule could be scored based on priority, deadlines, daily workload, preferred hours, spacing, breaks, and weekend preferences.

After that, I would add:

1. Persistent storage and user accounts
2. Automated tests for parsing, conflicts, CRUD operations, and rescheduling
3. OCR support for scanned timetables
4. A review-and-confirm step before AI-generated operations are applied
5. Google Calendar or Outlook export
6. Better navigation through full-term schedules
7. Structured logging and error reporting
8. Accessibility and mobile-layout testing

## Running the project

Live URL : https://ai-adaptive-scheduler.streamlit.app/

## Or run locally:

Dependencies:

```bash
pip install -r requirements.txt
```

Set the Groq API key using an environment variable (generate one here: https://console.groq.com/home) in the Secrets section in the streamlit site:

```bash
export GROQ_API_KEY="your-api-key"
```

Alternatively, add it to `.streamlit/secrets.toml`:

```toml
GROQ_API_KEY = "your-api-key"
```

The default timetable model is `openai/gpt-oss-120b`, with
`llama-3.3-70b-versatile` as its fallback. The importer limits model output to
1,500 tokens so it stays within Groq's common 8,000 TPM starter limit. You can
override either model or the output limit with `TIMETABLE_MODEL`,
`TIMETABLE_FALLBACK_MODEL`, and `TIMETABLE_MAX_COMPLETION_TOKENS`.

Start the application:

```bash
streamlit run index.py
```

Use a text-based timetable PDF (an anonymized test timetable has been provided in the google drive). Scanned image-only PDFs currently require OCR before upload.

