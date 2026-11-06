from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from advertising.models import Decision, AdSession, CampaignDay, Audit


class Command(BaseCommand):
    help = 'Dry-run by default: retain ad events/decisions 90 days and audit 365 days. Lifetime cap counters remain.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=90)
        groups = [Decision.objects.filter(created_at__lt=cutoff), AdSession.objects.filter(created_at__lt=cutoff),
                  CampaignDay.objects.filter(day__lt=cutoff.date()), Audit.objects.filter(created_at__lt=timezone.now() - timedelta(days=365))]
        for rows in groups:
            count = rows.count()
            if options['apply']:
                rows.delete()
            self.stdout.write(f'{rows.model.__name__}: {count} {"deleted" if options["apply"] else "eligible (dry run)"}')
