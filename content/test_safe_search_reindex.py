from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from content.search_reindex import rebuild_search_index


class _Response:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


@override_settings(MEILISEARCH_URL='http://meili.test', MEILISEARCH_MASTER_KEY='test-key', MEILISEARCH_INDEX='content')
class SafeReindexTests(SimpleTestCase):
    def test_shadow_is_verified_before_swap_and_old_index_is_retained(self):
        indexes = {'content': [{'id': 1}]}
        index_settings = {}
        calls = []

        def request(method, url, headers, json, timeout):
            path = url.removeprefix('http://meili.test')
            calls.append((method, path))
            self.assertEqual(headers['Authorization'], 'Bearer test-key')
            if path == '/indexes' and method == 'POST':
                indexes[json['uid']] = []
                return _Response({'taskUid': 1})
            if path == '/tasks/1':
                return _Response({'status': 'succeeded'})
            if path.endswith('/settings'):
                if method == 'PATCH':
                    self.assertEqual(json['searchableAttributes'][0], 'title')
                    self.assertIn('category', json['filterableAttributes'])
                    index_settings.update(json)
                    return _Response({'taskUid': 1})
                return _Response(index_settings)
            if '/documents?' in path and method == 'POST':
                indexes[path.split('/')[2]] = json
                return _Response({'taskUid': 1})
            if '/documents?' in path and method == 'GET':
                return _Response({'results': indexes[path.split('/')[2]]})
            if path == '/swap-indexes':
                first, second = json[0]['indexes']
                indexes[first], indexes[second] = indexes[second], indexes[first]
                return _Response({'taskUid': 1})
            raise AssertionError((method, path))

        with patch('content.search_reindex.requests.request', side_effect=request):
            result = rebuild_search_index([{'id': 2, 'title': 'New'}])
        self.assertEqual(result['indexed'], 1)
        self.assertEqual(indexes['content'], [{'id': 2, 'title': 'New'}])
        self.assertEqual(indexes[result['backup_index']], [{'id': 1}])
        self.assertEqual(sum(path == '/swap-indexes' for _, path in calls), 1)
        self.assertFalse(any(method == 'DELETE' for method, _ in calls))
