#!/bin/bash
set -e

# Deploy the ledger server (app/server/) to the shared Hetzner box — same box as
# Flockt, its own docker-compose stack under its own directory. See docs/deploy.md.
#
#   ./scripts/deploy_server.sh                 build -> push to Docker Hub -> pull on the server
#   ./scripts/deploy_server.sh --no-registry   build -> stream over SSH -> load (no Docker Hub)
#   ./scripts/deploy_server.sh --migrate       also run `alembic upgrade head` on the server after deploying
#
# Modeled directly on chicken-tracker/deploy-prod.sh (a sibling project, same author,
# same box) — see that file for the reasoning behind the registry/no-registry split.

# ── Edit these once ────────────────────────────────────────────────────────────
DOCKER_USERNAME="feenstra32"
IMAGE_NAME="graphling-server"
PROD_HOST="168.119.234.226"
PROD_USER="root"
SSH_KEY="$HOME/.ssh/flockt"
PROD_DIR="/opt/graphling"
# ─────────────────────────────────────────────────────────────────────────────

IMAGE="$DOCKER_USERNAME/$IMAGE_NAME"
TAG=$(git rev-parse --short HEAD)
SSH="ssh -i $SSH_KEY $PROD_USER@$PROD_HOST"

USE_REGISTRY=1
MIGRATE=0
for arg in "$@"; do
    [ "$arg" = "--no-registry" ] && USE_REGISTRY=0
    [ "$arg" = "--migrate" ] && MIGRATE=1
done

# Warn before shipping code that is not committed — TAG comes from HEAD, so
# uncommitted work would deploy under a tag that does not contain it.
if [ -n "$(git status --porcelain)" ]; then
    echo "WARNING: working tree has uncommitted changes."
    echo "         Deploying as $TAG, which will not match what is in the image."
    printf "         Continue? [y/N] "
    read -r reply
    [ "$reply" = "y" ] || { echo "Aborted."; exit 1; }
fi

echo "Building $IMAGE:$TAG for linux/amd64..."
docker build --platform linux/amd64 -f Dockerfile -t "$IMAGE:$TAG" -t "$IMAGE:latest" .

if [ "$USE_REGISTRY" = "1" ]; then
    echo "Pushing to Docker Hub..."
    if ! docker push "$IMAGE:$TAG" || ! docker push "$IMAGE:latest"; then
        echo ""
        echo "Push failed. If that was an authorization error, either run:"
        echo "  docker login -u $DOCKER_USERNAME"
        echo "(use a Personal Access Token if the account has 2FA)"
        echo "or skip the registry entirely:"
        echo "  ./scripts/deploy_server.sh --no-registry"
        exit 1
    fi
else
    echo "Streaming image to $PROD_HOST over SSH (no registry)..."
    docker save "$IMAGE:$TAG" | gzip | $SSH "gunzip | docker load"
fi

sed -i '' "s|image: $IMAGE:.*|image: $IMAGE:$TAG|" docker-compose.prod.yml

echo "Copying docker-compose.prod.yml to $PROD_HOST:$PROD_DIR/docker-compose.yml..."
$SSH "mkdir -p $PROD_DIR"
scp -i "$SSH_KEY" docker-compose.prod.yml "$PROD_USER@$PROD_HOST:$PROD_DIR/docker-compose.yml"

echo "Deploying on $PROD_HOST..."
if [ "$USE_REGISTRY" = "1" ]; then
    $SSH "cd $PROD_DIR && docker compose pull server && docker compose up -d --force-recreate server postgres"
else
    $SSH "cd $PROD_DIR && docker compose up -d --force-recreate server postgres"
fi

if [ "$MIGRATE" = "1" ]; then
    echo "Running Alembic migrations on the server..."
    $SSH "cd $PROD_DIR && docker compose run --rm server alembic upgrade head"
fi

echo ""
echo "Deployed $IMAGE:$TAG to $PROD_HOST"
echo ""
echo "Check it came up clean:"
echo "  $SSH \"cd $PROD_DIR && docker compose logs --tail=40 server\""
echo "  curl https://api.heyalice.help/health"
