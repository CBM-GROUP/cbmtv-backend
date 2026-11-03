"""Production acceptance probes; synthetic writes are always rolled back."""
import copy
import json
import os
import secrets
from datetime import timedelta
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cbmtv.settings')
import django
django.setup()
from django.db import transaction
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken
from accounts.models import User, DashboardRole
from content.models import Content
from content.publication import public_eligibility
from library.models import WatchProgress

host = 'apicbmtv.cbmgroupco.com'
checks = []

def client(user=None):
    result = APIClient(HTTP_HOST=host)
    if user:
        result.credentials(HTTP_AUTHORIZATION='Bearer ' + str(RefreshToken.for_user(user).access_token))
    return result

def section(response, name):
    assert response.status_code == 200, 'Homepage response status'
    return next(row for row in response.data['sections'] if row['id'] == name)['items']

def private(response):
    assert response['Cache-Control'] == 'private, no-store'
    assert {'authorization', 'cookie'} <= {key.strip().lower() for key in response['Vary'].split(',')}

with transaction.atomic():
    try:
        suffix = secrets.token_hex(8)
        def user(label, **kwargs):
            instance = User(email=f'rollout-{suffix}-{label}@example.invalid', name='Temporary rollout verification', **kwargs)
            instance.set_unusable_password()
            instance.save()
            return instance
        viewer_a, viewer_b = user('a'), user('b')
        manager = user('manager', is_staff=True, role='internal_admin')
        role = DashboardRole.objects.create(name='rollout-' + suffix, permissions=['channels'])
        restricted = user('restricted', dashboard_role=role)
        anon, a, b, admin, denied = client(), client(viewer_a), client(viewer_b), client(manager), client(restricted)
        health = anon.get('/health/')
        assert health.status_code == 200 and health.json()['status'] == 'ok'
        home = anon.get('/api/homepage/')
        assert not section(home, 'continue_watching')
        private(home)
        checks += ['health', 'anonymous_homepage', 'private_cache_headers']
        for actor in (anon, a, denied):
            for path, method in (('/api/homepage/manage/', 'get'), ('/api/homepage/manage/', 'put'), ('/api/homepage/preview/', 'post')):
                response = getattr(actor, method)(path, {}, format='json') if method != 'get' else actor.get(path)
                assert response.status_code in (401, 403), 'Management permission boundary'
                private(response)
        checks.append('anonymous_viewer_and_wrong_section_management_denied')
        response = admin.get('/api/homepage/manage/')
        assert response.status_code == 200
        config = copy.deepcopy(response.data)
        titles = list(Content.objects.filter(**public_eligibility()).order_by('pk')[:2])
        assert len(titles) == 2, 'Two public titles needed for isolated progress probes'
        for viewer, title in ((viewer_a, titles[0]), (viewer_b, titles[1])):
            WatchProgress.objects.create(user=viewer, content=title, position_seconds=41, duration_seconds=100)
        for actor, title in ((a, titles[0]), (b, titles[1])):
            response = actor.get('/api/homepage/')
            items = section(response, 'continue_watching')
            assert len(items) == 1 and items[0]['content']['id'] == title.pk
            private(response)
        assert not section(anon.get('/api/homepage/'), 'continue_watching')
        checks.append('jwt_authenticated_viewers_receive_only_own_progress')
        now = timezone.now()
        config['featured_titles'] = [{'content': titles[0].pk, 'start_at': (now - timedelta(hours=1)).isoformat(), 'end_at': (now + timedelta(hours=1)).isoformat()}]
        config['featured_channels'] = []
        response = admin.put('/api/homepage/manage/', config, format='json')
        assert response.status_code == 200, 'Authorized configuration save'
        assert admin.get('/api/homepage/manage/').data['featured_titles'][0]['content'] == titles[0].pk
        assert [item['id'] for item in section(anon.get('/api/homepage/'), 'featured_hero')] == [titles[0].pk]
        preview = admin.post('/api/homepage/preview/', config, format='json')
        assert [item['id'] for item in section(preview, 'featured_hero')] == [titles[0].pk]
        assert not section(preview, 'continue_watching')
        private(preview)
        checks += ['authorized_configuration_save_and_readback', 'preview_uses_same_eligibility_without_personal_history', 'active_schedule_visible']
        for start, end in ((now + timedelta(hours=1), now + timedelta(hours=2)), (now - timedelta(hours=2), now - timedelta(hours=1))):
            config['featured_titles'][0].update(start_at=start.isoformat(), end_at=end.isoformat())
            assert admin.put('/api/homepage/manage/', config, format='json').status_code == 200
            assert not section(anon.get('/api/homepage/'), 'featured_hero'), 'Schedule bypassed by fallback'
        checks.append('future_and_expired_schedules_hidden_without_fallback')
        invalid = copy.deepcopy(config)
        invalid['featured_titles'][0]['start_at'] = '2026-11-03T00:00:00'
        assert admin.put('/api/homepage/manage/', invalid, format='json').status_code == 400
        invalid = copy.deepcopy(config)
        invalid['featured_titles'][0]['end_at'] = invalid['featured_titles'][0]['start_at']
        assert admin.put('/api/homepage/manage/', invalid, format='json').status_code == 400
        assert not section(anon.get('/api/homepage/'), 'new_releases'), 'Unknown legacy dates treated as new'
        checks += ['naive_and_invalid_schedule_rejected', 'legacy_unknown_history_excluded_from_new_releases']
    finally:
        transaction.set_rollback(True)

assert not User.objects.filter(email__startswith='rollout-' + suffix + '-').exists()
assert not DashboardRole.objects.filter(name='rollout-' + suffix).exists()
checks.append('synthetic_accounts_progress_and_configuration_writes_rolled_back')
print(json.dumps({'passed': True, 'checks': checks}), flush=True)
