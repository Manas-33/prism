from fastapi import FastAPI, Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from app.webhook import github_webhook
from dotenv import load_dotenv
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
load_dotenv()

app = FastAPI()
app.include_router(github_webhook)


# Prometheus scrape endpoint (api process, default registry). Served as an
# explicit route (not a mount) so /metrics returns 200 directly with no
# trailing-slash redirect for the scraper to follow.
@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


logger.info("App started")