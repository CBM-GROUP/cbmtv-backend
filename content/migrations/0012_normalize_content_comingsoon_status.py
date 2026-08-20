from django.db import migrations, models


def normalize_comingsoon_status(apps, schema_editor):
    Content = apps.get_model("content", "Content")
    Content.objects.filter(status="comingsoon ").update(status="comingsoon")


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0011_alter_content_content_type_miniseries"),
    ]

    operations = [
        migrations.RunPython(normalize_comingsoon_status, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="content",
            name="status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("preview", "Preview"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                    ("comingsoon", "Comingsoon"),
                ],
                max_length=100,
                null=True,
            ),
        ),
    ]
