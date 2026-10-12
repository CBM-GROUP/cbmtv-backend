from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounts', '0010_dashboard_roles_and_invitations')]

    operations = [
        migrations.AddField(
            model_name='user',
            name='google_subject',
            field=models.CharField(max_length=255, unique=True, null=True, blank=True),
        ),
    ]
