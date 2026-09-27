from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('content', '0013_content_is_featured_content_is_trending'),
    ]

    operations = [
        migrations.AddField(
            model_name='contentadverts',
            name='show_in_hero',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='contentadverts',
            name='hero_order',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.CreateModel(
            name='HeroSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image_duration_seconds', models.PositiveSmallIntegerField(default=20)),
            ],
        ),
    ]
