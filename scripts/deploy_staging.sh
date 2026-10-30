#!/usr/bin/env bash
# Deploys one commit of main to the staging server. See docs/project/STAGING.md.
#
# GitHub Actions reaches this script through a forced command in authorized_keys, so
# the commit arrives in SSH_ORIGINAL_COMMAND. By hand on the server (e.g. a rollback):
#   bash scripts/deploy_staging.sh <full commit sha>

# Everything is inside main so bash has read the whole script before the checkout
# below replaces this file.
main() {
    set -euo pipefail

    local sha="${SSH_ORIGINAL_COMMAND:-${1:-}}"
    if [[ ! "$sha" =~ ^[0-9a-f]{40}$ ]]; then
        echo "Expected a full commit sha" >&2
        exit 2
    fi

    cd "$(dirname "${BASH_SOURCE[0]}")/.."

    git fetch --prune --quiet origin
    # A leaked deploy key must not be able to run a commit that never reached main
    if ! git merge-base --is-ancestor "$sha" origin/main; then
        echo "Commit $sha is not on main" >&2
        exit 3
    fi

    git checkout --quiet --detach "$sha"

    export IMAGE_TAG="$sha"
    local compose=(docker compose --env-file .env.production -f docker-compose.prod.yml)
    "${compose[@]}" pull --quiet web
    "${compose[@]}" up -d --no-build

    # The Caddyfile bind mount still points at the old file after a checkout changes it,
    # and a restart would keep it. Certificates are safe in the caddy_data volume.
    "${compose[@]}" up -d --no-build --no-deps --force-recreate caddy

    # The disk is small. Older images can still be pulled again for a rollback.
    docker image prune --all --force --filter "until=168h" >/dev/null \
        || echo "Warning: could not prune old images" >&2

    echo "Deployed $sha"
}

main "$@"
