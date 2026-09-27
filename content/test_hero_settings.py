from rest_framework.test import APITestCase

from accounts.models import User
from content.models import contentadverts


class HeroSettingsTests(APITestCase):
    url = '/api/content/hero-settings/'

    def test_public_defaults_and_admin_update(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['image_duration_seconds'], 20)

        viewer = User.objects.create_user(email='viewer@example.com', name='Viewer', password='pass1234')
        self.client.force_authenticate(user=viewer)
        response = self.client.patch(self.url, {'image_duration_seconds': 30}, format='json')
        self.assertEqual(response.status_code, 403)

        admin = User.objects.create_user(email='hero-admin@example.com', name='Hero admin', password='pass1234')
        admin.is_staff = True
        admin.save(update_fields=['is_staff'])
        self.client.force_authenticate(user=admin)

        response = self.client.patch(self.url, {'image_duration_seconds': 25}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['image_duration_seconds'], 25)

        response = self.client.patch(self.url, {'image_duration_seconds': 0}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(self.url).data['image_duration_seconds'], 25)

    def test_existing_adverts_remain_in_hero_by_default(self):
        advert = contentadverts.objects.create(advert_type='hello', advert_name='Featured')
        self.assertTrue(advert.show_in_hero)
        self.assertEqual(advert.hero_order, 0)
