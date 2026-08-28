"""Project app-config helpers.

Database aliases are selected with ``QuerySet.using()`` and by the database
router. AppConfig is global to the Django process, so it must not assign a
MongoDB-only primary-key field to Django's built-in apps.
"""

from django.apps import AppConfig


class ConfigConfig(AppConfig):
    default_auto_field = "django.db.models.AutoField"
    name = "config"
