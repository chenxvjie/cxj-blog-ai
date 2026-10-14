CREATE SEQUENCE ai.revision_seq;
SELECT setval('ai.revision_seq', greatest(coalesce((SELECT max(revision) FROM ai.document),0), (extract(epoch FROM clock_timestamp())*1000000)::bigint), true);
CREATE TABLE ai.index_job (
 post_id bigint PRIMARY KEY,
 revision bigint NOT NULL,
 state text NOT NULL DEFAULT 'PENDING' CHECK(state IN ('PENDING','RUNNING','READY','FAILED','EXCLUDED')),
 attempts integer NOT NULL DEFAULT 0,
 available_at timestamptz NOT NULL DEFAULT now(),
 lease uuid,
 lease_until timestamptz,
 error_code text,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ai_index_job_claim ON ai.index_job(state,available_at);

CREATE FUNCTION ai.enqueue_article(article_id bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ai,public AS $$
DECLARE next_revision bigint; visibility text;
BEGIN
 next_revision := nextval('ai.revision_seq');
 SELECT CASE WHEN deleted_at IS NOT NULL THEN 'DELETED' ELSE status END INTO visibility FROM public.blog_post WHERE id=article_id;
 UPDATE ai.document SET revision=next_revision,active_revision=NULL,status=coalesce(visibility,'DELETED'),
   index_state='PENDING',updated_at=now() WHERE post_id=article_id;
 INSERT INTO ai.index_job(post_id,revision) VALUES (article_id,next_revision)
 ON CONFLICT(post_id) DO UPDATE SET revision=excluded.revision,state='PENDING',attempts=0,
   available_at=now(),lease=NULL,lease_until=NULL,error_code=NULL,updated_at=now();
END $$;
REVOKE ALL ON FUNCTION ai.enqueue_article(bigint) FROM PUBLIC;

CREATE FUNCTION ai.on_article_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ai,public AS $$
BEGIN
 IF TG_OP='DELETE' THEN PERFORM ai.enqueue_article(OLD.id); RETURN OLD; END IF;
 IF TG_OP='UPDATE' AND ROW(NEW.title,NEW.slug,NEW.content_md,NEW.status,NEW.deleted_at)
   IS NOT DISTINCT FROM ROW(OLD.title,OLD.slug,OLD.content_md,OLD.status,OLD.deleted_at) THEN RETURN NEW; END IF;
 PERFORM ai.enqueue_article(NEW.id); RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION ai.on_article_change() FROM PUBLIC;
CREATE TRIGGER ai_article_changed AFTER INSERT OR UPDATE OR DELETE ON public.blog_post
 FOR EACH ROW EXECUTE FUNCTION ai.on_article_change();
SELECT ai.enqueue_article(id) FROM public.blog_post;
