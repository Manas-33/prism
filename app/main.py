from fastapi import FastAPI
from app.webhook import github_webhook

app = FastAPI()
app.include_router(github_webhook)