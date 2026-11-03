-- Temporary publication pause. Test on an isolated restore before production use.
-- This transaction fails rather than replacing any pre-existing guard.
BEGIN;
SET LOCAL lock_timeout = '5s';
DO $$ BEGIN
    IF to_regprocedure('public.cbmtv_homepage_publication_guard()') IS NOT NULL THEN
        RAISE EXCEPTION 'A homepage publication guard already exists; inspect it before proceeding';
    END IF;
END $$;
CREATE FUNCTION public.cbmtv_homepage_publication_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- The only data-migration write needed by Branch 3 marks unknown history.
    -- It changes no publication status, ownership, media or visible metadata.
    IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'content_content'
       AND (to_jsonb(NEW) - 'publication_history_unknown') = (to_jsonb(OLD) - 'publication_history_unknown')
       AND to_jsonb(OLD)->>'publication_history_unknown' = 'false'
       AND to_jsonb(NEW)->>'publication_history_unknown' = 'true'
       AND to_jsonb(NEW)->>'first_published_at' IS NULL THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION USING ERRCODE = '55000',
        MESSAGE = 'Content publication and channel changes are temporarily paused for verified maintenance';
END $$;
CREATE TRIGGER cbmtv_homepage_rollout_pause BEFORE INSERT OR UPDATE OR DELETE ON public.content_content
    FOR EACH ROW EXECUTE FUNCTION public.cbmtv_homepage_publication_guard();
CREATE TRIGGER cbmtv_homepage_rollout_pause BEFORE INSERT OR UPDATE OR DELETE ON public.channel_channel
    FOR EACH ROW EXECUTE FUNCTION public.cbmtv_homepage_publication_guard();
CREATE TRIGGER cbmtv_homepage_rollout_pause BEFORE INSERT OR UPDATE OR DELETE ON public.content_season
    FOR EACH ROW EXECUTE FUNCTION public.cbmtv_homepage_publication_guard();
CREATE TRIGGER cbmtv_homepage_rollout_pause BEFORE INSERT OR UPDATE OR DELETE ON public.content_episode
    FOR EACH ROW EXECUTE FUNCTION public.cbmtv_homepage_publication_guard();
CREATE TRIGGER cbmtv_homepage_rollout_pause BEFORE INSERT OR UPDATE OR DELETE ON public.content_miniseries
    FOR EACH ROW EXECUTE FUNCTION public.cbmtv_homepage_publication_guard();
ALTER TABLE public.content_content ENABLE ALWAYS TRIGGER cbmtv_homepage_rollout_pause;
ALTER TABLE public.channel_channel ENABLE ALWAYS TRIGGER cbmtv_homepage_rollout_pause;
ALTER TABLE public.content_season ENABLE ALWAYS TRIGGER cbmtv_homepage_rollout_pause;
ALTER TABLE public.content_episode ENABLE ALWAYS TRIGGER cbmtv_homepage_rollout_pause;
ALTER TABLE public.content_miniseries ENABLE ALWAYS TRIGGER cbmtv_homepage_rollout_pause;
COMMIT;
