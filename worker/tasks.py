from celery import Celery
from celery.signals import task_prerun, task_postrun, worker_init
from app.github import post_pr_comment
from dotenv import load_dotenv
import logging
import time
import uuid
from app.workspace import job_workspace
from app.pipeline import clone_and_analyze_pr
from app.metrics import TASK_TOTAL, TASK_DURATION, start_metrics_server
load_dotenv()
logger = logging.getLogger(__name__)

celery = Celery(
    "worker",
    broker="redis://redis:6379/0"
)

# Per-task start times, keyed by task id. task_prerun and task_postrun for a
# given task fire in the same (child) process, so a module dict is sufficient.
_task_starts: dict = {}


@worker_init.connect
def _start_metrics_server(**_kwargs):
    """Expose worker metrics once, from the main worker process."""
    start_metrics_server()


@task_prerun.connect
def _task_prerun(task_id=None, task=None, **_kwargs):
    _task_starts[task_id] = time.perf_counter()


@task_postrun.connect
def _task_postrun(task_id=None, task=None, state=None, **_kwargs):
    name = getattr(task, "name", "unknown")
    started = _task_starts.pop(task_id, None)
    if started is not None:
        TASK_DURATION.labels(name).observe(time.perf_counter() - started)
    TASK_TOTAL.labels(name, (state or "UNKNOWN").lower()).inc()


@celery.task
def analyze_pr(repo: str, pr_number: int):
    job_id = uuid.uuid4().hex
    with job_workspace(job_id) as workspace:
        logger.info(f"Using workspace {workspace}", extra={"path": workspace,"job_id":job_id,"pr":pr_number})
        summary = clone_and_analyze_pr(repo, pr_number, workspace)
        logger.info("Analyzing PR", extra={"repo": repo, "pr": pr_number})
        post_pr_comment(
            repo,
            pr_number,
            summary
        )
