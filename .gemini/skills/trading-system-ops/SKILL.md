---
name: trading-system-ops
description: Operational procedures for managing the LFM production environment, including daily synchronization, backtest integration, and Docker-aware configuration.
---

# Trading System Lifecycle Operations

This skill codifies the procedures for maintaining and synchronizing the LFM production environment, especially when running inside a containerised (Docker) architecture.

## 1. Daily EOD Synchronization Loop

The system relies on a daily synchronization process that updates data, flushes stale caches, and warms up the system for the next trading day.

### Standard Cron Integration
The host-side cron job (`scripts/cron_daily_sync.sh`) should orchestrate the following inside the container:
1. **Data Sync**: Run `scripts/daily_sync.sh`.
2. **Performance Validation**: Immediately follow with `scripts/walk_forward.py --watchlist "NIFTY 50"`.
3. **Cache Warm-up**: The `walk_forward.py` run serves to pre-compute and cache all markers/features for the primary watchlist, ensuring fast UI response times the next morning.

```bash
# Recommended logic in cron_daily_sync.sh
if [ $EXIT_CODE -eq 0 ]; then
    echo "Starting automated backtest and cache warm-up..."
    docker exec "$CONTAINER_NAME" python /app/scripts/walk_forward.py --watchlist "NIFTY 50"
fi
```

## 2. Docker-Aware Configuration

When scripts run both on the host (for research) and inside a container (for production), adhere to these implementation rules.

### Robust Environment Detection (Bash)
Use this specific pattern in shell scripts to reliably detect if they are running inside a container, avoiding bash precedence pitfalls (`&&` / `||` order):

```bash
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
elif [ -f "/.dockerenv" ] || { [ -f "/proc/1/cgroup" ] && grep -q "docker" /proc/1/cgroup; }; then
    echo "Detected Docker environment, skipping venv activation."
else
    echo "Error: Virtual environment not found."
    exit 1
fi
```

### Database Path Management (Python)
NEVER hardcode the database path (e.g., `liquidity_monitor.db`). Always import `DB_PATH` from `src.database` to ensure the script respects the `DB_PATH` environment variable set in the `Dockerfile` or `docker-compose.yml`.

```python
# GOOD
from src.database import DB_PATH
db = sqlite3.connect(str(DB_PATH))

# BAD
DB_PATH = Path(__file__).resolve().parent.parent / "liquidity_monitor.db"
```

## 3. Deployment Hygiene

- **Image Size Optimization**: In the `Dockerfile`, purge unused library components (like Nvidia GPU libs bundled with `xgboost`) and remove test directories after installation to keep the image slim.
- **Cache Flushing**: ALWAYS run `scripts/flush_cache.py --all` after any code changes to `DivergenceEngine` modules or signal scoring logic to prevent stale marker collisions.
