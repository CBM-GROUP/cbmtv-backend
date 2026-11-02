from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounts', '0011_user_google_subject')]

    operations = [
        migrations.AddField(
            model_name='user',
            name='session_version',
            field=models.PositiveIntegerField(default=0, editable=False),
        ),
    ]
