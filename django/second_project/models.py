"""Django model exports for the second_project app.

Model classes are defined once in the repository layer and re-exported here
because Django imports this conventional module during app initialization.
"""

from .repository.models import (
    BronzeRawRecord,
    LegacyOrgRecord,
    SilverArea,
    SilverEmployee,
    SilverMetadata,
    SilverParentArea,
    SilverTopAreaDetail,
)


__all__ = [
    "BronzeRawRecord",
    "LegacyOrgRecord",
    "SilverArea",
    "SilverEmployee",
    "SilverMetadata",
    "SilverParentArea",
    "SilverTopAreaDetail",
]
