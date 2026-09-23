# Cadre AI Chatbot — Claude Code Context

## Project
Customer support chatbot for Cadre AI built as a take-home evaluation.
Angular frontend deployed to S3 + CloudFront. Python backend on AWS
Lambda, streaming through a Function URL. LangGraph orchestrator with
three tools. OpenRouter API for LLM and embedding access.

Full technical documentation lives in /docs — start at docs/README.md.

## Architecture (decided — do not revisit)
- Frontend: Angular 20 standalone components, signals, zoneless change
  detection, OnPush, custom SCSS, no UI library
- Backend: FastAPI + LangGraph, deployed as a Lambda zip (Python 3.13, x86_64)
- Streaming: Function URL RESPONSE_STREAM + Lambda Web Adapter layer
  (handler `run.sh` → uvicorn) + FastAPI StreamingResponse (SSE).
  `handler.handler` (Mangum) is the buffered fallback only
- LLM: OpenRouter → anthropic/claude-sonnet-4-6
- RAG: no vector DB. Corpus embedded offline (scripts/build_index.py) into
  rag_index.npz, shipped in the zip, searched in memory with numpy.
  Query embeddings are hosted (OpenRouter, openai/text-embedding-3-small).
  sentence-transformers is dev/test only — it does not fit in the Lambda zip
- No database — feedback logged to CloudWatch, conversation state lives in
  the browser only

## Project Structure
- /WebUI — Angular app
- /LambdaBackend — Lambda function (FastAPI + LangGraph)
- /LambdaBackend/documents — PDF files for RAG (not committed)
- /LambdaBackend/orchestrator — LangGraph graph, state, nodes, prompts parser, tools/
- /LambdaBackend/rag — PDF loader, embedders, vector index
- /LambdaBackend/scripts — build_index.py, package.sh
- /LambdaBackend/system_prompts.md — prompt source of truth (parsed at runtime)
- /docs — technical documentation (architecture, API contract, backend,
  agents, web UI, security, deployment, decisions)
- plan-lambdabackend.md, plan_webui.md — original plans (historical;
  deviations are recorded in docs/decisions.md)

## Commands
# Frontend
cd WebUI && npx ng serve                 # :4200, proxies /chat and /feedback → :8000
cd WebUI && npx ng build                 # production is the default configuration
cd WebUI && npm run test:ci              # headless Karma run
cd WebUI && npm run lint
cd WebUI && node tools/mock-server.mjs   # fake backend on :8000

# Backend (local)
cd LambdaBackend && pip install -r requirements-dev.txt
cd LambdaBackend && python scripts/build_index.py
cd LambdaBackend && uvicorn app:app --reload --port 8000
cd LambdaBackend && pytest               # no network or API keys needed

# Package Lambda (builds for the Lambda interpreter, enforces the 250 MB limit)
cd LambdaBackend && ./scripts/package.sh

## Custom Commands
(Not yet defined — no .claude/commands/ directory exists.)
/review — scan for hallucinated imports, missing error handlers,
          and any hardcoded values that should be env variables
/package — rebuild and repackage the Lambda zip
/test-scenarios — run all 6 brief scenarios through the agent locally

## Constraints
- Never modify handler.py handler name — Lambda expects handler.handler
- Never change run.sh's role as the streaming entry point
- Never remove Cache-Control or X-Accel-Buffering headers from streaming responses
- Never enable CORS on the Function URL — FastAPI's CORSMiddleware owns it
- Never commit .env, rag_index.npz, or any file in /LambdaBackend/documents
- Never install new dependencies without adding them to requirements.txt
  (runtime) / requirements-dev.txt (dev only) / WebUI/package.json
- Never add torch or sentence-transformers to requirements.txt (250 MB limit)
- system_prompts.md is the source of truth for all prompts — edit there first
- Scraper pages: PAGE_PATHS in scraper.py and the list in system_prompts.md
  must stay in sync (a test enforces it)
- API URL change: update environment.prod.ts AND the CSP connect-src in
  WebUI/src/index.html together
- Embedding model change: rebuild the index; the runtime refuses a mismatch
- Update /docs when behaviour, config, or the API contract changes

## Current State
Phase: [Make production quality]
Last completed: [Unit testing, linting, security hardening, technical docs]
