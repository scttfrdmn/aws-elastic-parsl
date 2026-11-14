#!/bin/sh
# With arguments: run them (e.g. `globus-compute-endpoint login`).
# Without: run the reverse-tunnel helper in the background and the endpoint in the foreground.
set -e
if [ "$#" -gt 0 ]; then
    exec "$@"
fi
: "${GC_ENDPOINT:=aws-stock-provider}"
: "${GC_STATE_FILE:=$HOME/.globus_compute/$GC_ENDPOINT/awsproviderstate.json}"
: "${WORKER_PORT:=54000}"
python -m aws_elastic_parsl.tunnel --state-file "$GC_STATE_FILE" --port "$WORKER_PORT" \
    --key-path /run/secrets/ssh-key &
exec globus-compute-endpoint start "$GC_ENDPOINT"
