# Cadre AI Chatbot — Backend Plan

## 1. Overview

Python-based backend deployed as an AWS Lambda function with streaming
response enabled via Function URL. The system uses LangGraph to
orchestrate a single agent loop with three specialized tools. All LLM
calls route through OpenRouter using `claude-sonnet-4-6` as the primary
model. The knowledge base is built from user-provided PDF documents,
embedded at cold start and queried via in-memory semantic search.

---

## 2. Architecture

```
Angular UI
     │
     │  POST /chat  (SSE stream)
     ▼
Lambda Function URL (RESPONSE_STREAM)
     │
     ▼
FastAPI App (handler.py → app.py)
     │
     ▼
LangGraph Orchestrator
     │
     ├── tool: query_knowledge_base    → in-memory RAG over PDFs
     ├── tool: scrape_cadre_website    → live Cadre site content
     └── tool: escalate_to_human       → booking link + summary
     │
     ▼
OpenRouter API (claude-sonnet-4-6)
```

---

## 3. Project Structure

```
backend/
├── handler.py                  # Lambda entry point
├── app.py                      # FastAPI app, routes, CORS
├── requirements.txt            # Python dependencies
├── system_prompts.md           # All agent and tool prompts (source of truth)
├── orchestrator/
│   ├── graph.py                # LangGraph StateGraph definition
│   ├── state.py                # AgentState model
│   ├── nodes.py                # Orchestrator node and routing logic
│   └── tools/
│       ├── __init__.py
│       ├── rag.py              # query_knowledge_base tool
│       ├── scraper.py          # scrape_cadre_website tool
│       └── escalation.py      # escalate_to_human tool
├── rag/
│   ├── loader.py               # PDF ingestion and chunking
│   ├── embedder.py             # Embedding model wrapper
│   └── index.py                # In-memory vector index (cosine similarity)
├── documents/                  # User-provided PDF files (not committed to git)
│   └── .gitkeep
└── tests/
    ├── test_graph.py
    ├── test_tools.py
    └── test_rag.py
```

---

## 4. Dependencies

| Package | Purpose |
|---|---|
| `fastapi` | HTTP framework and route definitions |
| `mangum` | ASGI adapter for Lambda (non-streaming routes only) |
| `langgraph` | Orchestrator state graph and tool loop |
| `langchain-openai` | OpenRouter-compatible LLM client |
| `langchain-core` | Tool definitions, messages, prompt templates |
| `sentence-transformers` | Local embedding model for RAG |
| `pypdf` | PDF text extraction |
| `httpx` | Async HTTP client for web scraping |
| `beautifulsoup4` | HTML parsing for scraper tool |
| `pydantic` | Request/response validation, tool schemas |
| `numpy` | Cosine similarity for in-memory vector search |
| `python-dotenv` | Local environment variable loading |

---

## 5. Environment Variables

| Variable | Description |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter API key — never committed to git |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` |
| `MODEL_NAME` | `anthropic/claude-sonnet-4-6` |
| `CADRE_WEBSITE_URL` | Base URL of the Cadre AI website |
| `SCRAPE_CACHE_TTL` | Seconds before cached scrape expires (default 3600) |
| `MAX_ITERATIONS` | Max agent loop iterations before forced escalation (default 3) |
| `EMBEDDING_MODEL` | Sentence transformer model name (default `all-MiniLM-L6-v2`) |
| `CORS_ALLOW_ORIGIN` | Allowed origin for CORS (CloudFront URL in production) |

Set all of these in Lambda → Configuration → Environment variables.
Locally, use a `.env` file at the project root (add to `.gitignore`).

---

## 6. API Endpoints

### `POST /chat`

Streaming SSE endpoint. Accepts a message and conversation history,
runs the LangGraph orchestrator loop, and streams events back to the
client as they occur.

**Request body:**

```json
{
  "message": "What industries does Cadre work with?",
  "history": [
    { "role": "user", "content": "..." },
    { "role": "agent", "content": "..." }
  ]
}
```

**SSE event types emitted:**

| Event type | Payload | When |
|---|---|---|
| `tool_start` | `{ "tool": "query_knowledge_base" }` | Tool call begins |
| `tool_end` | `{ "tool": "...", "sources": [...] }` | Tool returns result |
| `token` | `{ "content": "Cadre works with..." }` | LLM token arrives |
| `error` | `{ "message": "..." }` | Unhandled exception |
| `[DONE]` | — | Stream complete |

### `GET /health`

Returns `{ "status": "ok" }`. Used by CloudFront and monitoring to
verify the function is alive.

### `POST /feedback`

Non-streaming. Accepts a message rating from the UI thumbs up/down
buttons. Logs the rating to CloudWatch for now — no database in PoC.

**Request body:**

```json
{
  "message_id": "uuid-string",
  "value": "up",
  "message_content": "Optional — the agent response text"
}
```

**Response:** `{ "status": "received" }`

---

## 7. LangGraph Orchestrator

### State

The `AgentState` holds the full conversation message list, an iteration
counter, and an optional escalation reason. The message list accumulates
both LLM responses and tool results across iterations. The iteration
counter is incremented on every pass through the orchestrator node and
checked before each tool call decision to enforce the maximum.

### Nodes

**Orchestrator node** — passes the system prompt, conversation history,
and current message list to the LLM via OpenRouter. The LLM responds
with either a tool call or a final text answer. Increments the iteration
counter on every call.

**Tool node** — a LangGraph `ToolNode` that receives tool call requests
from the orchestrator, executes the matching tool function, and injects
the result back into the message list as a `ToolMessage`.

**Escalate node** — triggered when `MAX_ITERATIONS` is reached without
a final answer. Generates a fixed escalation message with the booking
link and ends the graph.

### Routing logic

After each orchestrator node execution, a conditional edge evaluates the
last message. If the message contains tool calls, routing goes to the
tool node. If the tool called is `escalate_to_human`, routing goes
directly to the end. If no tool calls are present, the LLM has produced
a final answer and routing goes to the end. If the iteration counter has
reached `MAX_ITERATIONS`, routing forces the escalate node regardless of
the LLM output.

### Iteration cap

The hard cap at `MAX_ITERATIONS` (default 3) prevents runaway loops. On
cap hit, the orchestrator does not make another LLM call — the escalate
node produces the final message directly from a template, not from the
model.

---

## 8. Tools

### `query_knowledge_base`

Performs semantic search over the embedded PDF content. Accepts a query
string and an optional `top_k` parameter (default 3). Returns a list of
the top matching chunks, each with the chunk text, the source document
name, the page number, and the similarity score. Returns an empty list
if no chunks score above the minimum threshold (0.35). The orchestrator
calls this tool first on almost every factual question before considering
a web scrape.

**Inputs:** `query: str`, `top_k: int`

**Returns:** `list[{ chunk, source, page, score }]`

**Failure behavior:** If the embedding model fails to encode the query,
returns a single result with a descriptive error string so the
orchestrator can fall back to scraping.

---

### `scrape_cadre_website`

Fetches and parses a specific page from the Cadre AI website. Accepts
a page identifier from a fixed list: `services`, `about`, `industries`,
`case-studies`. Maps the identifier to the full URL, fetches the HTML
via `httpx`, strips navigation, footer, scripts, and style tags using
BeautifulSoup, and returns cleaned plain text capped at 4000 characters.
Results are cached in a module-level dictionary with a TTL defined by
`SCRAPE_CACHE_TTL` — the cache is checked before every fetch and
populated on every successful fetch. The orchestrator calls this tool
when the knowledge base returns no results or low-confidence results, or
when the user asks about something that may have changed recently.

**Inputs:** `page: Literal["services", "about", "industries", "case-studies"]`

**Returns:** `str` — cleaned page text or an error message

**Failure behavior:** Network timeout or non-200 response returns a
graceful string describing the failure. The orchestrator is prompted to
treat a scrape failure as a signal to escalate rather than retry.

---

### `escalate_to_human`

Produces a structured handoff response. Accepts a reason code from a
fixed enum and a conversation summary string. Maps the reason code to a
pre-written contextual message and returns the booking link alongside
the message and summary. Makes no external calls — all output is
generated from static templates. The orchestrator calls this tool when
pricing details are requested, when the user identifies as an existing
client with an account issue, when a custom scoping request is detected,
or when explicitly invoked by the routing logic after `MAX_ITERATIONS`.

**Inputs:**
`reason: Literal["pricing", "existing_client", "custom_scope", "user_requested", "low_confidence"]`,
`conversation_summary: str`

**Returns:** `{ booking_link, message, summary }`

**Failure behavior:** Cannot fail — no external dependencies.

---

## 9. RAG Pipeline

### Document ingestion

PDF files are placed in the `documents/` folder before deployment.
At Lambda cold start, the `loader.py` module reads every PDF in the
folder using `pypdf`, extracts text page by page, and splits each page
into chunks of approximately 500 characters with a 50-character overlap.
Each chunk carries metadata: source filename, page number, and chunk
index within the page. Ingestion runs once per cold start and the
resulting chunks are held in memory for the lifetime of the function
instance.

### Embedding

After chunking, `embedder.py` encodes all chunks using
`sentence-transformers` with the model defined by `EMBEDDING_MODEL`.
The default model is `all-MiniLM-L6-v2` — small, fast, and accurate
enough for a document corpus of this size. Embeddings are
L2-normalized at generation time so that dot product is equivalent to
cosine similarity at query time. The embedding model itself is also
loaded once at cold start and kept in memory.

### Query

At query time, the input string is encoded with the same model,
normalized, and compared against all chunk embeddings using a dot
product via numpy. Results are sorted by score, filtered by threshold,
and the top `k` are returned with their metadata.

### Cold start impact

Loading the embedding model and encoding the full document corpus adds
approximately 3–8 seconds to Lambda cold starts depending on corpus
size. This is acceptable for a PoC. Warm invocations are unaffected.
For production, consider packaging pre-computed embeddings as a pickle
file to eliminate re-encoding on every cold start.

---

## 10. Streaming Implementation

The `/chat` endpoint uses the Lambda Function URL `RESPONSE_STREAM`
invoke mode. The FastAPI `StreamingResponse` with `media_type="text/event-stream"`
is used to emit SSE events. The LangGraph graph is run via
`graph.astream_events()` with `version="v2"`, which emits granular
events for every node execution, tool call, and LLM token.

The streaming handler maps LangGraph event types to SSE event types:

| LangGraph event | SSE event emitted |
|---|---|
| `on_chat_model_stream` | `token` |
| `on_tool_start` | `tool_start` |
| `on_tool_end` | `tool_end` with sources payload |
| Graph end | `[DONE]` |
| Exception | `error` |

Each SSE event is a single `data:` line followed by two newlines,
conforming to the SSE spec. The `tool_end` event includes the sources
extracted from the tool's return value so the Angular UI can populate
the sources panel.

---

## 11. CORS Strategy

CORS is handled at two layers:

**Layer 1 — Function URL CORS config:** Set in Lambda console.
Allow origin set to the CloudFront domain in production and `*` during
development.

**Layer 2 — FastAPI CORSMiddleware:** Applied in `app.py` as a fallback.
Reads the allowed origin from the `CORS_ALLOW_ORIGIN` environment
variable.

Both layers are needed because the Function URL CORS config handles
preflight `OPTIONS` requests, while the FastAPI middleware handles CORS
headers on the actual streaming response.

---

## 12. Phase Breakdown

### Phase 1 — Project scaffold and Lambda baseline

Set up the project structure, `requirements.txt`, and `.env` template.
Implement `handler.py`, `app.py` with health and feedback endpoints,
and verify cold start and streaming work end-to-end on Lambda with a
mock response. No LangGraph or tools yet.

**Exit criteria:** `/health` returns 200, `/chat` streams mock tokens
from Lambda Function URL, CORS allows the Angular origin.

### Phase 2 — RAG pipeline

Implement `loader.py`, `embedder.py`, and `index.py`. Place one or two
test PDFs in `documents/` and verify chunking, embedding, and query
all work locally. Add `query_knowledge_base` tool and write unit tests
for threshold behavior and empty results.

**Exit criteria:** `query_knowledge_base("what industries does Cadre serve")`
returns relevant chunks from the PDF corpus locally.

### Phase 3 — Scraper and escalation tools

Implement `scrape_cadre_website` with TTL cache and `escalate_to_human`
with all five reason codes. Write unit tests mocking `httpx` for the
scraper and verifying all escalation reason mappings.

**Exit criteria:** Both tools return correct output locally, scraper
cache prevents duplicate fetches within TTL window.

### Phase 4 — LangGraph orchestrator

Implement `state.py`, `nodes.py`, and `graph.py`. Wire all three tools
into the graph. Test the full iteration loop locally: query KB first,
scrape if low confidence, escalate on max iterations. Run all 6 brief
scenarios through the graph and verify routing decisions.

**Exit criteria:** All 6 scenarios produce a correct final answer or
a correct escalation locally without hitting `MAX_ITERATIONS`.

### Phase 5 — Streaming integration

Connect the LangGraph `astream_events` output to the FastAPI
`StreamingResponse`. Verify `token`, `tool_start`, `tool_end`, and
`[DONE]` events all arrive correctly from the Lambda Function URL.
Test from curl and the browser console before touching Angular.

**Exit criteria:** curl shows individual tokens arriving with visible
gaps, `tool_start` events appear before the first token of each response.

### Phase 6 — Deploy and smoke test

Package all dependencies into `cadre-lambda.zip`, upload to Lambda,
set all environment variables in the console, and run all 6 scenarios
from the live Angular UI on CloudFront.

**Exit criteria:** Public URL accessible, streaming works end-to-end,
feedback endpoint logs to CloudWatch.

---

## 13. Out of Scope

- Database persistence for conversations or feedback (CloudWatch logs only)
- Authentication or API key validation on the Lambda endpoints
- Multiple PDF corpora or dynamic document upload
- Retry logic on OpenRouter failures
- Rate limiting
- Multi-region deployment
