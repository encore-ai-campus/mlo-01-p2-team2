from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("second_project", "0003_merge_bronze_silver"),
    ]

    operations = [
        migrations.AddField(
            model_name="silveremployee",
            name="profile_image_url",
            field=models.URLField(blank=True, max_length=2048, null=True),
        ),
    ]
