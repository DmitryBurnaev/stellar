#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
mode=${1:-}
if [[ $# -gt 1 || ( -n "$mode" && "$mode" != "--scan" ) ]]; then
    echo "Usage: bash scripts/verify-containers.sh [--scan]" >&2
    exit 2
fi
check_name=containers
if [[ "$mode" == "--scan" ]]; then
    check_name=security
fi
output=${STELLAR_CHECK_DIR:-"$root/.local/verification/$check_name"}
mkdir -p "$output"
output=$(cd -- "$output" && pwd)
scratch=$(mktemp -d)
context="$scratch/context"
project=stellar-check-$(basename "$scratch" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')
export DOCKER_IMAGE="stellar:$project"
export TEST_DOCKER_IMAGE="stellar:$project-test"
export COMPOSE_PROFILES=""
compose=(docker compose --project-name "$project" --project-directory "$context"
    --file "$context/docker-compose.yml" --env-file /dev/null)
compose_started=false

cleanup() {
    status=$?
    trap - EXIT
    if $compose_started && ! "${compose[@]}" --profile test down --volumes > "$output/cleanup.log" 2>&1; then
        status=1
    fi
    rm -rf -- "$scratch"
    printf '%s\n' "$status" > "$output/exit-code"
    exit "$status"
}
trap cleanup EXIT

cd "$root"
git rev-parse HEAD > "$output/base-revision"
git status --short > "$output/worktree"
docker version --format '{{.Server.Version}} {{.Server.Os}}/{{.Server.Arch}}' 2>&1 | tee "$output/docker-version"
mkdir -p "$context/etc"
cp .dockerignore Dockerfile pyproject.toml uv.lock alembic.ini docker-compose.yml "$context/"
cp etc/docker-entrypoint "$context/etc/"
tar -c --exclude=__pycache__ --exclude='.DS_Store' --exclude='.env*' --exclude='.ssh' src |
    tar -xf - -C "$context"
compose_started=true

if [[ "$mode" == "--scan" ]]; then
    trivy --version > "$output/trivy-version"
    "${compose[@]}" build app 2>&1 | tee "$output/build.log"
    docker image inspect "$DOCKER_IMAGE" --format '{{.Id}} {{.Os}}/{{.Architecture}}' > "$output/service-image"
    read -r _ platform < "$output/service-image"
    docker image save "$DOCKER_IMAGE" -o "$scratch/service.tar"
    # Docker can report the manifest index ID, while Trivy reports the image config digest.
    config_path=$(tar -xOf "$scratch/service.tar" manifest.json |
        jq -er 'if length == 1 then .[0].Config else error("Expected one image in the archive") end')
    config_hash=$(tar -xOf "$scratch/service.tar" "$config_path" | openssl dgst -sha256 -r)
    config_id="sha256:${config_hash%% *}"
    printf '%s\n' "$config_id" > "$output/service-image-config"
    common=(--cache-dir "$scratch/trivy-cache" --config "" --ignorefile ""
        --severity CRITICAL,HIGH,MEDIUM,LOW)
    status=0
    trivy fs "${common[@]}" --disable-telemetry --skip-version-check \
        --scanners vuln,misconfig --include-dev-deps --skip-check-update \
        --format json --output "$output/trivy-fs.json" "$context" 2>&1 | tee "$output/trivy-fs.log" || status=1
    trivy image "${common[@]}" --disable-telemetry --skip-version-check \
        --input "$scratch/service.tar" --scanners vuln \
        --format json --output "$output/trivy-image.json" 2>&1 | tee "$output/trivy-image.log" || status=1
    for target in fs image; do
        report="$output/trivy-$target.json"
        if ! jq -e '.SchemaVersion == 2 and (.Results | type == "array")' "$report" > /dev/null; then
            status=1
            continue
        fi
        trivy convert "${common[@]}" --scanners vuln,misconfig --exit-code 1 "$report" || status=1
    done
    jq -e --arg id "$config_id" --arg arch "${platform#*/}" \
        '.ArtifactType == "container_image" and .Metadata.ImageID == $id and
         .Metadata.ImageConfig.architecture == $arch' "$output/trivy-image.json" > /dev/null || status=1
    if [[ -f "$scratch/trivy-cache/db/metadata.json" ]]; then
        cp "$scratch/trivy-cache/db/metadata.json" "$output/trivy-db.json"
    fi
    exit "$status"
fi

"${compose[@]}" build app test 2>&1 | tee "$output/build.log"
docker image inspect "$DOCKER_IMAGE" --format '{{.Id}} {{.Os}}/{{.Architecture}}' > "$output/service-image"
"${compose[@]}" run --rm -T --no-deps -e APP_SERVICE=lint test 2>&1 | tee "$output/lint.log"
"${compose[@]}" run --rm -T test 2>&1 | tee "$output/pytest.log"
"${compose[@]}" run --rm -T --entrypoint pytest \
    -e RUN_API_E2E=1 -e E2E_ARTIFACT_DIR=/tmp/stellar-e2e \
    test src/tests/e2e --confcutdir=src/tests/e2e -m e2e -q -o cache_dir=/tmp/stellar-pytest 2>&1 |
    tee "$output/http-e2e.log"
