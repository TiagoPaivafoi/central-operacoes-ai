-- =====================================================================
--  PLATAFORMA SAAS MULTI-EMPRESA — MARKETING E ATENDIMENTO 360°
--  ARQUIVO 4 — BASE DE CONHECIMENTO DO AGENTE DE WHATSAPP (etapa b)
--  Supabase / PostgreSQL 15+ com pgvector
-- ---------------------------------------------------------------------
--  COMO USAR
--    1. Os arquivos 001, 002 e 003 já foram rodados (eles criaram as tabelas
--       knowledge_sources e knowledge_base e a extensão pgvector).
--    2. Supabase → SQL Editor → New query → cole ESTE ARQUIVO INTEIRO → Run
--    3. Resultado esperado: uma tabela com 6 linhas, TODAS começando com "OK".
--
--  SEGURANÇA DA EXECUÇÃO
--    • Roda como uma única transação: se algum comando falhar, nada muda.
--    • Pode ser executado mais de uma vez sem duplicar nada.
--    • Não apaga nenhum dado que já exista.
--
--  O QUE ESTE ARQUIVO ACRESCENTA
--    1. Visibilidade de cada trecho: 'publico' (o agente pode falar ao cliente)
--       ou 'interno' (só orienta o agente; nunca é citado ao cliente).
--    2. Chave do documento (source_key): o mesmo arquivo nunca vira dois
--       documentos; quando muda, é substituído.
--    3. kb_substituir_documento → troca os trechos de um documento de uma vez
--       só (se der erro no meio, fica a versão antiga inteira).
--    4. kb_remover_ausentes     → tira da base os arquivos apagados da pasta.
--    5. match_knowledge (nova versão) → busca na base DA EMPRESA; por padrão
--       devolve só trechos públicos.
--  Todas as funções só podem ser executadas pela chave de servidor
--  (service_role). Visitantes e usuários logados NÃO conseguem chamá-las.
-- =====================================================================


-- 0. CONFERÊNCIA: as tabelas do arquivo 001 precisam existir ----------------------
do $$
begin
  if to_regclass('public.knowledge_sources') is null
     or to_regclass('public.knowledge_base') is null then
    raise exception 'Rode antes os arquivos 001_estrutura_base.sql e 002_funcoes_robustez.sql.';
  end if;
  if not exists (select 1 from pg_extension where extname = 'vector') then
    raise exception 'A extensão pgvector não está instalada. Rode o arquivo 001 de novo.';
  end if;
end
$$;


-- 1. NOVAS COLUNAS ---------------------------------------------------------------
alter table public.knowledge_sources
  add column if not exists source_key text,
  add column if not exists visibility text not null default 'publico';

alter table public.knowledge_base
  add column if not exists visibility text not null default 'publico';

do $$
begin
  if not exists (select 1 from pg_constraint
                  where conname = 'knowledge_sources_visibility_check'
                    and conrelid = 'public.knowledge_sources'::regclass) then
    alter table public.knowledge_sources
      add constraint knowledge_sources_visibility_check
      check (visibility in ('publico', 'interno'));
  end if;
  if not exists (select 1 from pg_constraint
                  where conname = 'knowledge_base_visibility_check'
                    and conrelid = 'public.knowledge_base'::regclass) then
    alter table public.knowledge_base
      add constraint knowledge_base_visibility_check
      check (visibility in ('publico', 'interno'));
  end if;
end
$$;

-- Um arquivo = um documento por empresa
create unique index if not exists uq_knowledge_sources_key
  on public.knowledge_sources (tenant_id, source_key)
  where source_key is not null;

create index if not exists idx_knowledge_base_tenant_visibility
  on public.knowledge_base (tenant_id, visibility)
  where is_active;


-- 2. TROCA ATÔMICA DOS TRECHOS DE UM DOCUMENTO -------------------------------------
--    p_chunks = lista JSON: [{"content": "...", "embedding": [768 números],
--                             "token_count": 120, "metadata": {...}}, ...]
create or replace function public.kb_substituir_documento(
  p_tenant_id        uuid,
  p_source_key       text,
  p_title            text,
  p_source_type      text,
  p_visibility       text,
  p_content_hash     text,
  p_embedding_model  text,
  p_metadata         jsonb,
  p_chunks           jsonb
)
returns uuid
language plpgsql
set search_path = public, extensions
as $$
declare
  v_source_id  uuid;
  v_total      int;
  v_ruins      int;
begin
  -- Conferências antes de mexer em qualquer coisa
  if not exists (select 1 from public.tenants t where t.id = p_tenant_id) then
    raise exception 'Empresa não encontrada (tenant_id %).', p_tenant_id;
  end if;
  if coalesce(btrim(p_source_key), '') = '' then
    raise exception 'Documento sem chave (source_key).';
  end if;
  if p_visibility not in ('publico', 'interno') then
    raise exception 'Visibilidade inválida: % (use publico ou interno).', p_visibility;
  end if;
  if p_chunks is null or jsonb_typeof(p_chunks) <> 'array' or jsonb_array_length(p_chunks) = 0 then
    raise exception 'Documento % sem trechos para gravar.', p_source_key;
  end if;
  if jsonb_array_length(p_chunks) > 2000 then
    raise exception 'Documento % grande demais (% trechos; máximo 2000).',
      p_source_key, jsonb_array_length(p_chunks);
  end if;

  select count(*),
         count(*) filter (
           where coalesce(btrim(c->>'content'), '') = ''
              or jsonb_typeof(c->'embedding') <> 'array'
              or jsonb_array_length(c->'embedding') <> 768)
    into v_total, v_ruins
    from jsonb_array_elements(p_chunks) as c;
  if v_ruins > 0 then
    raise exception 'Documento %: % de % trechos sem texto ou sem vetor de 768 números.',
      p_source_key, v_ruins, v_total;
  end if;

  -- Outro documento da mesma empresa com conteúdo idêntico?
  if exists (select 1 from public.knowledge_sources s
              where s.tenant_id = p_tenant_id
                and s.content_hash = p_content_hash
                and s.source_key is distinct from p_source_key) then
    raise exception 'O conteúdo de % é idêntico ao de outro documento já carregado. Apague a cópia.',
      p_source_key;
  end if;

  -- Cria ou atualiza o documento (trava a linha para evitar duas cargas ao mesmo tempo)
  select s.id into v_source_id
    from public.knowledge_sources s
   where s.tenant_id = p_tenant_id and s.source_key = p_source_key
   for update;

  if v_source_id is null then
    insert into public.knowledge_sources
      (tenant_id, source_key, title, source_type, visibility, content_hash, status, error, metadata)
    values
      (p_tenant_id, p_source_key, p_title, coalesce(p_source_type, 'document'), p_visibility,
       p_content_hash, 'ready', null, coalesce(p_metadata, '{}'::jsonb))
    returning id into v_source_id;
  else
    update public.knowledge_sources s
       set title        = p_title,
           source_type  = coalesce(p_source_type, 'document'),
           visibility   = p_visibility,
           content_hash = p_content_hash,
           status       = 'ready',
           error        = null,
           metadata     = coalesce(p_metadata, '{}'::jsonb)
     where s.id = v_source_id;

    delete from public.knowledge_base kb where kb.source_id = v_source_id;
  end if;

  insert into public.knowledge_base
    (tenant_id, source_id, chunk_index, content, token_count, embedding,
     embedding_model, metadata, visibility, is_active)
  select p_tenant_id,
         v_source_id,
         (e.ordem - 1)::int,
         e.c->>'content',
         nullif(e.c->>'token_count', '')::int,
         ((e.c->'embedding')::text)::extensions.vector(768),
         p_embedding_model,
         coalesce(e.c->'metadata', '{}'::jsonb),
         p_visibility,
         true
    from jsonb_array_elements(p_chunks) with ordinality as e(c, ordem);

  return v_source_id;
end;
$$;


-- 3. REMOVE DA BASE OS ARQUIVOS QUE SAÍRAM DA PASTA --------------------------------
--    Só mexe em documentos carregados de arquivo (chave começando por "arquivo:").
--    Lista vazia é recusada, para nunca apagar a base inteira por engano.
create or replace function public.kb_remover_ausentes(
  p_tenant_id         uuid,
  p_chaves_presentes  text[]
)
returns int
language plpgsql
set search_path = public, extensions
as $$
declare
  v_qtd int;
begin
  if p_chaves_presentes is null or cardinality(p_chaves_presentes) = 0 then
    raise exception 'Lista de arquivos vazia: nada foi removido (proteção contra apagar tudo).';
  end if;

  delete from public.knowledge_sources s
   where s.tenant_id = p_tenant_id
     and s.source_key like 'arquivo:%'
     and not (s.source_key = any (p_chaves_presentes));
  get diagnostics v_qtd = row_count;
  return v_qtd;
end;
$$;


-- 4. BUSCA (nova versão de match_knowledge) ---------------------------------------
--    Igual à anterior (significado + palavras, filtrada por empresa), com 2 novidades:
--      • p_incluir_interno = false (padrão) → devolve só trechos públicos;
--      • devolve também o título do documento e a visibilidade do trecho.
--    Remove qualquer versão anterior (inclusive a do arquivo 002) antes de criar a nova.
do $$
declare
  r record;
begin
  for r in
    select p.oid::regprocedure::text as sig
      from pg_proc p
      join pg_namespace n on n.oid = p.pronamespace
     where n.nspname = 'public' and p.proname = 'match_knowledge'
  loop
    execute format('drop function %s', r.sig);
  end loop;
end
$$;

create or replace function public.match_knowledge(
  p_tenant_id         uuid,
  p_query_embedding   extensions.vector(768),
  p_query_text        text     default '',
  p_match_count       int      default 6,
  p_semantic_weight   float    default 1.0,
  p_keyword_weight    float    default 1.0,
  p_rrf_k             int      default 50,
  p_incluir_interno   boolean  default false
)
returns table (
  id            uuid,
  source_id     uuid,
  source_title  text,
  visibility    text,
  content       text,
  metadata      jsonb,
  similarity    float,
  score         float
)
language plpgsql
set search_path = public, extensions
as $$
#variable_conflict use_column
declare
  v_limit  int := least(greatest(coalesce(p_match_count, 6), 1), 30);
  v_tsq    tsquery;
  v_todos  boolean := coalesce(p_incluir_interno, false);
begin
  if p_tenant_id is null then
    raise exception 'Informe a empresa (p_tenant_id).';
  end if;

  -- pgvector 0.8 ou mais novo: continua varrendo o índice até achar resultados
  -- suficientes depois do filtro por empresa.
  begin
    perform set_config('hnsw.iterative_scan', 'relaxed_order', true);
  exception when others then
    null;
  end;

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
       and (v_todos or kb.visibility = 'publico')
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
       and (v_todos or kb.visibility = 'publico')
       and v_tsq is not null
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
         ks.title,
         kb.visibility,
         kb.content,
         kb.metadata,
         (1 - f.distance)::float,
         f.fused_score::float
    from fused f
    join public.knowledge_base kb   on kb.id = f.kb_id
    join public.knowledge_sources ks on ks.id = kb.source_id
   where kb.tenant_id = p_tenant_id          -- segunda trava: nunca outra empresa
   order by f.fused_score desc
   limit v_limit;
end;
$$;


-- 5. PERMISSÕES: somente a chave de servidor (service_role) -----------------------
do $$
declare
  r record;
begin
  for r in
    select p.oid::regprocedure::text as sig
      from pg_proc p
      join pg_namespace n on n.oid = p.pronamespace
     where n.nspname = 'public'
       and p.proname in ('kb_substituir_documento', 'kb_remover_ausentes', 'match_knowledge')
  loop
    execute format('revoke all on function %s from public, anon, authenticated', r.sig);
    execute format('grant execute on function %s to service_role', r.sig);
  end loop;
end
$$;

comment on column public.knowledge_sources.source_key is 'Chave única do documento na empresa (ex.: arquivo:01_portfolio.md).';
comment on column public.knowledge_sources.visibility is 'publico = o agente pode falar ao cliente; interno = só orienta o agente.';
comment on column public.knowledge_base.visibility    is 'Copiada do documento. A busca padrão devolve só publico.';
comment on function public.kb_substituir_documento(uuid, text, text, text, text, text, text, jsonb, jsonb)
  is 'Troca, numa transação só, todos os trechos de um documento. Somente service_role.';
comment on function public.kb_remover_ausentes(uuid, text[])
  is 'Remove da base os documentos de arquivo que saíram da pasta. Somente service_role.';
comment on function public.match_knowledge(uuid, extensions.vector, text, int, float, float, int, boolean)
  is 'Busca híbrida (vetor + palavras) na base de UMA empresa; só trechos públicos por padrão. Somente service_role.';


-- 6. VERIFICAÇÃO (resultado esperado: 6 linhas, todas "OK") -----------------------
select ordem, verificacao, resultado from (
  select 1 as ordem, 'pgvector instalado' as verificacao,
         coalesce((select 'OK — versão ' || extversion from pg_extension where extname = 'vector'),
                  'FALHA') as resultado
  union all
  select 2, 'Colunas de visibilidade e chave criadas',
         case when (select count(*) from information_schema.columns
                     where table_schema = 'public'
                       and ((table_name = 'knowledge_sources' and column_name in ('source_key','visibility'))
                         or (table_name = 'knowledge_base' and column_name = 'visibility'))) = 3
              then 'OK' else 'FALHA' end
  union all
  select 3, 'Funções novas criadas (3)',
         case when (select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                     where n.nspname = 'public'
                       and p.proname in ('kb_substituir_documento','kb_remover_ausentes','match_knowledge')) = 3
              then 'OK' else 'FALHA — confira se sobrou uma versão antiga de match_knowledge' end
  union all
  select 4, 'Funções bloqueadas para visitantes e usuários logados',
         case when not exists (
                select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                 where n.nspname = 'public'
                   and p.proname in ('kb_substituir_documento','kb_remover_ausentes','match_knowledge')
                   and (has_function_privilege('anon', p.oid, 'execute')
                     or has_function_privilege('authenticated', p.oid, 'execute')))
              then 'OK' else 'FALHA' end
  union all
  select 5, 'RLS ligado nas tabelas da base',
         case when (select count(*) from pg_tables
                     where schemaname = 'public'
                       and tablename in ('knowledge_sources','knowledge_base')
                       and rowsecurity) = 2
              then 'OK' else 'FALHA' end
  union all
  select 6, 'Empresa Cred Finance cadastrada',
         case when exists (select 1 from public.tenants where slug = 'cred-finance')
              then 'OK' else 'FALHA — cadastre a empresa no painel' end
) v
order by ordem;
