import os
from fastapi import APIRouter, Request, HTTPException
import hmac, hashlib

github_webhook = APIRouter()
GITHUB_SECRET = os.environ["GITHUB_WEBHOOK_SECRET"]

# Verify GitHub signature
def verify_signature(payload:bytes,signature:str):
    mac = hmac.new(GITHUB_SECRET.encode(),
                   msg=payload,
                   digestmod=hashlib.sha256)
    expected = "sha256="+mac.hexdigest(expected,signature)
    return hmac.compare_digest(expected, signature)

@github_webhook.post("/webhook/github")
async def handle_webhook(request: Request):
    payload = await request.body()
    signature  = request.headers.get("X-Hub-Signature-256")
    
    if not signature or not verify_signature(payload,signature):
        raise HTTPException(status_code=403,detail="Invalid Signature")
    
    data = await request.json()
    
    # only handle when PR is opened
    if data.get("action") != "opened":
        return {"status": "ignored"}
    
    repo = data["repository"]["full_name"]
    pr_number = data["pull_request"]["number"]
    
    # send request to celery worker
    
    return {"status":"queued"}