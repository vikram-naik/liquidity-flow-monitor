#!/bin/bash

# Default values
TAG="latest"
PUSH=true
PLATFORM="linux/amd64" # Default for standard AWS EC2 instances

# Help message
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo "Options:"
    echo "  --tag TAG        Image tag to build (default: latest)"
    echo "  --no-push        Build only, do not push to Docker Hub"
    echo "  --platform PLAT  Specify build platform (default: linux/amd64)"
    echo "  --help           Show this help message"
    exit 1
}

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --tag) TAG="$2"; shift ;;
        --no-push) PUSH=false ;;
        --platform) PLATFORM="$2"; shift ;;
        --help) usage ;;
        *) echo "Unknown parameter passed: $1"; usage ;;
    esac
    shift
done

IMAGE_NAME="vjdev/lfm-app:$TAG"

echo "🔨 Building Docker image: $IMAGE_NAME for platform $PLATFORM"

# Build the image targeting specific architecture for EC2 compatibility
docker build --platform "$PLATFORM" -t "$IMAGE_NAME" .

if [ $? -eq 0 ]; then
    echo "✅ Build successful: $IMAGE_NAME"
    
    if [ "$PUSH" = true ]; then
        echo "📤 Pushing to repository..."
        docker push "$IMAGE_NAME"
        if [ $? -eq 0 ]; then
            echo "✅ Push successful"
        else
            echo "❌ Push failed"
            exit 1
        fi
    else
        echo "⏭️  Skipping push as --no-push was specified."
    fi
else
    echo "❌ Build failed"
    exit 1
fi
