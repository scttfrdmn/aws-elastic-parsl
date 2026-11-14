#!/usr/bin/env bash
# Run a Globus Compute endpoint on Parsl's AWSProvider from a laptop.
#
#   ./gce.sh build                 build the endpoint image
#   ./gce.sh login                 log in to Globus (interactive; once)
#   ./gce.sh configure             write the endpoint config into the volume
#   ./gce.sh start                 start the endpoint (and tunnel helper) in the background
#   ./gce.sh logs                  follow the endpoint logs
#   ./gce.sh test [N]              submit N tasks (default 10) from the SDK and print results
#   ./gce.sh stop                  stop the endpoint (the provider tears down its VPC)
#
# Environment: AWS_PROFILE (required), GC_ENDPOINT (default aws-stock-provider),
# KEY_NAME / KEY_PATH (default aws-elastic-parsl, ~/.ssh/aws-elastic-parsl.pem),
# INSTANCE_TYPE (default t3.small), WORKER_PORT (default 54000).
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
IMAGE=aws-elastic-parsl-gce
VOLUME=aws-elastic-parsl-gce-home
CONTAINER=aws-elastic-parsl-gce
GC_ENDPOINT=${GC_ENDPOINT:-aws-stock-provider}
KEY_NAME=${KEY_NAME:-aws-elastic-parsl}
KEY_PATH=${KEY_PATH:-$HOME/.ssh/aws-elastic-parsl.pem}
INSTANCE_TYPE=${INSTANCE_TYPE:-t3.small}
WORKER_PORT=${WORKER_PORT:-54000}
EP_DIR=/home/gc/.globus_compute/$GC_ENDPOINT

: "${AWS_PROFILE:?set AWS_PROFILE}"

run() {  # run a one-off command in the image, with the volume and AWS config
    docker run --rm "$@"
}
common=(-v "$VOLUME:/home/gc/.globus_compute" -v "$HOME/.aws:/home/gc/.aws:ro" -e "AWS_PROFILE=$AWS_PROFILE")

case "${1:-}" in
build)
    docker build -t "$IMAGE" -f "$here/Dockerfile" "$here/.."
    ;;
login)
    run -it "${common[@]}" "$IMAGE" globus-compute-endpoint login
    ;;
configure)
    region=$(aws configure get region --profile "$AWS_PROFILE")
    ssh_cidr="$(curl -fsS https://checkip.amazonaws.com)/32"
    gce_version=$(run "$IMAGE" python -c "import importlib.metadata as m; print(m.version('globus-compute-endpoint'))")
    sed -e "s|@@WORKER_PORT@@|$WORKER_PORT|g" -e "s|@@INSTANCE_TYPE@@|$INSTANCE_TYPE|g" \
        -e "s|@@REGION@@|$region|g" -e "s|@@KEY_NAME@@|$KEY_NAME|g" -e "s|@@SSH_CIDR@@|$ssh_cidr|g" \
        -e "s|@@STATE_FILE@@|$EP_DIR/awsproviderstate.json|g" -e "s|@@GCE_VERSION@@|$gce_version|g" \
        "$here/user_config_template.yaml.j2" > "$here/.rendered-template.yaml.j2"
    run "${common[@]}" -v "$here/.rendered-template.yaml.j2:/tmp/template.yaml.j2:ro" "$IMAGE" sh -ec "
        test -d $EP_DIR || globus-compute-endpoint configure $GC_ENDPOINT >/dev/null
        cp /tmp/template.yaml.j2 $EP_DIR/user_config_template.yaml.j2
        printf 'AWS_PROFILE: %s\n' '$AWS_PROFILE' > $EP_DIR/user_environment.yaml
        rm -f $EP_DIR/user_config_schema.json"
    rm -f "$here/.rendered-template.yaml.j2"
    echo "configured $GC_ENDPOINT: region=$region ssh_cidr=$ssh_cidr key=$KEY_NAME"
    ;;
start)
    docker run -d --name "$CONTAINER" "${common[@]}" \
        -v "$KEY_PATH:/run/secrets/ssh-key:ro" \
        -e "GC_ENDPOINT=$GC_ENDPOINT" -e "WORKER_PORT=$WORKER_PORT" "$IMAGE" >/dev/null
    echo "started; endpoint id:"
    sleep 15
    docker exec "$CONTAINER" globus-compute-endpoint list
    ;;
logs)
    docker logs -f "$CONTAINER"
    ;;
test)
    docker exec "$CONTAINER" python -m aws_elastic_parsl.gc_smoke "$GC_ENDPOINT" "${2:-10}"
    ;;
stop)
    docker exec "$CONTAINER" globus-compute-endpoint stop "$GC_ENDPOINT" || true
    docker stop -t 300 "$CONTAINER" >/dev/null || true
    docker rm "$CONTAINER" >/dev/null || true
    ;;
*)
    sed -n '2,15p' "$0"; exit 2
    ;;
esac
