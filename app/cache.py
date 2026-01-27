import json
from app.redis_client import redis_client

CACHE_TTL = 3600 # 1 hour

def cache_get(key: str):
    raw = redis_client.get(key)
    return None if raw is None else json.loads(raw)

def cache_set(key: str, value, ttl: int = CACHE_TTL):
    raw = json.dumps(value)
    redis_client.set(key, raw, ex=ttl)
