from django.db import migrations


def backfill(apps, schema_editor):
    Creative = apps.get_model('advertising', 'Creative')
    alias = schema_editor.connection.alias
    rows = Creative.objects.using(alias).select_related('campaign').order_by('pk').iterator(chunk_size=500)
    for creative in rows:
        if creative.campaign_id is None:
            if creative.advertiser_id is None:
                raise RuntimeError(f'Creative {creative.pk} has neither client nor campaign.')
            continue
        expected = creative.campaign.advertiser_id
        if creative.advertiser_id is not None and creative.advertiser_id != expected:
            raise RuntimeError(f'Creative {creative.pk} has mismatched client and campaign ownership.')
        if creative.advertiser_id is None:
            Creative.objects.using(alias).filter(pk=creative.pk, advertiser__isnull=True).update(advertiser_id=expected)
    if Creative.objects.using(alias).filter(advertiser__isnull=True).exists():
        raise RuntimeError('Creative ownership backfill is incomplete.')


class Migration(migrations.Migration):
    dependencies = [('advertising', '0004_advertisingoperation_and_more')]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
