-- Remove only this rollout's temporary guards after backend verification.
BEGIN;
SET LOCAL lock_timeout = '5s';
DROP TRIGGER cbmtv_homepage_rollout_pause ON public.content_content;
DROP TRIGGER cbmtv_homepage_rollout_pause ON public.channel_channel;
DROP TRIGGER cbmtv_homepage_rollout_pause ON public.content_season;
DROP TRIGGER cbmtv_homepage_rollout_pause ON public.content_episode;
DROP TRIGGER cbmtv_homepage_rollout_pause ON public.content_miniseries;
DROP FUNCTION public.cbmtv_homepage_publication_guard();
COMMIT;
