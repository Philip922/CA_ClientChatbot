# Cadre AI Chatbot — Backend

Python backend for the Cadre AI support chatbot: a LangGraph agent with three
tools, streaming SSE to the Angular UI, deployed as an AWS Lambda behind a
Function URL.

Implements [`backend-plan.md`](backend-plan.md). All prompts live in
[`system_prompts.md`](system_prompts.md) and are parsed from it at runtime — the
code never restates a prompt, so editing that file is how you change agent
behaviour. Its placeholders — `{booking_link}`, `{website_url}`,
`{portal_login_url}` and `{maturity_index_url}` — are filled from
`BOOKING_LINK`, `CADRE_WEBSITE_URL`, `PORTAL_LOGIN_URL` and
`MATURITY_INDEX_URL`, so a URL is configured
in exactly one place rather than drifting between the prompt and the code. Three things depart from the plan as written; see
[Deviations](#deviations-from-backend-planmd).

---

## Layout

```
handler.py              Mangum adapter (buffered fallback invocations)
run.sh                  Handler when the Lambda Web Adapter layer is attached
app.py                  FastAPI app: /chat (SSE), /health, /feedback
config.py               Every environment variable, resolved once per container
system_prompts.md        Source of truth for prompts  ─┐
orchestrator/
  prompts.py            …parsed from it here          ─┘
  graph.py              StateGraph assembly
  state.py              AgentState
  nodes.py              Orchestrator node, escalate node, routing
  tools/
    rag.py              query_knowledge_base
    scraper.py          scrape_cadre_website
    escalation.py       escalate_to_human
rag/
  loader.py             PDF ingestion and chunking (offline only)
  embedder.py           Hosted HTTP + local sentence-transformers backends
  index.py              In-memory cosine index, persisted as .npz
scripts/
  build_index.py        Build rag_index.npz from documents/
  package.sh            Build cadre-lambda.zip for the Lambda runtime
documents/              Your PDFs (git-ignored)
tests/                  102 tests, no network access required
```

---

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # then fill in the two API keys

# 1. Put PDFs in documents/ and build the index
python scripts/build_index.py

# 2. Run the API on :8000 — the Angular proxy.conf.json already points here
uvicorn app:app --reload --port 8000

# 3. In WebUI/, `ng serve` and open http://localhost:4200
```

Verify streaming independently of the UI:

```bash
curl -N -X POST http://localhost:8000/chat \
  -H 'content-type: application/json' \
  -d '{"message":"What industries does Cadre work with?","history":[]}'
```

Tokens should arrive with visible gaps, and `tool_start` before the first one.

### Tests

```bash
pytest                    # 102 tests, ~2s, no API keys needed
```

Everything is stubbed: a keyword-based embedder, `respx` for HTTP, and a
scripted model for graph runs.

---

## Building the index

The corpus is embedded **offline** and the resulting `rag_index.npz` ships
inside the zip. Cold start is then a `np.load`, not a model load.

```bash
python scripts/build_index.py                     # hosted embeddings (default)
python scripts/build_index.py --backend local     # sentence-transformers
```

Rebuild whenever `documents/` changes. The index records which embedding model
produced it and the runtime **refuses to load a mismatched one** — vectors from
different models are not comparable, and the failure is otherwise silent
(plausible-looking but meaningless scores).

`--backend local` is for offline work and tests only. sentence-transformers
pulls in PyTorch, which does not fit a Lambda zip, so an index built locally
must be queried by a Lambda configured the same way — which is not possible in
the zip deployment. Build with `hosted` for anything you intend to deploy.

### Choosing an embeddings provider

**OpenRouter serves embeddings, so the default needs no second account.** Leave
`EMBEDDINGS_API_KEY` blank and `OPENROUTER_API_KEY` is used for both — the
fallback only applies when `EMBEDDINGS_BASE_URL` and `OPENROUTER_BASE_URL` are
the same host, so a key is never silently sent to a different provider.

Any OpenAI-compatible endpoint works. Set the three variables **together** —
the model name and the key both belong to the base URL:

| Provider | `EMBEDDINGS_BASE_URL` | `EMBEDDING_MODEL` |
|---|---|---|
| OpenRouter (default) | `https://openrouter.ai/api/v1` | `openai/text-embedding-3-small` |
| OpenAI | `https://api.openai.com/v1` | `text-embedding-3-small` |
| Voyage | `https://api.voyageai.com/v1` | `voyage-3-lite` |
| Jina | `https://api.jina.ai/v1` | `jina-embeddings-v3` |

Note the provider prefix on OpenRouter model names (`openai/…`) and its absence
everywhere else. Mixing a key from one provider with another's base URL is the
easy mistake here — an `sk-or-…` key aimed at a non-OpenRouter URL is rejected
up front with that explanation rather than a bare 401.

The build script also preflights the credential with one throwaway vector before
reading any PDFs, so a wrong key, base URL or model name fails in about a second
rather than after the whole corpus has been chunked.

### Tuning `RAG_MIN_SCORE`

The plan's 0.35 floor is calibrated for `all-MiniLM-L6-v2`. Cosine ranges differ
per embedding model — `text-embedding-3-small` typically scores relevant matches
lower. After the first index build, check real scores and set the floor from
what you see:

```bash
python -c "
import asyncio
from config import get_settings
from rag.embedder import get_embedder
from rag.index import VectorIndex
s = get_settings()
i = VectorIndex.load(s.index_path, s.embedding_model)
for h in asyncio.run(i.search('what industries does Cadre serve', get_embedder(s), top_k=5, min_score=0.0)):
    print(f'{h.score:.3f}  {h.source} p.{h.page}  {h.chunk[:60]}')
"
```

Set the floor between the last relevant hit and the first irrelevant one. It is
also quoted in `system_prompts.md`, so update both.

---

## Configuration

Set these in Lambda → Configuration → Environment variables, or in `.env`
locally. See [`.env.example`](.env.example).

| Variable | Default | Notes |
|---|---|---|
| `OPENROUTER_API_KEY` | — | Required. Never commit it. |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | |
| `MODEL_NAME` | `anthropic/claude-sonnet-4-6` | See note below |
| `LLM_TEMPERATURE` | `0.2` | |
| `REQUEST_TIMEOUT` | `30` | Seconds, LLM and HTTP tools |
| `MAX_ITERATIONS` | `3` | Agent loop cap |
| `BOOKING_LINK` | `https://cadreai.com/book` | |
| `CADRE_WEBSITE_URL` | `https://cadreai.com` | |
| `PORTAL_LOGIN_URL` | `https://auth.gocadre.ai` | Portal sign-in, shared by the model, not scraped |
| `MATURITY_INDEX_URL` | `https://portal.gocadre.ai/ai-maturity-index` | Portal sign-in, shared by the model, not scraped |
| `SCRAPE_CACHE_TTL` | `3600` | Seconds |
| `SCRAPE_MAX_CHARS` | `4000` | |
| `EMBEDDING_BACKEND` | `hosted` | `hosted` or `local` |
| `EMBEDDING_MODEL` | `openai/text-embedding-3-small` | Must match the index |
| `EMBEDDINGS_BASE_URL` | `https://openrouter.ai/api/v1` | Any OpenAI-compatible endpoint |
| `EMBEDDINGS_API_KEY` | `OPENROUTER_API_KEY` | Falls back only on the same host |
| `INDEX_PATH` | `rag_index.npz` | |
| `DOCUMENTS_DIR` | `documents` | Build-time only |
| `RAG_MIN_SCORE` | `0.35` | Tune per embedding model |
| `RAG_TOP_K` | `3` | |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `500` / `50` | Build-time only |
| `CORS_ALLOW_ORIGIN` | `*` | CloudFront domain in production |

**On `MODEL_NAME`:** the plan specifies `anthropic/claude-sonnet-4-6` and that
is the default. `anthropic/claude-sonnet-5` is the current generation and is
cheaper ($2/$10 per MTok vs $3/$15) — worth a look, but it is a behaviour change
and the prompts here have not been tuned against it, so it is not the default.

---

## Packaging

```bash
./scripts/package.sh                          # python3.13, x86_64
PY_VERSION=3.12 ARCH=aarch64 ./scripts/package.sh
```

Dependencies are installed for the **Lambda** interpreter and platform, not the
local one — a locally-built wheel imports fine on your machine and fails in
Lambda with an ELF error. The script fails the build if the package exceeds
Lambda's 250 MB unzipped limit.

Current size: **32 MB zipped, 124 MB unzipped.** It warns if `rag_index.npz` is
absent, in which case `query_knowledge_base` reports the knowledge base as
unavailable and the agent falls back to scraping.

---

## Deploying

### Streaming (the real deployment)

`/chat` needs the Function URL's `RESPONSE_STREAM` invoke mode, which the Python
runtime does not implement on its own. The **Lambda Web Adapter** layer provides
it: it runs the ASGI app as a local HTTP server and streams the response
through.

1. Create the function: Python 3.13, **x86_64**, memory **1024 MB**, timeout
   **60 s**. Upload `cadre-lambda.zip`.
2. Add the adapter layer:
   `arn:aws:lambda:eu-west-1:753240598075:layer:LambdaAdapterLayerX86:<version>`
   — take the current version from
   <https://github.com/awslabs/aws-lambda-web-adapter/releases>, and use
   `LambdaAdapterLayerArm64` for `arm64`.
3. Set the handler to **`run.sh`**.
4. Add these environment variables on top of the table above:
   | Variable | Value |
   |---|---|
   | `AWS_LAMBDA_EXEC_WRAPPER` | `/opt/bootstrap` |
   | `AWS_LWA_INVOKE_MODE` | `response_stream` |
   | `PORT` | `8080` |
5. Create a Function URL: auth **NONE**, invoke mode **RESPONSE_STREAM**, and
   **no CORS configuration**.
6. Set `CORS_ALLOW_ORIGIN` to the CloudFront domain (`*` while developing). The
   FastAPI middleware answers preflight and puts headers on every response.
   Enabling CORS on the Function URL as well duplicates
   `Access-Control-Allow-Origin`, which browsers reject as "Failed to fetch".

Verify against the live URL:

```bash
curl -N -X POST "$FUNCTION_URL/chat" \
  -H 'content-type: application/json' \
  -d '{"message":"What does Cadre AI do?","history":[]}'
curl -s "$FUNCTION_URL/health"
```

If tokens arrive in one burst at the end, one of steps 2–5 is missing — that is
the buffered path below, not a streaming failure.

### Buffered (fallback)

Without the adapter layer, set the handler to **`handler.handler`** (Mangum).
`/health` and `/feedback` are identical; `/chat` returns the correct body but all
at once, so the UI shows no progressive typing. Useful for API Gateway or a
direct `lambda invoke` smoke test.

### CloudFront

Front the Function URL and the S3-hosted Angular app from one distribution so
the browser sees one origin. On the `/chat` behaviour, disable caching and
forward the `Content-Type` and `Accept` headers; the response already carries
`X-Accel-Buffering: no` to stop intermediate buffering.

---

## Deviations from `backend-plan.md`

Three, each with the reason:

1. **§4/§10 — `mangum` cannot stream.** Mangum buffers the whole ASGI response,
   so it and `RESPONSE_STREAM` are mutually exclusive. Streaming goes through
   the Lambda Web Adapter layer (`run.sh`); Mangum is kept as the buffered
   fallback (`handler.handler`). Both ship.

2. **§9 — embeddings are not computed at cold start.** sentence-transformers
   pulls in PyTorch (~800 MB unzipped) against Lambda's 250 MB zip limit. The
   corpus is embedded offline into `rag_index.npz`; only the query is embedded at
   request time, over HTTP. Cold start drops from the plan's 3–8 s to under a
   second, and the plan's own "for production, consider pre-computed embeddings"
   note becomes the default. No extra credential: OpenRouter serves embeddings,
   so `OPENROUTER_API_KEY` covers both by default.

3. **§6 — `/feedback` accepts both payload shapes.** The plan specifies
   `{message_id, value, message_content}`; the Angular client
   (`feedback.service.ts`) sends `{messageId, rating, content}`. The endpoint
   accepts either, so neither side had to change.

Two smaller notes: `RAG_MIN_SCORE` is configurable rather than hard-coded at
0.35 (the figure is embedding-model-specific), and tools return
`(content, artifact)` so citations reach the UI's `tool_end` event without
spending model context on JSON.

---

## Out of scope

Per §13: no conversation or feedback persistence (CloudWatch only), no auth on
the endpoints, a single PDF corpus with no upload path, no OpenRouter retry
logic beyond the client's one attempt, no rate limiting, single region.
