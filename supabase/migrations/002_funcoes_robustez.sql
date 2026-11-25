-- =====================================================================
--  PLATAFORMA SAAS MULTI-EMPRESA — MARKETING E ATENDIMENTO 360°
--  ARQUIVO 2 DE 3 — FUNÇÕES: cofre de tokens, fila com retentativas e RAG
--  Supabase / PostgreSQL 15+
-- ---------------------------------------------------------------------
--  COMO USAR
--    1. Rode ANTES o arquivo 001_estrutura_base.sql
--    2. Supabase → SQL Editor → New query → cole ESTE ARQUIVO INTEIRO → Run
--    3. Resultado esperado: "Success. No rows returned"
--    (Roda como uma única transação e pode ser repetido sem problemas.)
--
--  O QUE ESTE ARQUIVO CRIA
--    A. Cofre de tokens:
--         set_social_credential_tokens  → guarda/renova tokens no Vault
--         get_social_credential         → devolve o token ao n8n (só service_role)
--         credentials_to_renew (view)   → lista tokens perto de vencer
--    B. Fila de publicação (sem postagem em dobro, com retentativas):
--         claim_due_post_targets        → o n8n "pega" os envios que venceram
--         complete_post_target          → marca como publicado
--         fail_post_target              → registra erro e agenda nova tentativa
--         sweep_stuck_post_targets      → destrava envios abandonados
--         refresh_post_status           → (interna) recalcula o status da postagem
--    C. RAG:
--         match_knowledge               → busca na base de conhecimento DA EMPRESA
--
--  Todas as funções "secretas" só podem ser executadas pela chave de servidor
--  (service_role). Visitantes e usuários logados NÃO conseguem chamá-las.
-- =====================================================================


-- A. COFRE DE TOKENS -----------------------------------------------------------

-- A.1 Guarda (ou renova) os tokens de uma conta no Vault.
--     Nunca grava o token em tabela comum: só a referência do cofre.
create or replace function public.set_social_credential_tokens(
  p_credential_id       uuid,
  p_access_token        text,
  p_refresh_token       text        default null,
  p_access_expires_at   timestamptz default null,   -- vazio = token que não expira
  p_refresh_expires_at  timestamptz default null
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_access_id  uuid;
  v_refresh_id uuid;
begin
  if p_access_token is null or btrim(p_access_token) = '' then
    raise exception 'O token de acesso não pode ser vazio.';
  end if;

  select c.access_token_secret_id, c.refresh_token_secret_id
    into v_access_id, v_refresh_id
    from public.social_credentials c
   where c.id = p_credential_id
   for update;

  if not found then
    raise exception 'Credencial % não encontrada.', p_credential_id;
  end if;

  if v_access_id is null then
    v_access_id := vault.create_secret(
      p_access_token,
      'social_credential:' || p_credential_id::text || ':access',
      'Token de acesso de rede social');
  else
    perform vault.update_secret(v_access_id, p_access_token);
  end if;

  if p_refresh_token is not null then
    if v_refresh_id is null then
      v_refresh_id := vault.create_secret(
        p_refresh_token,
        'social_credential:' || p_credential_id::text || ':refresh',
        'Refresh token de rede social');
    else
      perform vault.update_secret(v_refresh_id, p_refresh_token);
    end if;
  end if;

  update public.social_credentials
     set access_token_secret_id   = v_access_id,
         refresh_token_secret_id  = v_refresh_id,
         access_token_expires_at  = p_access_expires_at,
         refresh_token_expires_at = coalesce(p_refresh_expires_at, refresh_token_expires_at),
         status                   = 'active',
         last_error               = null,
         last_validated_at        = now()
   where id = p_credential_id;
end;
$$;

-- A.2 Lê a credencial (com os tokens já descriptografados) para o n8n usar.
create or replace function public.get_social_credential(p_credential_id uuid)
returns table (
  credential_id             uuid,
  tenant_id                 uuid,
  platform                  text,
  external_account_id       text,
  parent_external_id        text,
  status                    text,
  scopes                    text[],
  access_token              text,
  refresh_token             text,
  access_token_expires_at   timestamptz,
  refresh_token_expires_at  timestamptz
)
language sql
security definer
set search_path = ''
as $$
  select c.id, c.tenant_id, c.platform, c.external_account_id, c.parent_external_id,
         c.status, c.scopes,
         (select s.decrypted_secret from vault.decrypted_secrets s where s.id = c.access_token_secret_id),
         (select s.decrypted_secret from vault.decrypted_secrets s where s.id = c.refresh_token_secret_id),
         c.access_token_expires_at, c.refresh_token_expires_at
    from public.social_credentials c
   where c.id = p_credential_id;
$$;

-- A.3 Tokens perto de vencer (o n8n consulta isto de tempos em tempos para renovar).
--     Janela por rede: X = 20 min, TikTok = 2 h, demais = 7 dias.
--     Tokens que não expiram (data vazia) não aparecem aqui.
create or replace view public.credentials_to_renew
with (security_invoker = true) as
select c.id                              as credential_id,
       c.tenant_id,
       c.platform,
       c.external_account_id,
       c.access_token_expires_at,
       c.refresh_token_expires_at,
       (c.refresh_token_secret_id is not null) as has_refresh_token
  from public.social_credentials c
 where c.status = 'active'
   and c.access_token_expires_at is not null
   and c.access_token_expires_at <= now() + case c.platform
         when 'x'      then interval '20 minutes'
         when 'tiktok' then interval '2 hours'
         else               interval '7 days'
       end;


-- B. FILA DE PUBLICAÇÃO --------------------------------------------------------

-- B.1 (interna) Recalcula o status da postagem a partir do status de cada rede.
create or replace function public.refresh_post_status(p_post_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_proc int;
  v_open int;
  v_pub  int;
  v_fail int;
begin
  select count(*) filter (where status = 'processing'),
         count(*) filter (where status in ('pending','processing')),
         count(*) filter (where status = 'published'),
         count(*) filter (where status = 'failed')
    into v_proc, v_open, v_pub, v_fail
    from public.post_targets
   where post_id = p_post_id;

  update public.posts_schedule
     set status = case
           when v_proc > 0 or (v_open > 0 and v_pub + v_fail > 0) then 'publishing'
           when v_open > 0                                        then 'scheduled'
           when v_pub > 0 and v_fail = 0                          then 'published'
           when v_pub > 0                                         then 'partially_published'
           when v_fail > 0                                        then 'failed'
           else status
         end
   where id = p_post_id
     and status not in ('draft','pending_approval','cancelled');
end;
$$;

-- B.2 O n8n "pega" os envios que já venceram. Vários robôs ao mesmo tempo nunca
--     pegam o mesmo item (FOR UPDATE SKIP LOCKED) — não há postagem em dobro.
--     Só entram: empresa ativa, conta ativa, postagem agendada e horário vencido.
--     Também retoma envios "travados" há mais de 30 min (robô que caiu no meio).
create or replace function public.claim_due_post_targets(
  p_limit   int  default 10,
  p_worker  text default 'n8n'
)
returns table (
  target_id            uuid,
  post_id              uuid,
  tenant_id            uuid,
  credential_id        uuid,
  platform             text,
  external_account_id  text,
  parent_external_id   text,
  post_format          text,
  caption              text,
  link_url             text,
  options              jsonb,
  media                jsonb,
  attempt_count        int,
  max_attempts         int,
  idempotency_key      uuid
)
language sql
security definer
set search_path = ''
as $$
  with due as (
    select t.id
      from public.post_targets t
      join public.posts_schedule p     on p.id  = t.post_id
      join public.social_credentials c on c.id  = t.credential_id
      join public.tenants tn           on tn.id = t.tenant_id
     where p.status in ('scheduled', 'publishing')
       and p.scheduled_at <= now()
       and tn.status = 'active'
       and c.status  = 'active'
       and (
             (t.status = 'pending'
              and (t.retry_after is null or t.retry_after <= now()))
          or (t.status = 'processing'
              and t.locked_at < now() - interval '30 minutes'
              and t.attempt_count < t.max_attempts)
           )
     order by p.scheduled_at, t.created_at
     limit greatest(coalesce(p_limit, 10), 1)
       for update of t skip locked
  ),
  claimed as (
    update public.post_targets t
       set status        = 'processing',
           attempt_count = t.attempt_count + 1,
           locked_at     = now(),
           locked_by     = p_worker
      from due
     where t.id = due.id
    returning t.id, t.post_id, t.tenant_id, t.credential_id, t.caption_override,
              t.options, t.attempt_count, t.max_attempts, t.idempotency_key
  ),
  mark_posts as (
    update public.posts_schedule ps
       set status = 'publishing'
     where ps.id in (select cl.post_id from claimed cl)
       and ps.status = 'scheduled'
    returning ps.id
  )
  select cl.id,
         cl.post_id,
         cl.tenant_id,
         cl.credential_id,
         c.platform,
         c.external_account_id,
         c.parent_external_id,
         p.post_format,
         coalesce(cl.caption_override, p.caption),
         p.link_url,
         cl.options,
         coalesce((
           select jsonb_agg(
                    jsonb_build_object(
                      'sort_order',       pm.sort_order,
                      'asset_id',         a.id,
                      'kind',             a.kind,
                      'public_url',       a.public_url,
                      'mime_type',        a.mime_type,
                      'width',            a.width,
                      'height',           a.height,
                      'duration_seconds', a.duration_seconds,
                      'alt_text',         coalesce(pm.alt_text, a.alt_text)
                    )
                    order by pm.sort_order)
             from public.post_media pm
             join public.media_assets a
               on a.id = pm.asset_id and a.tenant_id = pm.tenant_id
            where pm.post_id = cl.post_id
         ), '[]'::jsonb),
         cl.attempt_count,
         cl.max_attempts,
         cl.idempotency_key
    from claimed cl
    join public.posts_schedule p     on p.id = cl.post_id
    join public.social_credentials c on c.id = cl.credential_id;
$$;

-- B.3 Marca o envio como publicado (devolve false se o item não estava "em processamento").
create or replace function public.complete_post_target(
  p_target_id         uuid,
  p_external_post_id  text,
  p_external_url      text default null
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_post_id    uuid;
  v_tenant_id  uuid;
begin
  update public.post_targets
     set status           = 'published',
         published_at     = now(),
         external_post_id = p_external_post_id,
         external_url     = p_external_url,
         last_error       = null,
         last_error_code  = null,
         retry_after      = null,
         locked_at        = null,
         locked_by        = null
   where id = p_target_id
     and status = 'processing'
  returning post_id, tenant_id into v_post_id, v_tenant_id;

  if not found then
    return false;
  end if;

  perform public.refresh_post_status(v_post_id);

  insert into public.event_logs
    (tenant_id, source, level, event_type, entity_type, entity_id, message, context)
  values
    (v_tenant_id, 'supabase', 'info', 'post.published', 'post_target', p_target_id,
     'Publicação concluída',
     jsonb_build_object('external_post_id', p_external_post_id, 'external_url', p_external_url));

  return true;
end;
$$;

-- B.4 Registra uma falha. Se der para tentar de novo, agenda a próxima tentativa com
--     espera crescente (2, 4, 8, 16... minutos, no máximo 60) e respeita o tempo de
--     espera pedido pela rede social (rate limit). Se não der (erro definitivo ou
--     tentativas esgotadas), marca como falhou.
--     Devolve o novo status: 'pending' (vai tentar de novo), 'failed' ou NULL
--     (o item não estava "em processamento").
create or replace function public.fail_post_target(
  p_target_id            uuid,
  p_error                text,
  p_error_code           text    default null,
  p_retryable            boolean default true,
  p_retry_after_seconds  int     default null
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_t           public.post_targets%rowtype;
  v_new_status  text;
  v_delay       int;
begin
  select * into v_t
    from public.post_targets
   where id = p_target_id
     and status = 'processing'
   for update;

  if not found then
    return null;
  end if;

  if coalesce(p_retryable, true) and v_t.attempt_count < v_t.max_attempts then
    v_new_status := 'pending';
    v_delay := greatest(
                 least(60, power(2, least(v_t.attempt_count, 10))::int) * 60,
                 coalesce(p_retry_after_seconds, 0));
  else
    v_new_status := 'failed';
  end if;

  update public.post_targets
     set status          = v_new_status,
         last_error      = left(p_error, 2000),
         last_error_code = p_error_code,
         retry_after     = case when v_new_status = 'pending'
                                then now() + make_interval(secs => v_delay)
                           end,
         locked_at       = null,
         locked_by       = null
   where id = p_target_id;

  perform public.refresh_post_status(v_t.post_id);

  insert into public.event_logs
    (tenant_id, source, level, event_type, entity_type, entity_id, message, context)
  values
    (v_t.tenant_id, 'supabase',
     case when v_new_status = 'failed' then 'error' else 'warning' end,
     case when v_new_status = 'failed' then 'post.failed' else 'post.retry_scheduled' end,
     'post_target', p_target_id, left(p_error, 500),
     jsonb_build_object('error_code', p_error_code,
                        'attempt', v_t.attempt_count,
                        'max_attempts', v_t.max_attempts,
                        'new_status', v_new_status));

  return v_new_status;
end;
$$;

-- B.5 Faxina: envios abandonados (robô caiu) que já esgotaram as tentativas viram
--     "falhou" para não ficarem presos para sempre. O n8n chama de tempos em tempos.
--     Devolve quantos itens foram encerrados.
create or replace function public.sweep_stuck_post_targets(p_stale_minutes int default 30)
returns int
language plpgsql
security definer
set search_path = ''
as $$
declare
  r        record;
  v_count  int := 0;
begin
  for r in
    update public.post_targets
       set status          = 'failed',
           last_error      = 'Processamento interrompido e limite de tentativas atingido.',
           last_error_code = 'STUCK',
           locked_at       = null,
           locked_by       = null
     where status = 'processing'
       and locked_at < now() - make_interval(mins => greatest(coalesce(p_stale_minutes, 30), 1))
       and attempt_count >= max_attempts
    returning id, post_id, tenant_id
  loop
    perform public.refresh_post_status(r.post_id);

    insert into public.event_logs
      (tenant_id, source, level, event_type, entity_type, entity_id, message)
    values
      (r.tenant_id, 'supabase', 'error', 'post.stuck', 'post_target', r.id,
       'Envio travado: limite de tentativas atingido.');

    v_count := v_count + 1;
  end loop;

  return v_count;
end;
$$;


-- C. RAG: BUSCA NA BASE DE CONHECIMENTO DA EMPRESA -----------------------------
--    Combina duas buscas e junta os resultados (Reciprocal Rank Fusion):
--      1. por SIGNIFICADO (vetor de 768 dimensões, similaridade por cosseno)
--      2. por PALAVRAS (útil para nomes de produto, taxas, números)
--    O filtro por empresa (p_tenant_id) é OBRIGATÓRIO: nunca mistura empresas.
create or replace function public.match_knowledge(
  p_tenant_id        uuid,
  p_query_embedding  vector(768),
  p_query_text       text   default '',
  p_match_count      int    default 6,
  p_semantic_weight  float  default 1.0,
  p_keyword_weight   float  default 1.0,
  p_rrf_k            int    default 50
)
returns table (
  id          uuid,
  source_id   uuid,
  content     text,
  metadata    jsonb,
  similarity  float,
  score       float
)
language plpgsql
set search_path = public, extensions
as $$
#variable_conflict use_column
declare
  v_limit  int := least(greatest(coalesce(p_match_count, 6), 1), 30);
  v_tsq    tsquery;
begin
  -- pgvector 0.8 ou mais novo: continua varrendo o índice até achar resultados
  -- suficientes depois do filtro por empresa. Em versões antigas, segue sem isso.
  begin
    perform set_config('hnsw.iterative_scan', 'relaxed_order', true);
  exception when others then
    null;
  end;

  -- Texto da pergunta -> busca por palavras com "OU" entre os termos
  if btrim(coalesce(p_query_text, '')) <> '' then
    v_tsq := websearch_to_tsquery(
               'portuguese',
               regexp_replace(btrim(p_query_text), '\s+', ' or ', 'g'));
  end if;

  return query
  with semantic as materialized (
    select kb.id as kb_id,
           (kb.embedding <=> p_query_embedding) as distance
      from public.knowledge_base kb
     where kb.tenant_id = p_tenant_id
       and kb.is_active
       and kb.embedding is not null
     order by kb.embedding <=> p_query_embedding
     limit v_limit * 3
  ),
  semantic_ranked as (
    select s.kb_id,
           s.distance,
           row_number() over (order by s.distance + 0) as rnk
      from semantic s
  ),
  keyword as (
    select kb.id as kb_id,
           row_number() over (order by ts_rank_cd(kb.fts, v_tsq) desc) as rnk
      from public.knowledge_base kb
     where kb.tenant_id = p_tenant_id
       and kb.is_active
       and numnode(v_tsq) > 0
       and kb.fts @@ v_tsq
     order by ts_rank_cd(kb.fts, v_tsq) desc
     limit v_limit * 3
  ),
  fused as (
    select coalesce(sr.kb_id, k.kb_id) as kb_id,
           sr.distance,
           coalesce(p_semantic_weight / (p_rrf_k + sr.rnk), 0)
         + coalesce(p_keyword_weight  / (p_rrf_k + k.rnk), 0) as fused_score
      from semantic_ranked sr
      full outer join keyword k on k.kb_id = sr.kb_id
  )
  select kb.id,
         kb.source_id,
         kb.content,
         kb.metadata,
         (1 - f.distance)::float,
         f.fused_score::float
    from fused f
    join public.knowledge_base kb on kb.id = f.kb_id
   order by f.fused_score desc
   limit v_limit;
end;
$$;


-- D. PERMISSÕES ----------------------------------------------------------------
--    Funções secretas/internas: SOMENTE a chave de servidor (service_role).
do $$
declare
  r record;
begin
  for r in
    select p.oid::regprocedure::text as sig
      from pg_proc p
      join pg_namespace n on n.oid = p.pronamespace
     where n.nspname = 'public'
       and p.proname in (
         'set_social_credential_tokens', 'get_social_credential',
         'refresh_post_status', 'claim_due_post_targets', 'complete_post_target',
         'fail_post_target', 'sweep_stuck_post_targets', 'match_knowledge')
  loop
    execute format('revoke all on function %s from public, anon, authenticated', r.sig);
    execute format('grant execute on function %s to service_role', r.sig);
  end loop;
end
$$;

revoke all on public.credentials_to_renew from anon, authenticated;
grant select on public.credentials_to_renew to service_role;

comment on function public.set_social_credential_tokens(uuid, text, text, timestamptz, timestamptz)
  is 'Guarda ou renova os tokens de uma conta no Vault. Somente service_role.';
comment on function public.get_social_credential(uuid)
  is 'Devolve a credencial com tokens descriptografados. Somente service_role.';
comment on function public.claim_due_post_targets(int, text)
  is 'Reserva (sem duplicar) os envios vencidos para o n8n publicar. Somente service_role.';
comment on function public.fail_post_target(uuid, text, text, boolean, int)
  is 'Registra falha e agenda nova tentativa com espera crescente. Somente service_role.';
comment on function public.match_knowledge(uuid, vector, text, int, float, float, int)
  is 'Busca híbrida (vetor + palavras) na base de conhecimento de UMA empresa. Somente service_role.';
