from celery import Celery
from app.github import post_pr_comment
from dotenv import load_dotenv
import logging

load_dotenv()
logger = logging.getLogger(__name__)

celery = Celery(
    "worker",
    broker="redis://redis:6379/0"
)
@celery.task
def analyze_pr(repo: str, pr_number: int):
    logger.info("Analyzing PR", extra={"repo": repo, "pr": pr_number})
    post_pr_comment(
        repo,
        pr_number,
        "👋 PR received! Analysis has been queued."
    )