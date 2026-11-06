from django.db import migrations


def seed(apps, schema_editor):
    Placement = apps.get_model('advertising', 'Placement')
    for code in ('homepage_banner', 'homepage_sponsored_section', 'homepage_featured_sponsor',
                 'homepage_channel_promotion', 'homepage_content_promotion', 'channel_sponsor',
                 'content_sponsor', 'preroll', 'midroll', 'postroll'):
        Placement.objects.get_or_create(code=code, defaults={'enabled': False})


class Migration(migrations.Migration):
    dependencies = [('advertising', '0001_initial')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
