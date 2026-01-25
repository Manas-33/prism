import os
from fastapi import APIRouter, Request, HTTPException
import hmac, hashlib
from worker.tasks import analyze_pr
from app.redis_client import redis_client
import logging

github_webhook = APIRouter()
GITHUB_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")
logger = logging.getLogger(__name__)

# Verify GitHub signature
def verify_signature(payload: bytes, signature_header: str) -> bool:
    mac = hmac.new(
        GITHUB_SECRET.encode(),
        msg=payload,
        digestmod=hashlib.sha256,
    )
    expected_signature = "sha256=" + mac.hexdigest()
    return hmac.compare_digest(expected_signature, signature_header)

@github_webhook.post("/webhook/github")
async def handle_webhook(request: Request):
    payload = await request.body()
    signature  = request.headers.get("X-Hub-Signature-256")
    delivery_id = request.headers.get("X-GitHub-Delivery")
    event = request.headers.get("X-GitHub-Event")
    logger.info("Webhook received", extra={"delivery_id": delivery_id})
    if not signature or not verify_signature(payload,signature):
        logger.warning("Invalid signature", extra={"delivery_id": delivery_id})
        raise HTTPException(status_code=403,detail="Invalid Signature")
    if not delivery_id:
        logger.warning("Missing delivery id")
        raise HTTPException(status_code=400,detail="Missing Delivery ID")
    
    result = redis_client.set(delivery_id, "1", nx=True, ex=3600)

    if not result:
        logger.info("Duplicate webhook ignored", extra={"delivery_id": delivery_id})
        return {"status": "duplicate"}

    if event != "pull_request":
        logger.info("Non-PR event ignored", extra={"event": event})
        return {"status": "ignored"}
    
    data = await request.json()
    
    # only handle when PR is opened
    if data.get("action") != "opened":
        logger.info("Non-opened action ignored", extra={"action": data.get("action")})
        return {"status": "ignored"}
    
    repo = data["repository"]["full_name"]
    
    ALLOWED_ACTIONS = ["opened", "synchronize"]
    
    action = data.get("action")
    pr = data["pull_request"]
    pr_number = pr["number"]
    if action not in ALLOWED_ACTIONS:
        logger.info("Ignoring unsupported action", extra={"pr": pr_number, "repo": repo, "action": action})
        return {"status":"ignored"}
    
    if pr.get("draft", False):
        logger.info("Ignoring draft PR", extra={"pr": pr_number, "repo": repo})
        return {"status":"ignored"}
    
    if pr.get("merged", False):
        logger.info("Ignoring merged PR", extra={"pr": pr_number, "repo": repo})
        return {"status":"ignored"}
    
    if pr["head"]["repo"]["fork"]:
        logger.info("Ignoring PR from forked repo", extra={"pr": pr_number, "repo": repo})
        return {"status":"ignored fork PR"}
    
    # send request to celery worker
    logger.info("Queueing PR analysis", extra={"pr": pr_number, "repo": repo})
    analyze_pr.delay(repo,pr_number)
    return {"status":"queued"}