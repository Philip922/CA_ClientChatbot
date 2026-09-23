#!/bin/bash
# Lambda handler when the Lambda Web Adapter layer is attached.
# Set the function handler to `run.sh` and AWS_LAMBDA_EXEC_WRAPPER=/opt/bootstrap.
exec python -m uvicorn app:app --host 0.0.0.0 --port "${PORT:-8080}" --no-access-log
