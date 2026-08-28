from django.test import SimpleTestCase

from config.database_router import ProjectDatabaseRouter
from second_project.models import SilverEmployee
from second_project.repository.models import BronzeRawRecord


class ProjectDatabaseRouterTests(SimpleTestCase):
    def setUp(self) -> None:
        self.router = ProjectDatabaseRouter()

    def test_bronze_and_silver_models_use_separate_databases(self) -> None:
        self.assertEqual(self.router.db_for_read(BronzeRawRecord), "mongodb")
        self.assertEqual(self.router.db_for_write(BronzeRawRecord), "mongodb")
        self.assertEqual(self.router.db_for_read(SilverEmployee), "default")

    def test_model_migrations_are_limited_to_their_database(self) -> None:
        self.assertTrue(
            self.router.allow_migrate(
                "mongodb",
                "second_project",
                model_name="bronzerawrecord",
            )
        )
        self.assertFalse(
            self.router.allow_migrate(
                "default",
                "second_project",
                model_name="bronzerawrecord",
            )
        )
        self.assertFalse(
            self.router.allow_migrate(
                "mongodb",
                "second_project",
                model_name="silveremployee",
            )
        )

    def test_run_python_operation_can_apply_its_own_alias_guard(self) -> None:
        self.assertIsNone(
            self.router.allow_migrate(
                "mongodb",
                "second_project",
                model_name=None,
            )
        )

    def test_framework_tables_are_not_migrated_to_mongodb(self) -> None:
        self.assertFalse(
            self.router.allow_migrate(
                "mongodb",
                "auth",
                model_name="user",
            )
        )
