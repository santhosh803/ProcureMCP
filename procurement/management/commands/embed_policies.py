"""Backfill embeddings for policy documents that have none.

By default this queues a Celery task per document (processed by a running
worker). Pass --sync to embed inline in the current process, which is handy for
one-off backfills, container start-up, and deterministic verification without a
worker.
"""

from django.core.management.base import BaseCommand

from procurement.models import PolicyDocument
from procurement.tasks import _embed_policy, embed_policy_document_task


class Command(BaseCommand):
    help = "Embed policy documents whose embedding is NULL."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Embed inline instead of queuing Celery tasks.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Re-embed every policy document, not just missing ones.",
        )

    def handle(self, *args, **options):
        qs = PolicyDocument.objects.all()
        if not options["all"]:
            qs = qs.filter(embedding__isnull=True)

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.WARNING("No policy documents need embedding."))
            return

        self.stdout.write(f"Embedding {total} policy document(s) ({'sync' if options['sync'] else 'queued'})…")
        done = 0
        for policy in qs.iterator():
            if options["sync"]:
                _embed_policy(policy)
                done += 1
                self.stdout.write(f"  embedded: {policy.title}")
            else:
                embed_policy_document_task.delay(policy.id)
                done += 1
                self.stdout.write(f"  queued: {policy.title}")

        verb = "Embedded" if options["sync"] else "Queued"
        self.stdout.write(self.style.SUCCESS(f"{verb} {done} policy document(s)."))
