import os
from fastapi import APIRouter, Request, HTTPException
import hmac, hashlib
from worker.tasks import analyze_pr

github_webhook = APIRouter()
GITHUB_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")

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
    
    if not signature or not verify_signature(payload,signature):
        raise HTTPException(status_code=403,detail="Invalid Signature")
    
    event = request.headers.get("X-GitHub-Event")
    if event != "pull_request":
        return {"status": "ignored"}
    
    data = await request.json()
    
    # only handle when PR is opened
    if data.get("action") != "opened":
        return {"status": "ignored"}
    
    repo = data["repository"]["full_name"]
    pr_number = data["pull_request"]["number"]
    
    # send request to celery worker
    analyze_pr.delay(repo,pr_number)
    return {"status":"queued"}