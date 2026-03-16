#!/usr/bin/env python3
"""
Utility script to flush Redis cache keys matching a pattern.
Default pattern is 'de:*' (Divergence Engine results).
"""

import sys
import argparse
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.cache.factory import get_cache

def main():
    parser = argparse.ArgumentParser(description="Flush Redis cache keys matching a pattern.")
    parser.add_argument(
        "--pattern", 
        type=str, 
        default="de:*", 
        help="Pattern to match keys (default: 'de:*')"
    )
    parser.add_argument(
        "--all", 
        action="store_true", 
        help="Flush ALL keys in the current Redis database"
    )
    
    args = parser.parse_args()
    
    cache = get_cache()
    if not hasattr(cache, 'client') or cache.client is None:
        print("Error: Could not connect to Redis cache.")
        sys.exit(1)
        
    if args.all:
        print("Clearing entire Redis database...")
        success = cache.clear()
        if success:
            print("Successfully cleared all keys.")
        else:
            print("Failed to clear database.")
    else:
        print(f"Flushing keys matching pattern: {args.pattern}")
        count = cache.delete_pattern(args.pattern)
        print(f"Successfully deleted {count} keys.")

if __name__ == "__main__":
    main()
