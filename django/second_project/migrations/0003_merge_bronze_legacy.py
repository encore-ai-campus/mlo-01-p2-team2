from django.db import migrations


class Migration(migrations.Migration):
    """Merge the independent Bronze and legacy SQLite migration branches."""

    dependencies = [
        ("second_project", "0002_bronze_mongodb"),
        ("second_project", "0002_legacy_org_record"),
    ]

    operations = []
