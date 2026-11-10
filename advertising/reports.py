from datetime import datetime, time, timedelta, timezone
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.db.models.functions import Coalesce
from django.utils import timezone as clock
from rest_framework import serializers
from .constants import EVENTS
from .models import Event, Campaign, Decision, AdRequest


def counts():
    return {kind: Count('pk', filter=Q(kind=kind)) for kind in EVENTS}


def rates(row):
    row['completion_rate'] = round(row['complete'] / row['video_start'], 4) if row['video_start'] else None
    row['click_through_rate'] = round(row['click'] / row['impression'], 4) if row['impression'] else None
    return row


def report(params):
    today = clock.now().date()
    field = serializers.DateField()
    start = field.run_validation(params.get('from', (today - timedelta(days=6)).isoformat()))
    end = field.run_validation(params.get('to', today.isoformat()))
    if not today - timedelta(days=89) <= start <= end <= today:
        raise serializers.ValidationError('Select a date range within the retained past 90 UTC days.')
    after = datetime.combine(start, time.min, tzinfo=timezone.utc)
    before = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    filters = {}
    for key in ('campaign', 'advertiser', 'creative'):
        if params.get(key):
            value = serializers.IntegerField(min_value=1).run_validation(params[key])
            filters[{'campaign': 'decision__campaign_id', 'advertiser': 'decision__campaign__advertiser_id', 'creative': 'decision__creative_id'}[key]] = value
    events = Event.objects.filter(created_at__gte=after, created_at__lt=before, **filters)
    summary = rates(events.aggregate(**counts()))
    decision_filters = {key.removeprefix('decision__'): value for key, value in filters.items()}
    decisions = Decision.objects.filter(created_at__gte=after, created_at__lt=before, **decision_filters)
    requests = AdRequest.objects.filter(created_at__gte=after, created_at__lt=before,
        **{'decision__' + key: value for key, value in decision_filters.items()})
    summary.update(requests=requests.count(), decisions=decisions.count(), served=decisions.filter(creative__isnull=False).count(),
                   no_ad=decisions.filter(creative__isnull=True).count())
    def grouped(*keys):
        return [rates(row) for row in events.values(*keys).annotate(**counts()).order_by(*keys)]
    campaigns = Campaign.objects.all()
    if params.get('campaign'):
        campaigns = campaigns.filter(pk=params['campaign'])
    if params.get('advertiser'):
        campaigns = campaigns.filter(advertiser_id=params['advertiser'])
    if params.get('creative'):
        campaigns = campaigns.filter(creatives__pk=params['creative'])
    return {'from': start, 'to': end, 'summary': summary,
            'by_campaign': grouped('decision__campaign_id', 'decision__campaign__name'),
            'by_creative': grouped('decision__creative_id', 'decision__creative__name', 'decision__creative__kind', 'decision__campaign_id', 'decision__campaign__name'),
            'by_advertiser': grouped('decision__campaign__advertiser_id', 'decision__campaign__advertiser__name'),
            'by_placement': grouped('decision__placement_id'),
            'by_date': [rates(row) for row in events.annotate(day=TruncDate('created_at', tzinfo=timezone.utc)).values('day').annotate(**counts()).order_by('day')],
            'cap_utilization': list(campaigns.annotate(today_impressions=Coalesce(Sum('campaignday__impressions', filter=Q(campaignday__day=today)), 0))
                .order_by('pk').values('id', 'name', 'impressions', 'impression_limit', 'daily_limit', 'today_impressions')),
            'no_ad_reasons': list(decisions.filter(creative__isnull=True).values('reason').annotate(count=Count('pk')).order_by('reason'))}
