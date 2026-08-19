"""Vertex AI embedding client (Gemini ``text-embedding-004``, 768 dimensions).

A single lazily-instantiated client is shared across the embedding task and the
policy retriever. Configuration comes from Django settings / environment.
"""

import os
import threading

from django.conf import settings

_client = None
_lock = threading.Lock()


def _ensure_credentials():
    """Point the Google client at the service-account file if provided."""
    cred = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if cred and not os.path.isabs(cred):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(cred)


def get_embeddings_client():
    """Return a process-wide VertexAIEmbeddings client (thread-safe singleton)."""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _ensure_credentials()
                from langchain_google_vertexai import VertexAIEmbeddings

                _client = VertexAIEmbeddings(
                    model_name=getattr(settings, "EMBEDDING_MODEL", "text-embedding-004"),
                    project=getattr(settings, "GOOGLE_CLOUD_PROJECT", None),
                    location=getattr(settings, "GOOGLE_CLOUD_LOCATION", "us-central1"),
                )
    return _client


def embed_text(text: str) -> list:
    """Embed a single string, returning a 768-dimension vector."""
    return get_embeddings_client().embed_query(text)


def embed_documents(texts: list) -> list:
    """Embed a batch of strings."""
    return get_embeddings_client().embed_documents(texts)
