def effective_destination(creative):
    """Return the reviewed URL and the level supplying it."""
    if creative.destination_url:
        return creative.destination_url, 'creative'
    if creative.campaign_id and creative.campaign.default_destination_url:
        return creative.campaign.default_destination_url, 'campaign'
    if creative.advertiser.default_destination_url:
        return creative.advertiser.default_destination_url, 'advertiser'
    return '', 'none'
