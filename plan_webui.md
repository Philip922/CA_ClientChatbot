# Cadre AI Chatbot — Frontend Plan

## 1. Overview

Angular single-page application that provides a chat interface to the
Cadre AI support agent. The UI consumes a streaming SSE endpoint from
the FastAPI backend, renders agent responses token-by-token, surfaces
tool activity while the agent is thinking, and lets users rate and copy
individual responses.

**Primary audience:** Prospective clients, existing clients, and
business leaders visiting Cadre AI's site to get fast answers.

**UI's one job:** Make talking to the agent feel immediate, transparent,
and trustworthy — not like a generic chatbot widget.

---

## 2. Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Framework | Angular 17+ (standalone components) | Specified requirement |
| Styling | SCSS + CSS custom properties | No UI framework — avoids generic component look |
| Streaming | Native `fetch` with `ReadableStream` | `EventSource` is GET-only; POST required to send message body |
| HTTP | Angular `HttpClient` for non-streaming calls | Feedback and static requests only |
| State | Component-level signals (`signal`, `computed`) | Session-scoped state — NgRx adds overhead with no benefit |
| Icons | Lucide Angular | Lightweight, consistent, tree-shakeable |
| Markdown | `ngx-markdown` | Renders agent responses with headers, lists, and code blocks |
| Testing | Jest + Angular Testing Library | Fast unit tests for service logic |

---

## 3. Project Structure

```
src/
├── app/
│   ├── core/
│   │   └── services/
│   │       ├── chat.service.ts          # SSE streaming + message state
│   │       ├── feedback.service.ts      # Thumbs up/down POST to backend
│   │       └── clipboard.service.ts     # Copy-to-clipboard utility
│   ├── features/
│   │   └── chat/
│   │       ├── chat.component.ts        # Root chat page
│   │       ├── chat.component.html
│   │       ├── chat.component.scss
│   │       ├── components/
│   │       │   ├── message-bubble/      # Single message (user or agent)
│   │       │   ├── message-actions/     # Thumbs + copy + sources buttons
│   │       │   ├── sources-panel/       # Inline popover listing sources
│   │       │   ├── tool-indicator/      # "Searching knowledge base..." state
│   │       │   ├── input-bar/           # Text input + send button
│   │       │   └── typing-indicator/    # Animated dots while streaming starts
│   │       └── models/
│   │           ├── message.model.ts
│   │           ├── source.model.ts
│   │           └── feedback.model.ts
│   └── app.config.ts
├── styles/
│   ├── _tokens.scss                     # Color, type, spacing variables
│   ├── _typography.scss
│   └── global.scss
└── environments/
    ├── environment.ts
    └── environment.prod.ts
```

---

## 4. Data Models

### Message

Represents a single turn in the conversation. Role is either `user` or
`agent`. Status tracks the lifecycle: `streaming` while tokens are
arriving, `complete` when the stream closes, `error` if the request
fails. The `content` field accumulates token-by-token during streaming.
`toolEvents` holds the sequence of tool calls that occurred before the
final answer was produced. `feedback` stores the user's rating for the
session.

### ToolEvent

Attached to an agent message. Records which tool was called (`query_knowledge_base`,
`scrape_cadre_website`, or `escalate_to_human`), its current status
(`running` or `done`), and the `sources` returned by that tool once
complete. Drives both the `ToolIndicatorComponent` display and the
sources panel.

### Source

Returned by the backend inside `tool_end` events. A source has a `type`
(`url` for scraped web pages, `document` for knowledge base chunks), a
human-readable `label` (page title or document name), and either a `url`
string or an `excerpt` string containing the relevant text passage. The
full list of sources for a message is the flattened union of sources
across all its completed tool events.

### FeedbackPayload

Sent to the backend when a user rates a response. Contains the message
ID, the rating value (`up` or `down`), and optionally the message
content for backend logging context.

---

## 5. Services

### `ChatService`

Owns the full message list as a signal. Exposes a `sendMessage` method
that appends the user message immediately, opens a `fetch` stream to
`POST /chat`, and processes incoming SSE events as they arrive. Handles
four event types from the backend: `token` (appends content to the
active agent message), `tool_start` (adds a running tool event to the
message), `tool_end` (marks that tool event as done and attaches its
returned sources to the event), and `sources` (a dedicated event the
backend may emit at stream end with the full deduplicated source list
for the message). Marks the message `complete` when the stream closes
and `error` if the fetch throws. Exposes a `getHistory` helper that
formats the current message list for inclusion in the next request body.

### `FeedbackService`

Sends a single `POST /feedback` call with the message ID and rating
value. On failure, returns an observable error so the calling component
can revert the selected state and show a brief inline error. No retry
logic — one submission attempt per rating action.

### `ClipboardService`

Wraps `navigator.clipboard.writeText`. Returns a promise that resolves
on success and rejects on permission denial. The calling component uses
the resolved/rejected state to toggle the copy icon between its default
and confirmed states.

---

## 6. Component Breakdown

### `ChatComponent` (page root)

Owns the message list and scroll container. Auto-scrolls to the bottom
on each new token using `afterRender`. Handles two keyboard shortcuts:
`Enter` to send, `Shift+Enter` to insert a newline. Disables the input
bar while any message has `status === 'streaming'`.

### `MessageBubbleComponent`

Renders user messages right-aligned and agent messages left-aligned.
Displays accumulated `content` as markdown via `ngx-markdown` — renders
incrementally as tokens arrive without re-parsing the full string on
each update. Shows `ToolIndicatorComponent` above the bubble while
`status === 'streaming'` and tool events are present. Shows
`MessageActionsComponent` only when `status === 'complete'`.

### `ToolIndicatorComponent`

Receives the current `ToolEvent[]` array from the parent message. Maps
each tool name to a human-readable label: `Searching knowledge base…`,
`Reading Cadre website…`, `Connecting you to the team…`. Displays the
label for the currently running tool. Disappears entirely once all tool
events reach `done` status and the final answer begins streaming.

### `MessageActionsComponent`

Rendered below completed agent messages only. Contains three action groups:

**Copy button** — calls `ClipboardService` with the message's plain-text
content. Icon switches to a checkmark for 2 seconds on success, then
resets to the copy icon. Does not appear on user messages.

**Feedback buttons (thumbs up / down)** — mutually exclusive toggle.
Selecting one calls `FeedbackService.submit()` and disables both buttons
to prevent re-submission. If the POST fails, reverts to unselected state
and shows a brief inline error label. Selected state persists in the
`Message` signal for the duration of the session.

**Sources button** — appears only when the message has at least one
source attached. Rendered as a subtle text link — "N sources" — next to
the copy and feedback buttons. Clicking it opens the
`SourcesPanelComponent` anchored to that message. Hidden when the
message has no sources (e.g. a direct answer that required no tool
calls).

### `SourcesPanelComponent`

A lightweight popover that opens inline below the message actions row
when the sources button is clicked. Closes on a second click of the
button, on outside click, or on `Escape`. Does not use a modal overlay —
it sits in the document flow so the user can still read the message
alongside the sources.

Displays a flat list of all sources collected from the message's tool
events. Each source renders differently by type:

**URL source** — shows the page title as a link and the domain as muted
metadata below it. Clicking the title opens the scraped page in a new
tab. Example: "Cadre AI — Industries" / cadreai.com.

**Document source** — shows the document or chunk name as a label, then
the excerpt text below it in a slightly smaller size. No link. The
excerpt is capped at 3 lines with a "Show more" toggle that expands it
inline if longer.

Sources are ordered by the sequence in which tools were called — web
sources from `scrape_cadre_website` first if it ran before the KB, or
KB chunks first otherwise. Duplicate URLs are deduplicated; duplicate
document chunks are deduplicated by content hash.

### `InputBarComponent`

Auto-growing textarea capped at 4 lines before scrolling internally.
Send button is disabled while any agent message is streaming. Emits the
input value to the parent `ChatComponent` on send; does not own message
state itself.

### `TypingIndicatorComponent`

Three animated dots rendered between the user message and the first
arriving token. Shown from the moment `sendMessage` is called until the
first `token` event is received. Respects `prefers-reduced-motion` —
shows static dots instead of animation when set.

---

## 7. Visual Design

### Design Rationale

Matched directly to Cadre AI's existing brand as seen on their website.
The background is a warm off-white — not pure white, but a faded
cream-to-linen tone that reads as professional without feeling clinical.
The single accent is Cadre's red-coral, used only for primary actions
and selected states. Near-black text on the warm background matches the
site's existing type treatment. No dark mode, no blue accents, no
gradients — the chatbot should feel like it belongs on cadreai.com, not
like a generic SaaS product dropped in.

### Color Palette

| Token | Value | Usage |
|---|---|---|
| Background | `#F2EFE9` | Page background — warm cream, not pure white |
| Surface | `#FDFCFA` | Agent message bubble, input bar background |
| Surface user | `#1A1A1A` | User message bubble (near-black, mirrors site header) |
| Border | `#E0DAD1` | Input border, dividers |
| Accent | `#C0392B` | Send button, primary actions — Cadre red-coral |
| Accent hover | `#A93226` | Accent on hover — slightly deeper red |
| Accent muted | `#F5E6E4` | Thumbs up selected background, copy confirmed |
| Text primary | `#1A1A1A` | Body copy — matches site's near-black |
| Text inverse | `#F2EFE9` | Text on user bubble (dark background) |
| Text muted | `#8A7F75` | Timestamps, labels, placeholder text |
| Success | `#C0392B` | Thumbs up selected — uses accent, no separate green |
| Escalation | `#8A7F75` | Escalation tool indicator — muted, not alarming |

### Typography

Single family: Inter — matches Cadre's site. Two weights: 400 for body,
600 for the send button and action labels. Font size 15px base, 13px
for metadata and timestamps. Line height 1.65 — slightly open to match
the site's airy feel. No ALL-CAPS labels, no tracked-out text.

---

## 8. Layout

```
┌─────────────────────────────────────────────┐
│  Cadre AI                  [status]          │  ← Header (fixed, 56px)
├─────────────────────────────────────────────┤
│                                             │
│      ┌──────────────────────────┐           │
│      │ What industries does     │           │  ← User bubble (right)
│      │ Cadre work with?         │           │
│      └──────────────────────────┘           │
│                                             │
│  ┌ Searching knowledge base...           ┐  │  ← Tool indicator
│  └────────────────────────────────────── ┘  │
│  ┌──────────────────────────────────────┐   │
│  │ Cadre works with companies across    │   │  ← Agent bubble (left)
│  │ professional services, private       │   │
│  │ equity, real estate, and more...     │   │
│  │                                      │   │
│  │  [👍] [👎]  [Copy ⎘]  [2 sources]   │   │  ← Actions (post-stream)
│  │                                      │   │
│  │  ┌────────────────────────────────┐  │   │
│  │  │ Sources                        │  │   │  ← Sources panel (inline
│  │  │                                │  │   │    popover, opens on click)
│  │  │ 🔗 Cadre AI — Industries       │  │   │
│  │  │    cadreai.com                 │  │   │
│  │  │                                │  │   │
│  │  │ 📄 Knowledge base chunk        │  │   │
│  │  │    "Cadre works with PE-backed │  │   │
│  │  │     firms across construction, │  │   │
│  │  │     real estate..." [Show more]│  │   │
│  │  └────────────────────────────────┘  │   │
│  └──────────────────────────────────────┘   │
│                                             │
├─────────────────────────────────────────────┤
│  [ Ask anything about Cadre AI...  ] [Send] │  ← Input bar (fixed, 72px)
└─────────────────────────────────────────────┘
```

Message list is the only scrolling region. Header and input bar are
position-fixed. On mobile, the input bar lifts above the software
keyboard via `env(keyboard-inset-height)`.

---

## 9. Phase Breakdown

### Phase 1 — Scaffold & Static Shell

Set up the Angular project with standalone components, SCSS design
tokens, and Inter font. Build `ChatComponent` layout with hardcoded
messages. Build `MessageBubbleComponent` with user and agent variants.
No services or API calls yet.

**Exit criteria:** Layout renders correctly at desktop and mobile widths.

### Phase 2 — Streaming Integration

Implement `ChatService` with `fetch` + `ReadableStream`. Wire up
`InputBarComponent` to `ChatService`. Render token accumulation live on
the agent bubble. Implement `ToolIndicatorComponent` driven by
`tool_start` and `tool_end` events. Implement `TypingIndicatorComponent`.

**Exit criteria:** Full streaming conversation works end-to-end with the backend.

### Phase 3 — Markdown Rendering

Integrate `ngx-markdown` into `MessageBubbleComponent`. Confirm that
streamed markdown renders correctly for headers, lists, bold text, and
inline code. Test with real agent responses from all 6 brief scenarios.

**Exit criteria:** Agent responses render as formatted markdown without
layout breakage during streaming.

### Phase 4 — Message Actions

Implement `ClipboardService` and the copy button with icon confirmation
state. Implement `FeedbackService` and the thumbs up/down buttons with
toggle, disable, and error-revert logic. Add `MessageActionsComponent`
to agent bubbles on stream completion.

**Exit criteria:** Copy works, feedback POSTs to backend, states persist
for the session duration.

### Phase 5 — Polish & Edge Cases

Auto-scroll on new messages and during streaming. Error state when
backend is unreachable — inline message with retry. Empty state on first
load with a suggested prompt. Reduced motion support. Input bar disabled
state during streaming.

**Exit criteria:** All 6 brief scenarios complete without broken states.

### Phase 6 — Build & Deploy

Production build via `ng build --configuration production`. Upload
`dist/` to S3. Configure CloudFront pointing to S3. Set `apiUrl`
environment variable to Lambda Function URL. Smoke test all 6 scenarios
on the live public URL.

**Exit criteria:** Public URL accessible, streaming works through CloudFront.

---

## 10. Out of Scope

- User authentication or session persistence across page reloads
- Chat history stored in a database
- File upload or attachment support
- Multi-language UI
- Admin dashboard for reviewing feedback ratings
- Stream cancellation (stop button)
