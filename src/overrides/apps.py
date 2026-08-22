from django.apps import AppConfig


class OverridesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'overrides'
    verbose_name = '內容覆寫'
