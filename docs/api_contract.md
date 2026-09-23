# API Contract

This is the interface between `WebUI/` and `LambdaBackend/`. The backend
implements it in `LambdaBackend/app.py`. The frontend consumes it in
`WebUI/src/app/core/services/chat.service.ts` and `feedback.service.ts`.
`WebUI/tools/mock-server.mjs` implements the same contract, so the UI can be
developed without the agent running.

Base URL:

| Environment | Base URL |
|---|---|
| Local (`ng serve`) | `''` (same origin; `proxy.conf.json` forwards `/chat` and `/feedback` to `http://localhost:8000`) |
| Production | The Lambda Function URL set in `WebUI/src/environments/environment.prod.ts` |

No endpoint requires authentication. See [security.md](security.md).

---

## `POST /chat`

Streams one agent reply as Server-Sent Events.

### Request

```http
POST /chat
Content-Type: application/json
Accept: text/event-stream
```

```json
{
  "message": "What industries does Cadre work with?",
  "history": [
    { "role": "user", "content": "Hi" },
    { "role": "assistant", "content": "Hello! How can I help?" }
  ]
}
```

| Field | Type | Constraint (server, `app.py`) | Client-side limit |
|---|---|---|---|
| `message` | string | 1 – 4 000 chars (`MAX_MESSAGE_CHARS`) | 4 000 (`MAX_MESSAGE_CHARS` in `message.model.ts`, enforced by `maxlength`) |
| `history` | array | ≤ 40 turns (`MAX_HISTORY_TURNS`) | ≤ 20 turns and ≤ 24 000 chars in total (`chat.service.ts`) |
| `history[].role` | `"user"` \| `"assistant"` \| `"agent"` | enum | Client sends `user` / `assistant` |
| `history[].content` | string | ≤ 32 000 chars (`MAX_TURN_CHARS`) | — |

The client's limits are tighter than the server's, so history the client has
trimmed is never rejected. The server limits only matter for requests that did
not come from the UI.

History rules the client applies (`ChatService.getHistory`):

- It includes only completed, non-empty turns. A user question whose reply
  failed is dropped together with that reply, so the model never sees two user
  turns in a row.
- It keeps the most recent contiguous run of turns that fits both caps.
- The history always starts on a user turn.

On the server, turns with only whitespace are skipped. `user` becomes a
`HumanMessage`, and anything else becomes an `AIMessage`.

### Response

```http
HTTP/1.1 200 OK
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```

Once headers are sent, the status is always `200`. Failures during the run
arrive as an `error` event. Validation failures return before streaming starts,
as `422` with FastAPI's standard JSON error body.

### Event framing

Every record is one `event:` line, one `data:` line holding a JSON object, and
a blank line. The terminator is the exception: it is a bare `data:` line.

```
event: tool_start
data: {"tool": "query_knowledge_base", "id": "6f1c…"}

event: tool_end
data: {"tool": "query_knowledge_base", "id": "6f1c…", "sources": [ … ]}

event: token
data: {"content": "Cadre works with "}

event: token
data: {"content": "professional services, private equity…"}

event: sources
data: {"sources": [ … ]}

data: [DONE]

```

### Event vocabulary

| Event | Payload | Emitted when | Client behaviour |
|---|---|---|---|
| `token` | `{"content": string}` | Each non-empty LLM chunk; also the templated escalation text; also a fallback line if the run produced no text | Appended to the agent message, batched to one flush per animation frame |
| `tool_start` | `{"tool": string, "id": string}` | LangGraph `on_tool_start`. `id` is the LangChain run id | Adds a `running` tool event; the tool indicator shows its label |
| `tool_end` | `{"tool": string, "id": string, "sources": Source[]}` | `on_tool_end`, and also `on_tool_error` (with `sources: []`) so the indicator always stops | Matches by `id` (falling back to the latest running event with that tool name); marks it `done` and stores the sources |
| `sources` | `{"sources": Source[]}` | Once, after the graph finishes, if any tool produced sources | Attached as a synthetic `summary` tool event; the UI de-duplicates across events |
| `error` | `{"message": string}` | Any exception escaping the graph | The message is shown as written (it is written for users); ends the stream |
| *(terminator)* | `[DONE]` (bare `data:`) | Always last, including after an `error` (sent from a `finally` block) | Marks the message `complete`, or `error` if it has no text |

The client is deliberately lenient. It ignores unknown event types. It accepts
`token` payloads under `content`/`token`/`text`/`delta` or as a bare string. It
also treats `event: done` the same as `[DONE]`. Because of this, adding a new
event type to the backend never breaks a frontend that is already deployed.

If the stream closes **without** `[DONE]` (Lambda timeout, dropped
connection), the client reports it as an error rather than showing partial
text as a finished answer.

### `Source` object

```ts
{ "type": "document", "label": "case-studies.pdf · p.4", "excerpt": "…chunk text…" }
{ "type": "url",      "label": "Industries | Cadre AI",  "url": "https://cadreai.com/industries" }
```

| Field | `document` (from `query_knowledge_base`) | `url` (from `scrape_cadre_website`) |
|---|---|---|
| `type` | `"document"` | `"url"` |
| `label` | `"<file>.pdf · p.<page>"` | Page `<title>` → first `<h1>` → URL without scheme |
| `excerpt` | Full chunk text (≈500 chars) | — |
| `url` | — | Absolute page URL |

`escalate_to_human` produces no sources.

---

## `POST /feedback`

Records a thumbs-up or thumbs-down rating. There is no database; the record is
written to CloudWatch as one JSON line.

### Request

The endpoint accepts both the camelCase shape the Angular client sends and the
snake_case shape in the original plan:

```json
{ "messageId": "b1e0…", "rating": "up", "content": "…the rated answer…" }
{ "message_id": "b1e0…", "value": "down", "message_content": "…" }
```

| Field (aliases) | Type | Required |
|---|---|---|
| `messageId` / `message_id` | string (client-generated UUID) | yes |
| `rating` / `value` | `"up"` \| `"down"` | yes |
| `content` / `message_content` | string | no |

### Response

`200 {"status": "received"}`. A request with an invalid `rating` gets `422`.

Log line written to CloudWatch (logger `cadre.chat`):

```
INFO:cadre.chat:feedback {"message_id": "b1e0…", "rating": "up", "content": "…"}
```

The client submits one rating per message and updates the UI optimistically.
If the request fails, it reverts the selection and shows "Couldn't send that —
try again." There is no automatic retry.

---

## `GET /health`

`200 {"status": "ok"}`. Does not touch the index, the LLM or the network. The
RAG index loads lazily on the first query, so a missing index cannot fail this
check. Note that the dev proxy does not forward `/health`; call the backend on
`:8000` directly.

---

## Client-side error mapping

`ChatService.describeFailure` turns raw failures into user-facing text. Raw
errors go to `console.error` only.

| Failure | Message shown |
|---|---|
| `event: error` | The backend's `message`, verbatim |
| HTTP 429 | "Too many requests right now…" |
| HTTP 413 | "That message is too long…" |
| HTTP ≥ 500 | "The service is having trouble right now…" |
| Other non-2xx (e.g. 422) | "That message could not be processed…" |
| `navigator.onLine === false` | "You appear to be offline…" |
| `TypeError` from `fetch` (DNS, CORS, refused) | "Could not reach the server…" |
| 60 s with no bytes (idle watchdog) | "The agent took too long to respond…" |
| Stream closed without `[DONE]` | "The connection dropped before the answer finished…" |
| `[DONE]` with empty content | "The agent returned an empty response." |
