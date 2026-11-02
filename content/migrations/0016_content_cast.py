from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('content', '0015_content_taxonomy')]

    operations = [
        migrations.AddField(
            model_name='content',
            name='cast',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
