#!/usr/bin/env bash
# LLTPM Deployment Script
# Usage: ./deploy/deploy.sh [target]
#   target: docker | cloudrun | local

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TARGET="${1:-docker}"

echo "LLTPM Deployment — target: $TARGET"

case "$TARGET" in
  docker)
    echo "Building Docker image..."
    docker build -t lltpm:latest "$PROJECT_DIR"
    echo ""
    echo "Starting LLTPM..."
    docker compose -f "$PROJECT_DIR/docker-compose.yml" up -d lltpm
    echo ""
    echo "LLTPM is running at http://localhost:8080"
    echo "API docs at http://localhost:8080/docs"
    echo ""
    echo "To view logs: docker compose logs -f lltpm"
    echo "To stop: docker compose down"
    ;;

  cloudrun)
    echo "Deploying to Google Cloud Run..."

    # Check for gcloud
    if ! command -v gcloud &> /dev/null; then
      echo "Error: gcloud CLI not found. Install from https://cloud.google.com/sdk"
      exit 1
    fi

    PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
    if [ -z "$PROJECT_ID" ]; then
      echo "Error: No GCP project set. Run: gcloud config set project YOUR_PROJECT"
      exit 1
    fi

    IMAGE="gcr.io/$PROJECT_ID/lltpm:latest"

    echo "Building and pushing image..."
    docker build -t "$IMAGE" "$PROJECT_DIR"
    docker push "$IMAGE"

    echo "Deploying service..."
    sed "s/PROJECT_ID/$PROJECT_ID/g" "$SCRIPT_DIR/cloudrun.yaml" | \
      gcloud run services replace - --region=us-central1

    echo ""
    echo "Deployment complete. Get URL with:"
    echo "  gcloud run services describe lltpm --region=us-central1 --format='value(status.url)'"
    ;;

  local)
    echo "Starting local development server..."
    cd "$PROJECT_DIR"
    pip install -e ".[dashboard,dev]" 2>/dev/null || true
    python -m uvicorn lltpm.dashboard:create_app --factory --reload --host 0.0.0.0 --port 8080
    ;;

  *)
    echo "Unknown target: $TARGET"
    echo "Usage: ./deploy/deploy.sh [docker|cloudrun|local]"
    exit 1
    ;;
esac
