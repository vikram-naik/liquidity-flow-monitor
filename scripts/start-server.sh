#!/bin/bash

# Check if redis-mfmds container is running
if [ ! "$(docker ps -q -f name=redis-mfmds -f status=running)" ]; then
    echo "Container 'redis-mfmds' is not running. Restarting..."
    docker restart redis-mfmds
else
    echo "Container 'redis-mfmds' is already running."
fi

# Start uvicorn server
echo "Starting uvicorn server..."
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
