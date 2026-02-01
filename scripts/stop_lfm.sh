#!/bin/bash

echo "🛑 Stopping LFM services..."

# Run docker-compose down
# This stops and removes containers, networks, and images created by 'up'
# Note: It does NOT remove volumes (lfm-data is safe)
docker compose down

echo "✅ Services stopped."
