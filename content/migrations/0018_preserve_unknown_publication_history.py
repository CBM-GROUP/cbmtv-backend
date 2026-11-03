from django.db import migrations


def preserve_unknown(apps, schema_editor):
    # No reliable historical publication log exists. Neither created_at nor
    # status can establish when a legacy title was first published.
    apps.get_model('content', 'Content').objects.using(schema_editor.connection.alias).filter(
        first_published_at__isnull=True,
    ).update(publication_history_unknown=True)


class Migration(migrations.Migration):
    dependencies = [('content', '0017_content_first_published_at_and_more')]
    operations = [migrations.RunPython(preserve_unknown, migrations.RunPython.noop)]
