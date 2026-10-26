"""Read-only prerequisite for any future email identity remediation."""

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.db.models.functions import Lower, Trim

from accounts.models import User


class Command(BaseCommand):
    help = 'Count case-insensitive email collisions without printing emails or modifying accounts.'

    def handle(self, *args, **options):
        groups = list(User.objects.annotate(identity=Lower(Trim('email')))
                      .values('identity').annotate(total=Count('pk')).filter(total__gt=1))
        self.stdout.write(f'Users: {User.objects.count()}; collision groups: {len(groups)}; '
                          f'affected accounts: {sum(group["total"] for group in groups)}. No records changed.')
