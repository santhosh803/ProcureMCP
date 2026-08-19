"""Celery application for ProcureMCP.

IMPORTANT — shared Redis instance:
This project's ``REDIS_URL`` points to the SAME Upstash Redis database used by
the ReturnPilot project (the free tier allows only one database). The
``global_keyprefix`` below namespaces every Celery key this project writes so it
never collides with ReturnPilot's keys on the shared instance. This prefix is
mandatory and must not be removed or changed.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "procuremcp.settings")

app = Celery(
    "procuremcp",
    broker=os.environ.get("REDIS_URL"),
    backend=os.environ.get("REDIS_URL"),
)

# Namespace ALL keys on the shared Upstash instance. Do not remove.
app.conf.broker_transport_options = {"global_keyprefix": "procuremcp:"}
app.conf.result_backend_transport_options = {"global_keyprefix": "procuremcp:"}

app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
