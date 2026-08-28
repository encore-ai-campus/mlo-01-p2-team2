from django.db import migrations


class Migration(migrations.Migration):
    """Join migration leaves created independently on Develop and Gold."""

    dependencies = [
        ("second_project", "0003_merge_bronze_and_legacy"),
        ("second_project", "0003_merge_bronze_legacy"),
        ("second_project", "0004_silveremployee_profile_image_url"),
    ]

    operations = []
