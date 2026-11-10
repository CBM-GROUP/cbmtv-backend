from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [('homepage', '0001_initial')]

    operations = [
        migrations.AddField(model_name='homepageconfiguration', name='advert_insertion_enabled', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='homepageconfiguration', name='advert_interval', field=models.PositiveIntegerField(default=3)),
        migrations.AddConstraint(model_name='homepageconfiguration', constraint=models.CheckConstraint(condition=Q(advert_interval__gte=1), name='homepage_advert_interval_positive')),
    ]
