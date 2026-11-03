from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('content', '0016_content_cast')]
    operations = [
        migrations.AddField(model_name='content', name='first_published_at', field=models.DateTimeField(blank=True, db_index=True, editable=False, null=True)),
        migrations.AddField(model_name='content', name='publication_history_unknown', field=models.BooleanField(default=False, editable=False)),
    ]
