from celery import Celery
from app.github import post_pr_comment
from dotenv import load_dotenv

load_dotenv()

celery = Celery(
    "worker",
    broker="redis://redis:6379/0"
)
@celery.task
def analyze_pr(repo: str, pr_number: int):
    print(f"[WORKER] Analyzing PR {repo}#{pr_number}")
    post_pr_comment(
        repo,
        pr_number,
        "👋 PR received! Analysis has been queued."
    )