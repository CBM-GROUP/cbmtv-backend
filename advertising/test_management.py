from rest_framework.test import APITestCase
from accounts.models import User
from .models import Advertiser


class AdvertisingManagementStageTests(APITestCase):
    def test_staff_records_require_dashboard_permission_and_keep_private_cache(self):
        staff = User.objects.create_superuser('stage-staff@example.com', 'Staff', password='test')
        viewer = User.objects.create_user('stage-viewer@example.com', 'Viewer', password='test')
        Advertiser.objects.create(name='Sponsor', billing_reference='PRIVATE')

        self.assertEqual(self.client.get('/api/advertising/advertisers/').status_code, 401)
        self.client.force_authenticate(viewer)
        self.assertEqual(self.client.get('/api/advertising/advertisers/').status_code, 403)
        self.client.force_authenticate(staff)
        response = self.client.get('/api/advertising/advertisers/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'][0]['billing_reference'], 'PRIVATE')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
