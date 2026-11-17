import importlib
from datetime import timedelta

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class CreativeOwnershipMigrationTests(TransactionTestCase):
    databases = {'default'}

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([('advertising', target)])
        return executor.loader.project_state([('advertising', target)]).apps

    def tearDown(self):
        self.migrate('0007_deliveryallocation_ad_allocation_unique_start')
        super().tearDown()

    def test_backfill_preserves_existing_creative_identifier(self):
        old = self.migrate('0003_creative_promoted_contents')
        Advertiser = old.get_model('advertising', 'Advertiser')
        Campaign = old.get_model('advertising', 'Campaign')
        Creative = old.get_model('advertising', 'Creative')
        advertiser = Advertiser.objects.create(name='Existing client')
        campaign = Campaign.objects.create(name='Existing campaign', advertiser=advertiser,
            starts_at=timezone.now(), ends_at=timezone.now() + timedelta(days=1))
        creative = Creative.objects.create(name='Existing creative', campaign=campaign, kind='image')
        current = self.migrate('0006_alter_creative_advertiser')
        migrated = current.get_model('advertising', 'Creative').objects.get(pk=creative.pk)
        self.assertEqual(migrated.advertiser_id, advertiser.pk)
        self.assertEqual(migrated.campaign_id, campaign.pk)

    def test_backfill_rejects_conflicting_ownership(self):
        old = self.migrate('0004_advertisingoperation_and_more')
        Advertiser = old.get_model('advertising', 'Advertiser')
        Campaign = old.get_model('advertising', 'Campaign')
        Creative = old.get_model('advertising', 'Creative')
        owner = Advertiser.objects.create(name='Owner')
        other = Advertiser.objects.create(name='Other')
        campaign = Campaign.objects.create(name='Campaign', advertiser=owner,
            starts_at=timezone.now(), ends_at=timezone.now() + timedelta(days=1))
        Creative.objects.create(name='Mismatch', campaign=campaign, advertiser=other, kind='image')
        backfill = importlib.import_module('advertising.migrations.0005_backfill_creative_advertiser').backfill
        with connection.schema_editor() as schema_editor:
            with self.assertRaisesRegex(RuntimeError, 'mismatched client'):
                backfill(old, schema_editor)
        Creative.objects.all().delete()
