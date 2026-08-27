"""Django model exports for the second_project app."""

# Keep the model definition in the repository layer.  Re-exporting it from
# the conventional Django models module ensures Django imports one model
# class instead of registering a duplicate BronzeRawRecord.
from .repository.models import BronzeRawRecord


__all__ = ["BronzeRawRecord"]
