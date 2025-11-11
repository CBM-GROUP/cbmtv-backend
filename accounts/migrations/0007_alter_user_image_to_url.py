from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_user_auth_provider'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='image',
            field=models.URLField(blank=True, max_length=255, null=True),
        ),
    ]


