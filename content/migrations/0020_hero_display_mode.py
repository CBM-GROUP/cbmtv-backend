from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('content', '0019_mediaasset')]

    operations = [
        migrations.AlterField(model_name='herosettings', name='image_duration_seconds', field=models.PositiveSmallIntegerField(default=5)),
        migrations.AddField(model_name='herosettings', name='display_mode', field=models.CharField(max_length=20, default='thumbnail_trailer', choices=[('thumbnails_only', 'Thumbnails only'), ('thumbnail_trailer', 'Thumbnail + trailer')])),
        migrations.AddField(model_name='herosettings', name='trailer_preview_seconds', field=models.PositiveSmallIntegerField(default=20)),
    ]
