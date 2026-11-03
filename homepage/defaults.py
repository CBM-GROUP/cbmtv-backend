SECTION_LABELS = {
    'featured_hero': 'Featured Hero',
    'featured_channels': 'Featured Channels',
    'continue_watching': 'Continue Watching',
    'new_releases': 'New Releases',
    'tv_shows': 'TV Shows',
    'movies': 'Movies',
    'documentaries': 'Documentaries',
    'animations': 'Animations',
}
REQUIRED_SECTIONS = ('featured_hero', 'continue_watching')


def default_sections():
    return [{'id': key, 'enabled': True} for key in SECTION_LABELS]
