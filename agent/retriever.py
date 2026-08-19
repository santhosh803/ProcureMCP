"""pgvector-backed policy retrieval.

Embeds a natural-language query with the same Vertex AI model used for the
corpus, then runs an HNSW cosine-similarity search over PolicyDocument.embedding
using pgvector's Django integration.
"""

from pgvector.django import CosineDistance

from procurement.embeddings import embed_text
from procurement.models import PolicyDocument


def retrieve_policy_context(query: str, k: int = 5, policy_types=None):
    """Return the top-k most relevant policy snippets for a query.

    Each result: ``{title, policy_type, content_snippet, similarity_score}``,
    ordered by descending cosine similarity. ``policy_types`` optionally filters
    the corpus to specific PolicyDocument.PolicyType values.
    """
    query_vector = embed_text(query)

    qs = PolicyDocument.objects.exclude(embedding__isnull=True)
    if policy_types:
        qs = qs.filter(policy_type__in=policy_types)

    results = (
        qs.annotate(distance=CosineDistance("embedding", query_vector))
        .order_by("distance")[:k]
    )

    snippets = []
    for policy in results:
        content = policy.content
        snippet = content if len(content) <= 320 else content[:317] + "…"
        # Cosine similarity = 1 - cosine distance.
        similarity = round(1.0 - float(policy.distance), 4)
        snippets.append(
            {
                "id": policy.id,
                "title": policy.title,
                "policy_type": policy.policy_type,
                "content_snippet": snippet,
                "similarity_score": similarity,
            }
        )
    return snippets
