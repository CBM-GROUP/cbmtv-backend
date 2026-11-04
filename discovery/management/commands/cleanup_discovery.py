from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from discovery.models import DiscoveryEvent, PlaybackSession, ContentHourlyMetric, QueryHourlyMetric


class Command(BaseCommand):
    help = 'Dry-run by default; --apply removes source events/sessions and hourly metrics older than 90 days.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=90)
        targets = [
            ('events', DiscoveryEvent.objects.filter(occurred_at__lt=cutoff)),
            ('sessions', PlaybackSession.objects.filter(created_at__lt=cutoff)),
            ('content_hours', ContentHourlyMetric.objects.filter(hour__lt=cutoff)),
            ('query_hours', QueryHourlyMetric.objects.filter(hour__lt=cutoff)),
        ]
        counts = {name: queryset.count() for name, queryset in targets}
        if options['apply']:
            for _, queryset in targets:
                queryset.delete()
        self.stdout.write(f"{'deleted' if options['apply'] else 'would_delete'}={counts}")
