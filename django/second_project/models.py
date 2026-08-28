"""Public model exports for the ``second_project`` Django app.

The concrete model definitions live in ``repository.models``.  Keeping this
module as an export layer lets Django discover the app's models through its
normal ``models`` import without registering a second copy of each model.
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
