"""Stable public classification identifiers and legacy compatibility rules."""

CATEGORY_CHOICES = (
    ('movies', 'Movies'),
    ('tv_shows', 'TV Shows'),
    ('documentaries', 'Documentaries'),
    ('animations', 'Animations'),
)
FORMAT_CHOICES = (('standalone', 'Standalone'), ('series', 'Series'))
CATEGORY_FLAGS = {
    'movies': 'for_movies',
    'tv_shows': 'for_tv_shows',
    'documentaries': 'for_documentaries',
    'animations': 'for_animations',
}


def genre_categories(genre):
    return [key for key, field in CATEGORY_FLAGS.items() if getattr(genre, field)]


def legacy_classification(content_type):
    """Only infer category where the old value has one clear meaning."""
    return {
        'movie': ('movies', 'standalone'),
        'documentary': ('documentaries', 'standalone'),
        'animations': ('animations', 'standalone'),
        'tv_show': ('tv_shows', 'standalone'),
        'series': (None, 'series'),
        'miniseries': (None, 'series'),
    }.get((content_type or '').lower(), (None, None))


def legacy_type_for(category, content_format):
    if content_format == 'series':
        return 'series'
    return {
        'movies': 'movie',
        'tv_shows': 'tv_show',
        'documentaries': 'documentary',
        'animations': 'animations',
    }[category]
