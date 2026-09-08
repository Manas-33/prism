"""
Prometheus metrics for PRism.

One module, imported by both the FastAPI `api` process and the Celery `worker`
process. Each process only touches the metrics it needs; the rest stay at zero
on that process's exposition.

Multiprocess: the worker runs a prefork pool, so tasks execute in child
processes. The worker container sets PROMETHEUS_MULTIPROC_DIR, which makes
prometheus_client write samples to that directory; `start_metrics_server` then
serves a registry that aggregates across all worker processes. The single
uvicorn `api` process leaves the env unset and serves the default registry via
`/metrics` (see app.main).
"""
import os
import time
import logging
from contextlib import contextmanager

from prometheus_client import Counter, Histogram, CollectorRegistry

logger = logging.getLogger(__name__)

# The multiprocess dir must exist before any metric is created, so do it here at
# import time (the env is set per-process, before this module is imported).
_MULTIPROC_DIR = os.getenv("PROMETHEUS_MULTIPROC_DIR")
if _MULTIPROC_DIR:
    os.makedirs(_MULTIPROC_DIR, exist_ok=True)

# ---- Webhook (api process) ----
WEBHOOK_EVENTS = Counter(
    "prism_webhook_events_total",
    "Webhook deliveries received, by outcome.",
    ["outcome"],  # queued | duplicate | ignored | invalid
)

# ---- Celery tasks (worker process) ----
TASK_TOTAL = Counter(
    "prism_task_total", "Celery tasks run, by name and status.", ["task", "status"]
)
TASK_DURATION = Histogram(
    "prism_task_duration_seconds", "Celery task wall-clock duration.", ["task"]
)

# ---- Domain metrics (worker process) ----
CACHE_REQUESTS = Counter(
    "prism_cache_requests_total",
    "Redis cache lookups, by cache type and result.",
    ["type", "result"],  # type: graph|summary|rules ; result: hit|miss
)
LLM_CALLS = Counter(
    "prism_llm_calls_total",
    "Generative API calls, by kind and status.",
    ["kind", "status"],  # kind: generate|embed ; status: success|error
)
LLM_LATENCY = Histogram(
    "prism_llm_latency_seconds", "Generative API call latency.", ["kind"]
)
ANALYSIS_DURATION = Histogram(
    "prism_analysis_duration_seconds", "analyze_impacts wall-clock duration."
)
IMPACTS_DETECTED = Histogram(
    "prism_impacts_detected",
    "Impacts detected per analysis run.",
    buckets=(0, 1, 2, 5, 10, 20, 50, 100),
)


@contextmanager
def track_call(kind: str):
    """Time a generative API call and record its success/error status."""
    start = time.perf_counter()
    try:
        yield
        LLM_CALLS.labels(kind, "success").inc()
    except Exception:
        LLM_CALLS.labels(kind, "error").inc()
        raise
    finally:
        LLM_LATENCY.labels(kind).observe(time.perf_counter() - start)


def record_cache(key: str, hit: bool) -> None:
    """Record a cache lookup, deriving the cache type from the key prefix."""
    cache_type = key.split(":", 1)[0] if key else "unknown"
    CACHE_REQUESTS.labels(cache_type, "hit" if hit else "miss").inc()


def start_metrics_server(port: int = 9808) -> None:
    """Expose worker metrics on `port` for Prometheus. Multiprocess-aware."""
    from prometheus_client import start_http_server
    if _MULTIPROC_DIR:
        from prometheus_client import multiprocess
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        start_http_server(port, registry=registry)
    else:
        start_http_server(port)
    logger.info("Worker metrics server listening on :%d", port)
