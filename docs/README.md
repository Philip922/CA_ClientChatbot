# Cadre AI Chatbot — Technical Documentation

A customer-support chatbot for Cadre AI. An Angular single-page app streams
answers from a Python agent running on AWS Lambda. The agent is a LangGraph
loop around one LLM (Claude Sonnet via OpenRouter) with three tools: semantic
search over Cadre's PDF corpus, a fetcher for an allowlisted set of Cadre
website pages, and a templated hand-off to the human team.

## Reading order

| Document | What it covers |
|---|---|
| [README.md](README.md) | This page: system overview, request lifecycle, repository map |
| [api_contract.md](api_contract.md) | HTTP endpoints, request/response schemas, the SSE event vocabulary |
| [lambda_backend.md](lambda_backend.md) | FastAPI app, Lambda entry points, configuration, dependencies, error handling, tests |
| [agents.md](agents.md) | LangGraph graph, state, routing, tools, RAG pipeline, models, prompts |
| [web_ui.md](web_ui.md) | Angular app structure, streaming client, rendering, state, styling, tests |
| [security.md](security.md) | Threat model and every security control, front and back |
| [deployment.md](deployment.md) | AWS resources, packaging, environment, CloudFront, operations |
| [decisions.md](decisions.md) | Architecture decisions, trade-offs, known limitations, future work |

---

## System overview

```mermaid
flowchart LR
    subgraph Browser
        UI["Angular 20 SPA<br/>(signals, zoneless)"]
    end

    subgraph AWS
        CF["CloudFront"]
        S3[("S3<br/>static assets")]
        FURL["Lambda Function URL<br/>invoke mode RESPONSE_STREAM"]
        subgraph Lambda["Lambda (Python 3.13)"]
            LWA["Lambda Web Adapter layer"]
            API["FastAPI app<br/>(uvicorn)"]
            G["LangGraph orchestrator"]
            IDX[("rag_index.npz<br/>in-memory vectors")]
        end
        CW[("CloudWatch Logs")]
    end

    subgraph External
        OR["OpenRouter<br/>chat + embeddings"]
        SITE["cadreai.com"]
    end

    UI -- "GET /, assets" --> CF --> S3
    UI -- "POST /chat (SSE)<br/>POST /feedback" --> FURL --> LWA --> API --> G
    G -- "chat completions<br/>(streaming)" --> OR
    G -- "query embedding" --> OR
    G -- "cosine search" --> IDX
    G -- "GET allowlisted page" --> SITE
    API -- "logs, feedback" --> CW
```

| Layer | Technology | Where it lives |
|---|---|---|
| Frontend | Angular 20 standalone components, signals, zoneless change detection, custom SCSS, `marked`, Lucide icons | `WebUI/` |
| Hosting | S3 + CloudFront | AWS |
| API | FastAPI + `StreamingResponse` (Server-Sent Events) | `LambdaBackend/app.py` |
| Runtime | AWS Lambda, Python 3.13, Lambda Web Adapter for response streaming | `LambdaBackend/run.sh` |
| Agent | LangGraph `StateGraph` with a `ToolNode` | `LambdaBackend/orchestrator/` |
| LLM | `anthropic/claude-sonnet-4-6` through OpenRouter's OpenAI-compatible API | configured in `config.py` |
| Retrieval | Offline-built numpy index + hosted embeddings (`openai/text-embedding-3-small`) | `LambdaBackend/rag/` |
| Persistence | None. Conversation state lives in the browser; feedback goes to CloudWatch | — |

---

## Request lifecycle

One chat turn, end to end:

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant UI as ChatService (Angular)
    participant API as FastAPI /chat
    participant G as LangGraph
    participant LLM as OpenRouter (Sonnet)
    participant T as Tool

    U->>UI: types question, presses Enter
    UI->>UI: append user msg + empty agent msg (status=streaming)<br/>build trimmed history
    UI->>API: POST /chat {message, history}<br/>Accept: text/event-stream
    API->>API: Pydantic validation (size caps)
    API->>G: astream_events(state, version="v2")
    G->>LLM: system prompt + history + message (tools bound)
    LLM-->>G: tool_call(query_knowledge_base)
    G-->>API: on_tool_start
    API-->>UI: event: tool_start
    G->>T: run tool
    T-->>G: (content for LLM, artifact = sources)
    G-->>API: on_tool_end
    API-->>UI: event: tool_end {sources}
    G->>LLM: messages + ToolMessage
    loop each streamed chunk
        LLM-->>G: token
        G-->>API: on_chat_model_stream
        API-->>UI: event: token {content}
        UI->>UI: buffer, flush once per animation frame
    end
    API-->>UI: event: sources {all sources}
    API-->>UI: data: [DONE]
    UI->>UI: status=complete, show actions + sources
```

Things that can change this flow:

- **Escalation.** If the model calls `escalate_to_human`, or the loop reaches
  `MAX_ITERATIONS` with a tool call still pending, the graph routes to the
  `escalate` node. That node writes a templated message without calling the
  LLM. `app.py` streams that text as `token` events.
- **Errors.** Any exception inside the stream becomes `event: error` followed
  by `[DONE]`. The HTTP status is always 200 once streaming has started.
- **User stop.** The client aborts the `fetch`. Any partial text becomes a
  complete answer. If no text had arrived yet, the turn becomes a retryable
  error.

---

## Repository map

```
CA_ClientChatbot/
├── CLAUDE.md                  Claude Code project context
├── docs/                      ← you are here
├── plan-lambdabackend.md      Original backend plan (historical; see decisions.md for deviations)
├── plan_webui.md              Original frontend plan (historical)
├── LambdaBackend/
│   ├── app.py                 FastAPI routes + SSE translation layer
│   ├── handler.py             Mangum adapter (buffered fallback entry point)
│   ├── run.sh                 Entry point under the Lambda Web Adapter (streaming)
│   ├── config.py              All environment variables, resolved once
│   ├── system_prompts.md      Source of truth for every prompt (parsed at runtime)
│   ├── orchestrator/          LangGraph graph, state, nodes, prompts parser, tools/
│   ├── rag/                   PDF loader, embedders, vector index
│   ├── scripts/               build_index.py, package.sh
│   ├── documents/             Source PDFs (git-ignored)
│   └── tests/                 121 pytest tests, no network
└── WebUI/
    ├── src/app/core/          ChatService, SSE decoder, FeedbackService, markdown pipe
    ├── src/app/features/chat/ Chat page, components, models
    ├── src/styles/            Design tokens, typography, globals
    ├── tools/mock-server.mjs  Backend mock implementing the same contract
    └── proxy.conf.json        Dev proxy: /chat, /feedback → localhost:8000
```

## Quick start (local)

```bash
# Backend
cd LambdaBackend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                 # set OPENROUTER_API_KEY
python scripts/build_index.py        # after placing PDFs in documents/
uvicorn app:app --reload --port 8000

# Frontend (second terminal)
cd WebUI
npm ci
npx ng serve                         # http://localhost:4200, proxied to :8000
```
