from django.apps import AppConfig


class SecurityMonitoringConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "security_monitoring"

    def ready(self) -> None:
        # Ensure signal handlers are registered.
        from . import signals  # noqa: F401

