from fastapi import FastAPI
from app.webhook import github_webhook
from dotenv import load_dotenv
load_dotenv()

app = FastAPI()
app.include_router(github_webhook)