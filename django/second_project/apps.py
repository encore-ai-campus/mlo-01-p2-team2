from django.apps import AppConfig


class SecondProjectConfig(AppConfig):
    name = "second_project"

    def ready(self) -> None:
        # The model module lives in the repository package so that the app's
        # MongoDB schema and queries stay together.  Importing it registers
        # the model with Django; it does not connect to MongoDB at startup.
        from .repository import models  # noqa: F401, PLC0415
