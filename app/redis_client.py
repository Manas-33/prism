import redis
import os
import logging

logger = logging.getLogger(__name__)

redis_client = redis.Redis(
    host = os.getenv("REDIS_HOST", "localhost"),
    port = int(os.getenv("REDIS_PORT", 6379)),
    decode_responses=True
)
logger.info("Redis client initialized")