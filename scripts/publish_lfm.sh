#!/bin/bash
# scripts/publish_lfm.sh

# Defaults
DEFAULT_HOST="https://www.mfmunshi.com"
DEFAULT_USER="api_user"

# Help message
function show_help {
    echo "Usage: ./scripts/publish_lfm.sh [PASSWORD] [EXTRA_ARGS...]"
    echo ""
    echo "Arguments:"
    echo "  PASSWORD     (Required) The API Password"
    echo "  EXTRA_ARGS   Optional arguments passed to src/publisher.py (e.g., --backfill)"
    echo ""
    echo "Environment Variables (Overrides):"
    echo "  LFM_HOST     Default: $DEFAULT_HOST"
    echo "  LFM_USER     Default: $DEFAULT_USER"
    echo ""
    echo "Example:"
    echo "  ./scripts/publish_lfm.sh my_password_123"
    echo "  ./scripts/publish_lfm.sh my_password_123 --backfill"
    exit 1
}

if [ "$1" == "--help" ] || [ "$1" == "-h" ] || [ -z "$1" ]; then
    show_help
fi

PASS="$1"
shift  # Shift to get the rest as extra args

# Use env overrides if present
LFM_HOST="${LFM_HOST:-$DEFAULT_HOST}"
LFM_USER="${LFM_USER:-$DEFAULT_USER}"

echo "📡 Publishing data to ${LFM_HOST} as ${LFM_USER}..."

python3 src/publisher.py \
    --host "$LFM_HOST" \
    --user "$LFM_USER" \
    --pass "$PASS" \
    "$@"
