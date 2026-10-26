from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(
        name='CreationOperation',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('key', models.UUIDField()),
            ('endpoint', models.CharField(max_length=160)),
            ('payload_digest', models.CharField(max_length=64)),
            ('record_id', models.PositiveBigIntegerField(null=True)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('actor', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ],
        options={'constraints': [models.UniqueConstraint(fields=('actor', 'key'), name='creation_actor_key_unique')]},
    )]
