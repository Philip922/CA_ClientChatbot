# Cadre AI Chatbot — Claude Code Context

## Project
Customer support chatbot for Cadre AI built as a take-home evaluation.
Angular frontend deployed to S3 + CloudFront. Python backend on AWS
Lambda with streaming via Function URL. LangGraph orchestrator with
three tools. OpenRouter API for LLM access.

## Architecture (decided — do not revisit)
- Frontend: Angular 17 standalone components, custom SCSS, no UI library
- Backend: FastAPI + LangGraph, deployed as Lambda zip
- Streaming: Lambda RESPONSE_STREAM + FastAPI StreamingResponse (SSE)
- LLM: OpenRouter → claude-sonnet-4-6
- RAG: sentence-transformers in-memory, no vector DB
- No database — feedback logged to CloudWatch, state is session-only

## Project Structure
- /frontend — Angular app
- /backend — Lambda function (FastAPI + LangGraph)
- /backend/documents — PDF files for RAG (not committed)
- /backend/orchestrator — LangGraph graph, state, nodes, tools
- /backend/rag — PDF loader, embedder, index
- plan.md, backend-plan.md, system_prompts.md — source of truth docs

## Commands
# Frontend
cd frontend && ng build --configuration production
cd frontend && ng serve

# Backend (local)
cd backend && pip install -r requirements.txt
cd backend && uvicorn app:app --reload

# Package Lambda
cd backend && pip install -r requirements.txt -t . && zip -r ../cadre-lambda.zip .

## Custom Commands
/review — scan for hallucinated imports, missing error handlers,
          and any hardcoded values that should be env variables
/package — rebuild and repackage the Lambda zip
/test-scenarios — run all 6 brief scenarios through the agent locally

## Constraints
- Never modify handler.py handler name — Lambda expects handler.handler
- Never remove Cache-Control or X-Accel-Buffering headers from streaming responses
- Never commit .env or any file in /backend/documents
- Never install new dependencies without adding them to requirements.txt
- system_prompts.md is the source of truth for all prompts — edit there first

## Current State
Phase: [Bug Fix]
Last completed: [Deployment]
