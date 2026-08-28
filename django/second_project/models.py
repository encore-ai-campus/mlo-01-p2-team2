"""Django model exports for the second_project app.

<<<<<<< HEAD
# Keep the model definition in the repository layer.  Re-exporting it from
# the conventional Django models module ensures Django imports one model
# class instead of registering a duplicate BronzeRawRecord.
=======
Model classes are defined once in the repository layer and re-exported here
because Django imports this conventional module during app initialization.
"""

>>>>>>> bd73a7194037f4dd6ccbabb8203a013d42d02be5
from .repository.models import (
    BronzeRawRecord,
    LegacyOrgRecord,
    SilverArea,
    SilverEmployee,
<<<<<<< HEAD
    SilverParentArea,
    SilverTopAreaDetail,
    SqliteSyncRun,
=======
    SilverMetadata,
    SilverParentArea,
    SilverTopAreaDetail,
>>>>>>> bd73a7194037f4dd6ccbabb8203a013d42d02be5
)


__all__ = [
    "BronzeRawRecord",
    "LegacyOrgRecord",
    "SilverArea",
    "SilverEmployee",
<<<<<<< HEAD
    "SilverParentArea",
    "SilverTopAreaDetail",
    "SqliteSyncRun",
=======
    "SilverMetadata",
    "SilverParentArea",
    "SilverTopAreaDetail",
>>>>>>> bd73a7194037f4dd6ccbabb8203a013d42d02be5
]
