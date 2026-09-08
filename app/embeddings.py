"""
Gemini embeddings wrapper (Stage B).

Uses the same google-genai client as app.api_service so ingestion and retrieval
share one SDK and key. `gemini-embedding-001` is used over the deprecated
`text-embedding-004`; a 768-d output is plenty for short rule snippets. Rules
are embedded with task_type RETRIEVAL_DOCUMENT and queries with RETRIEVAL_QUERY,
the asymmetric setup Gemini recommends for retrieval. Qdrant scores these with
cosine distance, so the vectors need no manual normalization.
"""
import os
import logging
from typing import List

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 768


def _client() -> genai.Client:
    return genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def _embed(texts: List[str], task_type: str) -> List[List[float]]:
    # Bind the client to a local so it isn't garbage-collected (which closes its
    # httpx client) mid-request — matches the pattern in app.api_service.
    client = _client()
    resp = client.models.embed_content(
        model=EMBED_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=EMBED_DIM,
        ),
    )
    return [e.values for e in resp.embeddings]


def embed_documents(texts: List[str]) -> List[List[float]]:
    """Embed rule texts for storage (task_type RETRIEVAL_DOCUMENT)."""
    return _embed(texts, "RETRIEVAL_DOCUMENT")


def embed_query(text: str) -> List[float]:
    """Embed a single query string (task_type RETRIEVAL_QUERY)."""
    return _embed([text], "RETRIEVAL_QUERY")[0]
