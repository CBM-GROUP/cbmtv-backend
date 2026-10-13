"""Current database snapshots for authorized dashboard analytics users."""

from django.db.models import Count, Q
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from accounts.permissions import HasDashboardSection
from channel.models import Channel
from content.models import Content, contentadverts
from library.models import SavedItem, WatchProgress


class DashboardAnalyticsView(APIView):
    permission_classes = [HasDashboardSection]
    required_section = 'analytics'

    def get(self, request):
        content = Content.objects.aggregate(total=Count('id'))
        status_counts = Content.objects.values('status').annotate(count=Count('id')).order_by('status')
        type_counts = Content.objects.values('content_type').annotate(count=Count('id')).order_by('content_type')
        users = User.objects.aggregate(
            total=Count('id'),
            active=Count('id', filter=Q(is_active=True, must_change_password=False)),
            inactive=Count('id', filter=Q(is_active=False)),
            pending_activation=Count('id', filter=Q(is_active=True, must_change_password=True)),
        )
        saved = SavedItem.objects.aggregate(total=Count('id'), users=Count('user_id', distinct=True))
        progress = WatchProgress.objects.aggregate(
            users_with_records=Count('user_id', distinct=True),
            unfinished_records=Count('id', filter=Q(completed=False)),
        )
        most_saved = SavedItem.objects.values('content_id', 'content__title', 'content__status').annotate(
            saved_records=Count('id')
        ).order_by('-saved_records', 'content_id')[:10]
        unfinished_titles = WatchProgress.objects.filter(completed=False).values(
            'content_id', 'content__title', 'content__status'
        ).annotate(users_with_unfinished_progress=Count('user_id', distinct=True)).order_by(
            '-users_with_unfinished_progress', 'content_id'
        )[:10]

        return Response({
            'content': {
                'total_records': content['total'],
                'by_status': [{'status': row['status'] or 'unassigned', 'count': row['count']} for row in status_counts],
                'by_type': [{'type': row['content_type'], 'count': row['count']} for row in type_counts],
                'channel_count': Channel.objects.count(),
                'advert_count': contentadverts.objects.count(),
            },
            'users': users,
            'saved': {
                'total_records': saved['total'],
                'distinct_users': saved['users'],
                'most_saved_titles': [
                    {'content_id': row['content_id'], 'title': row['content__title'],
                     'status': row['content__status'], 'saved_records': row['saved_records']}
                    for row in most_saved
                ],
            },
            'progress': {
                **progress,
                'unfinished_titles': [
                    {'content_id': row['content_id'], 'title': row['content__title'],
                     'status': row['content__status'],
                     'users_with_unfinished_progress': row['users_with_unfinished_progress']}
                    for row in unfinished_titles
                ],
            },
        })
