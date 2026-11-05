HOMEPAGE_PLACEMENTS = {
    'homepage_banner': 'Sponsor banner',
    'homepage_sponsored_section': 'Sponsored collection',
    'homepage_featured_sponsor': 'Featured sponsor',
    'homepage_channel_promotion': 'Sponsored channel',
    'homepage_content_promotion': 'Sponsored title',
}
PAGE_PLACEMENTS = {**HOMEPAGE_PLACEMENTS, 'channel_sponsor': 'Channel sponsor',
                   'content_sponsor': 'Content sponsor'}
VIDEO_PLACEMENTS = {'preroll': 'Pre-roll', 'midroll': 'Mid-roll', 'postroll': 'Post-roll'}
PLACEMENTS = {**PAGE_PLACEMENTS, **VIDEO_PLACEMENTS}
EVENTS = ('impression', 'video_start', 'first_quartile', 'midpoint', 'third_quartile',
          'complete', 'click', 'skip', 'failure')
APPROVALS = tuple((s, s) for s in ('draft', 'review', 'approved', 'rejected'))
