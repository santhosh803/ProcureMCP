from django.apps import AppConfig


class ProcurementConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'procurement'

    def ready(self):
        # Register signal handlers (audit ledger, embedding trigger).
        from . import signals  # noqa: F401
