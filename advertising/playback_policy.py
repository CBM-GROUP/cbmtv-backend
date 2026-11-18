"""Conservative viewer policy and placement playback defaults.

Inventory caps belong to campaigns or allocations; these limits protect a
viewer playback session independently of any unlimited inventory setting.
"""

DIRECT_SESSION_LIMIT = 2
VIDEO_SESSION_LIMIT = 2
DEFAULT_SKIP_SECONDS = 5
DEFAULT_MAX_VIDEO_DURATION = 300
DEFAULT_MAX_MIDROLL_INTERRUPTION = 2


def skip_after(decision):
    if 'skippable_after' in decision.playback_policy:
        return decision.playback_policy['skippable_after']
    # Existing campaign null means explicitly non-skippable. Direct creatives
    # inherit the placement setting, which also permits null for no skip.
    return decision.campaign.skippable_after if decision.campaign_id else decision.placement.skippable_after
