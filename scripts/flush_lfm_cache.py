import redis
import os
import argparse

def flush_lfm_cache(pattern="lfm:*"):
    host = os.getenv("REDIS_HOST", "172.17.0.1")
    port = int(os.getenv("REDIS_PORT", 6379))
    db = int(os.getenv("REDIS_DB", 0))
    
    print(f"Connecting to Redis at {host}:{port} (DB {db})...")
    r = redis.Redis(host=host, port=port, db=db)
    
    keys = r.keys(pattern)
    if not keys:
        print(f"No keys found matching pattern: {pattern}")
        return
    
    print(f"Found {len(keys)} keys matching '{pattern}'. Deleting...")
    # Use pipeline for efficiency if there are many keys
    pipe = r.pipeline()
    for key in keys:
        pipe.delete(key)
    pipe.execute()
    print("Done. Cache cleared for LFM.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Flush LFM specific cache from Redis.")
    parser.add_argument("--pattern", type=str, default="lfm:*", help="Pattern of keys to delete (default: lfm:*)")
    args = parser.parse_args()
    
    try:
        flush_lfm_cache(args.pattern)
    except Exception as e:
        print(f"Error: {e}")
