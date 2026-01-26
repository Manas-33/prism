from celery import Celery
from app.github import post_pr_comment
from dotenv import load_dotenv
import logging
import uuid 
from app.workspace import job_workspace
from app.git_ops import clone_and_analyze_pr
load_dotenv()
logger = logging.getLogger(__name__)

celery = Celery(
    "worker",
    broker="redis://redis:6379/0"
)

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