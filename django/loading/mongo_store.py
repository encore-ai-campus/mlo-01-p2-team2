"""Backward-compatible imports for the second_project Mongo repository."""

from second_project.repository.mongodb_repository import (
    ChecksumConflictError,
    MongoDependencyError,
    MongoRepository,
    MongoStoreError,
    RawWriteResult,
)

MongoStore = MongoRepository

__all__ = [
    "ChecksumConflictError",
    "MongoDependencyError",
    "MongoRepository",
    "MongoStore",
    "MongoStoreError",
    "RawWriteResult",
]
