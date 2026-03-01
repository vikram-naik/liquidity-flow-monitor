#!/bin/bash

# Default values
TAG="latest"
NO_CACHE=""

# Help message
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo "Options:"
    echo "  --tag TAG       Image tag to build (default: latest)"
    echo "  --no-cache      Force build without using cache"
    echo "  --help           Show this help message"
    exit 1
}

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --tag) TAG="$2"; shift ;;
        --no-cache) NO_CACHE="--no-cache" ;;
        --help) usage ;;
        *) echo "Unknown parameter passed: $1"; usage ;;
    esac
    shift
done

IMAGE_NAME="vjdev/lfm-app:$TAG"

echo "🔨 Building Docker image: $IMAGE_NAME"

# Build the image
docker build $NO_CACHE -t "$IMAGE_NAME" .

if [ $? -eq 0 ]; then
    echo "✅ Build successful: $IMAGE_NAME"
    echo "📤 Pushing to repository..."
    docker push "$IMAGE_NAME"
    if [ $? -eq 0 ]; then
        echo "✅ Push successful"
    else
        echo "❌ Push failed"
        exit 1
    fi
else
    echo "❌ Build failed"
    exit 1
fi
