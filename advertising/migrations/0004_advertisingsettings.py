from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('advertising', '0003_creative_promoted_contents')]

    operations = [migrations.CreateModel(
        name='AdvertisingSettings',
        fields=[
            ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
            ('enabled', models.BooleanField(default=False)),
            ('homepage_enabled', models.BooleanField(default=False)),
            ('video_enabled', models.BooleanField(default=False)),
            ('updated_at', models.DateTimeField(auto_now=True)),
        ],
    )]
