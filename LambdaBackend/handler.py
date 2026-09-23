"""Lambda entry points.

Two, because one runtime cannot do both jobs:

* **Streaming (primary).** `/chat` needs `RESPONSE_STREAM`, which the Python
  runtime does not implement. The Lambda Web Adapter layer does: it runs the
  ASGI app as a local HTTP server and streams its response through the Function
  URL. In that mode Lambda's handler is `run.sh` and this module is unused.

* **Buffered (fallback).** `handler` below is a Mangum adapter, for invoking the
  function without the adapter layer — API Gateway, direct `lambda invoke`, or
  a smoke test. `/health` and `/feedback` behave identically; `/chat` still
  returns the right body, but it arrives in one piece at the end rather than
  token by token.

See README.md § Deploying for which to configure.
"""

from mangum import Mangum

from app import app

handler = Mangum(app, lifespan="off")
