# Policy RAG Design

## Corpus

`PolicyDocument` rows hold procurement policies (spending limits, approved-vendor
rules, category rules, sole-source rules, compliance policies). Each has a
`policy_type`, `content`, and a 768-dimension `embedding` (`pgvector.VectorField`).

## Index

An HNSW index (`vector_cosine_ops`, `m=16`, `ef_construction=64`) is declared on
the `embedding` field for fast approximate cosine search directly in Postgres.
The `vector` extension is enabled by migration `0002_pgvector_extension`, which
runs before the initial schema via `run_before`.

## Embedding pipeline

- **On create:** a `post_save` signal enqueues `embed_policy_document_task`
  (Celery). The task calls Vertex AI `text-embedding-004` and stores the vector.
- **Bulk backfill:** `python manage.py embed_policies` queues tasks for all
  documents with a null embedding; `--sync` embeds inline (used for one-off
  backfills and deterministic verification without a worker); `--all` re-embeds
  everything.
- A single Vertex client is shared process-wide (`procurement/embeddings.py`).

## Retrieval

`agent/retriever.py::retrieve_policy_context(query, k, policy_types)`:

1. Embed the query with the same model.
2. Annotate the corpus with `CosineDistance('embedding', query_vector)`.
3. Order ascending by distance, take top-k.
4. Return `{title, policy_type, content_snippet, similarity_score}` where
   `similarity_score = 1 − cosine_distance`.

`policy_types` optionally restricts the search to specific categories.

## In the loop

The agent's `retrieve_policy_context` node runs **before** reasoning on each turn,
injecting a system message with the retrieved snippets so the model grounds tool
selection in policy. The `check_policy_compliance` tool exposes the same
retrieval for explicit compliance checks, attaching citations to created POs and
the audit ledger.
