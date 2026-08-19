"""Enable the pgvector extension.

On Neon the `vector` extension is provisioned out of band, but this migration
guarantees it exists before any vector column or HNSW index is created — a
safety net for fresh local PostgreSQL databases. It is ordered to run *before*
the initial schema migration via ``run_before``.
"""

from django.db import migrations
from pgvector.django import VectorExtension


class Migration(migrations.Migration):

    dependencies = []

    run_before = [
        ("procurement", "0001_initial"),
    ]

    operations = [
        VectorExtension(),
    ]
