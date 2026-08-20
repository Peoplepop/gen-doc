from django.db import models


class HealthCheckPing(models.Model):
    """Trivial model that exists only to prove the DB connection + migration
    mechanism works end to end (T1 skeleton).

    It intentionally carries no business meaning — the real domain model
    (User / Project / FeatureNode / ...) arrives in later tickets (T2+).
    """

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"HealthCheckPing({self.pk}) @ {self.created_at:%Y-%m-%d %H:%M:%S}"
