#!/bin/bash

# Default values
TAG="latest"
DATA_PATH="."

# Help message
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo "Options:"
    echo "  --tag TAG       Image tag to deploy (default: latest)"
    echo "  --data PATH      Host path for data volume (default: ./data)"
    echo "  --help           Show this help message"
    exit 1
}

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --tag) TAG="$2"; shift ;;
        --data) DATA_PATH="$2"; shift ;;
        --help) usage ;;
        *) echo "Unknown parameter passed: $1"; usage ;;
    esac
    shift
done

echo "🚀 Deploying LFM with TAG=$TAG and DATA_PATH=$DATA_PATH"

# Export variables for docker-compose
export TAG
export DATA_MOUNT_PATH="$DATA_PATH"

# Run docker-compose
docker compose up -d

echo "✅ Deployment requested. Check status with 'docker compose ps'"
