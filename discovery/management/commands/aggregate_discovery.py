from django.core.management.base import BaseCommand
from django.db.models import Count, Min
from discovery.aggregation import aggregate_batch, rebuild
from discovery.models import DiscoveryEvent


class Command(BaseCommand):
    help = 'Drain discovery events into hourly metrics; --rebuild recomputes retained history.'

    def add_arguments(self, parser):
        parser.add_argument('--rebuild', action='store_true')
        parser.add_argument('--batch', type=int, default=500)
        parser.add_argument('--max-batches', type=int, default=100)

    def handle(self, *args, **options):
        if not 1 <= options['batch'] <= 5000 or not 1 <= options['max_batches'] <= 10000:
            raise ValueError('Use positive bounded batch and max-batches values.')
        if options['rebuild']:
            rebuild()
        total = 0
        for _ in range(options['max_batches']):
            count = aggregate_batch(options['batch'])
            total += count
            if count < options['batch']:
                break
        pending = DiscoveryEvent.objects.filter(aggregated_at__isnull=True).aggregate(count=Count('id'), oldest=Min('occurred_at'))
        self.stdout.write(f"processed={total} pending={pending['count']} oldest_pending={pending['oldest'] or '-'}")
