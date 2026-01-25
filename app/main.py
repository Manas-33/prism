from fastapi import FastAPI
from app.webhook import github_webhook
from dotenv import load_dotenv
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
load_dotenv()

app = FastAPI()
app.include_router(github_webhook)
logger.info("App started")