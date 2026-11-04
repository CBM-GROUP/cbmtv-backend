"""Short-lived proof that an analytics event refers to an actual search response."""
import re
import unicodedata
from django.core import signing

SALT = 'cbmtv.discovery.search.v1'
MODES = {'title', 'everything', 'director', 'genre', 'cast', 'channel'}
SENSITIVE = re.compile(r'(?i)(?:https?://|\b[^\s@]+@[^\s@]+\.[^\s@]+\b|\b(?:password|passwd|token|secret|api[_ -]?key|bearer)\s*[:= ]|\b[A-Za-z0-9_-]{40,}\b)')


def normalized_term(value):
    if not isinstance(value, str):
        return None
    term = ' '.join(unicodedata.normalize('NFKC', value).casefold().split())
    if not term or len(term) > 120 or SENSITIVE.search(term):
        return None
    return term


def make_receipt(query, mode, total, ids):
    term = normalized_term(query)
    if not term or mode not in MODES:
        return None
    return signing.dumps({'q': term, 'm': mode, 'n': total, 'ids': list(ids)}, salt=SALT, compress=True)


def read_receipt(value):
    if not isinstance(value, str) or len(value) > 4000:
        raise ValueError('Invalid search receipt.')
    try:
        data = signing.loads(value, salt=SALT, max_age=600)
    except signing.BadSignature as exc:
        raise ValueError('Invalid or expired search receipt.') from exc
    if (not isinstance(data, dict) or data.get('m') not in MODES or
            data.get('q') != normalized_term(data.get('q')) or
            not isinstance(data.get('n'), int) or not 0 <= data['n'] <= 1000000 or
            not isinstance(data.get('ids'), list) or len(data['ids']) > 100 or
            not all(isinstance(pk, int) and pk > 0 for pk in data['ids'])):
        raise ValueError('Invalid search receipt.')
    return data
