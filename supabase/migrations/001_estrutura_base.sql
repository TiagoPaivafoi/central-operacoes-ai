-- =====================================================================
--  PLATAFORMA SAAS MULTI-EMPRESA — MARKETING E ATENDIMENTO 360°
--  ARQUIVO 1 DE 3 — ESTRUTURA BASE (tabelas, segurança e armazenamento)
--  Supabase / PostgreSQL 15+
-- ---------------------------------------------------------------------
--  COMO USAR
--    1. Supabase → SQL Editor → New query
--    2. Cole ESTE ARQUIVO INTEIRO (sem selecionar só um trecho) e clique em Run
--    3. Resultado esperado: "Success. No rows returned"
--
--  SEGURANÇA DA EXECUÇÃO
--    • Roda como uma única transação: se algum comando falhar, NADA é criado
--      e você pode corrigir e rodar de novo.
--    • Pode ser executado mais de uma vez sem duplicar nada.
--
--  REGRAS DESTE DESENHO
--    1. Toda tabela de negócio carrega o tenant_id (a empresa dona do dado).
--    2. As tabelas "filhas" usam chave estrangeira COMPOSTA (tenant_id, id):
--       o próprio banco impede ligar o dado de uma empresa ao de outra.
--    3. RLS (Row Level Security) ligado em TODAS as tabelas.
--    4. Tokens e senhas NUNCA ficam em tabelas comuns: ficam no Supabase
--       Vault (veja o arquivo 2).
-- =====================================================================


-- 0. EXTENSÕES ----------------------------------------------------------------
create extension if not exists vector with schema extensions;   -- busca por similaridade (RAG)

-- Cofre de segredos. Se der erro nesta linha: Supabase → Integrations → Vault
-- → Enable, e rode este arquivo de novo.
create extension if not exists supabase_vault cascade;


-- 1. FUNÇÃO AUXILIAR: mantém a coluna updated_at sempre correta ----------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;


-- 2. TABELAS ------------------------------------------------------------------

-- 2.1 EMPRESAS (tenants) — raiz do isolamento multi-empresa
create table if not exists public.tenants (
  id              uuid primary key default gen_random_uuid(),
  slug            text not null unique
                  check (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),       -- ex.: cred-finance
  name            text not null,
  legal_name      text,
  status          text not null default 'onboarding'
                  check (status in ('onboarding','active','paused','suspended','archived')),
  plan            text not null default 'trial'
                  check (plan in ('trial','starter','pro','enterprise')),
  timezone        text not null default 'America/Sao_Paulo',
  locale          text not null default 'pt-BR',
  -- identidade da marca (usada pela IA ao criar textos, artes e respostas)
  logo_url        text,
  brand_colors    jsonb not null default '{}'::jsonb,   -- ex.: {"primaria":"#0A5CFF","secundaria":"#111827"}
  tone_of_voice   text,                                  -- como a marca fala
  business_rules  text,                                  -- regras de negócio e o que NÃO pode ser dito
  support_info    jsonb not null default '{}'::jsonb,    -- horários, telefone, e-mail, link de suporte
  settings        jsonb not null default '{}'::jsonb,    -- ajustes livres (modelo de IA, funil etc.)
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

-- 2.2 USUÁRIOS DE CADA EMPRESA (para quando houver login por empresa)
create table if not exists public.tenant_members (
  tenant_id   uuid not null references public.tenants(id) on delete cascade,
  user_id     uuid not null references auth.users(id) on delete cascade,
  role        text not null default 'editor'
              check (role in ('owner','admin','editor','viewer')),
  created_at  timestamptz not null default now(),
  primary key (tenant_id, user_id)
);

-- 2.3 CONTAS DE REDES SOCIAIS (social_credentials)
--     IMPORTANTE: aqui ficam só REFERÊNCIAS ao cofre (Vault). O token em si
--     nunca é gravado nesta tabela.
create table if not exists public.social_credentials (
  id                        uuid primary key default gen_random_uuid(),
  tenant_id                 uuid not null references public.tenants(id) on delete cascade,
  platform                  text not null
                            check (platform in ('instagram','facebook','linkedin','tiktok','x')),
  label                     text not null,                 -- nome amigável, ex.: "Instagram @cred_finance_"
  external_account_id       text not null,                 -- ID na rede (Page ID, IG ID, URN, open_id...)
  external_account_name     text,                          -- @usuario ou nome da página
  parent_external_id        text,                          -- opcional: ex. Page ID ligada ao Instagram
  token_kind                text not null default 'oauth_user'
                            check (token_kind in ('system_user','page_token','oauth_user','api_key')),
  scopes                    text[] not null default '{}',
  access_token_secret_id    uuid,                          -- referência ao Vault
  refresh_token_secret_id   uuid,                          -- referência ao Vault
  access_token_expires_at   timestamptz,                   -- vazio = não expira
  refresh_token_expires_at  timestamptz,
  status                    text not null default 'pending'
                            check (status in ('pending','active','expired','revoked','error')),
  last_validated_at         timestamptz,
  last_error                text,
  metadata                  jsonb not null default '{}'::jsonb,
  created_at                timestamptz not null default now(),
  updated_at                timestamptz not null default now(),
  unique (tenant_id, id),
  unique (tenant_id, platform, external_account_id)
);

-- 2.4 SERVIDOR DE MÍDIA CENTRAL (arquivos com URL pública HTTPS)
create table if not exists public.media_assets (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         uuid not null references public.tenants(id) on delete cascade,
  kind              text not null check (kind in ('image','video','audio','document')),
  storage_provider  text not null default 'supabase'
                    check (storage_provider in ('supabase','cloudinary','s3','external')),
  storage_bucket    text,
  storage_path      text,                                  -- ex.: <tenant_id>/2026/10/arquivo.jpg
  public_url        text not null check (public_url ~ '^https://'),
  mime_type         text,
  size_bytes        bigint check (size_bytes is null or size_bytes >= 0),
  width             int,
  height            int,
  duration_seconds  numeric(8,2),
  alt_text          text,
  origin            text not null default 'upload'
                    check (origin in ('upload','ai_generated','template','external')),
  metadata          jsonb not null default '{}'::jsonb,
  created_by        uuid references auth.users(id) on delete set null,
  created_at        timestamptz not null default now(),
  unique (tenant_id, id)
);

-- 2.5 POSTAGENS E AGENDAMENTO (posts_schedule)
create table if not exists public.posts_schedule (
  id               uuid primary key default gen_random_uuid(),
  tenant_id        uuid not null references public.tenants(id) on delete cascade,
  title            text,                                   -- nome interno
  post_format      text not null check (post_format in ('image','carousel','video','text')),
  caption          text not null default '',               -- legenda/texto principal (com hashtags)
  link_url         text,
  scheduled_at     timestamptz not null default now(),     -- "agora" = data/hora atual
  status           text not null default 'draft'
                   check (status in ('draft','pending_approval','scheduled','publishing',
                                     'published','partially_published','failed','cancelled')),
  approved_by      uuid references auth.users(id) on delete set null,
  approved_at      timestamptz,
  generated_by_ai  boolean not null default false,
  ai_context       jsonb not null default '{}'::jsonb,     -- tema, prompt e modelo usados
  created_by       uuid references auth.users(id) on delete set null,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (tenant_id, id)
);

-- 2.6 MÍDIAS DE CADA POSTAGEM (ordem = ordem do carrossel)
create table if not exists public.post_media (
  tenant_id   uuid not null,
  post_id     uuid not null,
  asset_id    uuid not null,
  sort_order  smallint not null default 0 check (sort_order >= 0),
  alt_text    text,
  primary key (post_id, sort_order),
  constraint post_media_post_fk
    foreign key (tenant_id, post_id)  references public.posts_schedule (tenant_id, id) on delete cascade,
  constraint post_media_asset_fk
    foreign key (tenant_id, asset_id) references public.media_assets (tenant_id, id) on delete restrict
);

-- 2.7 DESTINOS DA POSTAGEM: uma linha por rede/conta (a "fila" de publicação)
--     Se uma rede falhar, as outras seguem normalmente.
create table if not exists public.post_targets (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         uuid not null,
  post_id           uuid not null,
  credential_id     uuid not null,
  caption_override  text,                                  -- legenda específica desta rede (opcional)
  options           jsonb not null default '{}'::jsonb,    -- opções da rede (privacidade, capa etc.)
  status            text not null default 'pending'
                    check (status in ('pending','processing','published','failed','cancelled')),
  attempt_count     int not null default 0 check (attempt_count >= 0),
  max_attempts      int not null default 5 check (max_attempts between 1 and 20),
  retry_after       timestamptz,                           -- só vale depois desta data (espera entre tentativas)
  locked_at         timestamptz,                           -- quando um robô pegou este item
  locked_by         text,
  idempotency_key   uuid not null default gen_random_uuid(),
  external_post_id  text,                                  -- ID da publicação na rede
  external_url      text,
  published_at      timestamptz,
  last_error        text,
  last_error_code   text,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  unique (post_id, credential_id),
  constraint post_targets_post_fk
    foreign key (tenant_id, post_id)       references public.posts_schedule (tenant_id, id) on delete cascade,
  constraint post_targets_credential_fk
    foreign key (tenant_id, credential_id) references public.social_credentials (tenant_id, id) on delete restrict
);

-- 2.8 NÚMEROS DE WHATSAPP DE CADA EMPRESA
create table if not exists public.whatsapp_accounts (
  id                      uuid primary key default gen_random_uuid(),
  tenant_id               uuid not null references public.tenants(id) on delete cascade,
  provider                text not null default 'meta_cloud'
                          check (provider in ('meta_cloud','evolution','waha')),
  label                   text not null,
  display_phone_number    text,                            -- ex.: +55 21 99999-9999
  phone_number_id         text,                            -- Meta: identifica a empresa quando chega mensagem
  waba_id                 text,                            -- Meta: conta do WhatsApp Business
  instance_name           text,                            -- Evolution/WAHA: nome da instância/sessão
  api_base_url            text,                            -- Evolution/WAHA: endereço do servidor
  access_token_secret_id  uuid,                            -- referência ao Vault
  status                  text not null default 'pending'
                          check (status in ('pending','active','paused','disconnected','error')),
  agent_enabled           boolean not null default false,  -- agente de IA ligado?
  agent_config            jsonb not null default '{}'::jsonb,  -- nome do agente, horários, regras de transferência
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now(),
  unique (tenant_id, id),
  check (
       (provider = 'meta_cloud' and phone_number_id is not null)
    or (provider in ('evolution','waha') and instance_name is not null)
  )
);

-- 2.9 CONTATOS / LEADS (funil de vendas)
create table if not exists public.contacts (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid not null references public.tenants(id) on delete cascade,
  wa_id                text,                               -- número em formato internacional, só dígitos
  name                 text,
  email                text,
  funnel_stage         text not null default 'new'
                       check (funnel_stage in ('new','qualifying','qualified','proposal',
                                               'negotiation','won','lost')),
  lead_score           smallint check (lead_score is null or lead_score between 0 and 100),
  qualification        jsonb not null default '{}'::jsonb, -- dados coletados pela IA na conversa
  tags                 text[] not null default '{}',
  opt_in_at            timestamptz,                        -- consentimento (LGPD / regras do WhatsApp)
  opt_out_at           timestamptz,
  owner_user_id        uuid references auth.users(id) on delete set null,
  last_interaction_at  timestamptz,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  unique (tenant_id, id),
  unique (tenant_id, wa_id)
);

-- 2.10 CONVERSAS DE WHATSAPP (whatsapp_conversations)
create table if not exists public.whatsapp_conversations (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid not null,
  whatsapp_account_id  uuid not null,
  contact_id           uuid not null,
  status               text not null default 'bot'
                       check (status in ('bot','waiting_human','human','closed')),
  assigned_user_id     uuid references auth.users(id) on delete set null,
  handoff_reason       text,
  last_inbound_at      timestamptz,                        -- última msg do cliente (janela de 24h do WhatsApp)
  last_outbound_at     timestamptz,
  ai_summary           text,                               -- resumo corrido: memória do agente
  ai_state             jsonb not null default '{}'::jsonb, -- etapa do funil e dados já coletados
  closed_at            timestamptz,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  unique (tenant_id, id),
  constraint wa_conv_account_fk
    foreign key (tenant_id, whatsapp_account_id) references public.whatsapp_accounts (tenant_id, id) on delete restrict,
  constraint wa_conv_contact_fk
    foreign key (tenant_id, contact_id)          references public.contacts (tenant_id, id) on delete cascade
);

-- Só pode existir UMA conversa aberta por contato em cada número
create unique index if not exists uq_wa_conv_one_open
  on public.whatsapp_conversations (whatsapp_account_id, contact_id)
  where status <> 'closed';

-- 2.11 MENSAGENS DO WHATSAPP
create table if not exists public.whatsapp_messages (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null,
  conversation_id       uuid not null,
  direction             text not null check (direction in ('inbound','outbound')),
  sender                text not null check (sender in ('contact','ai','human','system')),
  wa_message_id         text,                              -- ID na Meta/Evolution (evita mensagem duplicada)
  message_type          text not null default 'text'
                        check (message_type in ('text','image','audio','video','document','sticker',
                                                'location','contacts','interactive','button',
                                                'template','reaction','unsupported')),
  body                  text,                              -- texto (ou transcrição do áudio)
  media_url             text,
  payload               jsonb not null default '{}'::jsonb,
  delivery_status       text not null default 'received'
                        check (delivery_status in ('received','queued','sent','delivered','read','failed')),
  error                 text,
  ai_model              text,                              -- auditoria da IA
  ai_prompt_tokens      int,
  ai_completion_tokens  int,
  retrieved_chunk_ids   uuid[] not null default '{}',      -- trechos da base de conhecimento usados na resposta
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  constraint wa_msg_conversation_fk
    foreign key (tenant_id, conversation_id) references public.whatsapp_conversations (tenant_id, id) on delete cascade
);

-- O WhatsApp pode entregar a mesma mensagem mais de uma vez: este índice barra a duplicata
create unique index if not exists uq_wa_msg_dedupe
  on public.whatsapp_messages (tenant_id, wa_message_id)
  where wa_message_id is not null;

-- 2.12 DOCUMENTOS DA BASE DE CONHECIMENTO (origem)
create table if not exists public.knowledge_sources (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references public.tenants(id) on delete cascade,
  title         text not null,
  source_type   text not null default 'document'
                check (source_type in ('document','faq','url','product','policy','manual')),
  source_url    text,
  storage_path  text,
  content_hash  text,                                      -- evita processar o mesmo conteúdo duas vezes
  status        text not null default 'pending'
                check (status in ('pending','processing','ready','error','archived')),
  error         text,
  metadata      jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (tenant_id, id)
);

create unique index if not exists uq_knowledge_sources_hash
  on public.knowledge_sources (tenant_id, content_hash)
  where content_hash is not null;

-- 2.13 BASE DE CONHECIMENTO (knowledge_base) — trechos já vetorizados para o RAG
--      Vetor de 768 dimensões (ex.: gemini-embedding-001 com outputDimensionality = 768)
create table if not exists public.knowledge_base (
  id               uuid primary key default gen_random_uuid(),
  tenant_id        uuid not null,
  source_id        uuid not null,
  chunk_index      int not null check (chunk_index >= 0),
  content          text not null,
  token_count      int,
  embedding        vector(768),
  embedding_model  text,                                   -- qual modelo gerou o vetor (para reprocessar no futuro)
  metadata         jsonb not null default '{}'::jsonb,     -- seção, página, categoria...
  is_active        boolean not null default true,
  fts              tsvector generated always as (to_tsvector('portuguese', coalesce(content, ''))) stored,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (source_id, chunk_index),
  constraint kb_source_fk
    foreign key (tenant_id, source_id) references public.knowledge_sources (tenant_id, id) on delete cascade
);

-- 2.14 REGISTRO DE EVENTOS (alimenta o "terminal de logs" do Streamlit)
create table if not exists public.event_logs (
  id           bigint generated always as identity primary key,
  tenant_id    uuid references public.tenants(id) on delete cascade,
  source       text not null,                              -- 'streamlit', 'n8n', 'supabase', 'whatsapp_agent'...
  level        text not null default 'info' check (level in ('debug','info','warning','error')),
  event_type   text not null,                              -- ex.: 'post.published', 'token.renewed'
  entity_type  text,
  entity_id    uuid,
  message      text,
  context      jsonb not null default '{}'::jsonb,
  created_at   timestamptz not null default now()
);


-- 3. ÍNDICES (deixam as consultas rápidas mesmo com muitas empresas) ------------
create index if not exists idx_tenant_members_user        on public.tenant_members (user_id);
create index if not exists idx_social_credentials_tenant  on public.social_credentials (tenant_id, platform);
create index if not exists idx_media_assets_tenant        on public.media_assets (tenant_id, created_at desc);
create index if not exists idx_posts_schedule_tenant      on public.posts_schedule (tenant_id, scheduled_at desc);
create index if not exists idx_posts_schedule_due         on public.posts_schedule (scheduled_at)
  where status in ('scheduled','publishing');
create index if not exists idx_post_media_asset           on public.post_media (asset_id);
create index if not exists idx_post_targets_post          on public.post_targets (post_id);
create index if not exists idx_post_targets_credential    on public.post_targets (credential_id);
create index if not exists idx_post_targets_open          on public.post_targets (retry_after)
  where status in ('pending','processing');
create index if not exists idx_whatsapp_accounts_tenant   on public.whatsapp_accounts (tenant_id);
create unique index if not exists uq_whatsapp_accounts_phone_number_id
  on public.whatsapp_accounts (phone_number_id) where phone_number_id is not null;
create unique index if not exists uq_whatsapp_accounts_instance
  on public.whatsapp_accounts ((coalesce(api_base_url, '')), instance_name) where instance_name is not null;
create index if not exists idx_contacts_tenant_stage      on public.contacts (tenant_id, funnel_stage);
create index if not exists idx_wa_conv_tenant_status      on public.whatsapp_conversations (tenant_id, status, updated_at desc);
create index if not exists idx_wa_conv_contact            on public.whatsapp_conversations (contact_id);
create index if not exists idx_wa_conv_account            on public.whatsapp_conversations (whatsapp_account_id);
create index if not exists idx_wa_msg_conversation        on public.whatsapp_messages (conversation_id, created_at);
create index if not exists idx_knowledge_sources_tenant   on public.knowledge_sources (tenant_id, status);
create index if not exists idx_knowledge_base_tenant      on public.knowledge_base (tenant_id) where is_active;
create index if not exists idx_knowledge_base_source      on public.knowledge_base (source_id);
create index if not exists idx_knowledge_base_fts         on public.knowledge_base using gin (fts);
create index if not exists idx_knowledge_base_embedding   on public.knowledge_base using hnsw (embedding vector_cosine_ops);
create index if not exists idx_event_logs_tenant          on public.event_logs (tenant_id, created_at desc);
create index if not exists idx_event_logs_problems        on public.event_logs (created_at desc)
  where level in ('warning','error');


-- 4. GATILHOS: atualizam updated_at automaticamente ----------------------------
do $$
declare
  t text;
begin
  foreach t in array array[
    'tenants','social_credentials','posts_schedule','post_targets','whatsapp_accounts',
    'contacts','whatsapp_conversations','whatsapp_messages','knowledge_sources','knowledge_base'
  ] loop
    execute format('drop trigger if exists trg_set_updated_at on public.%I', t);
    execute format(
      'create trigger trg_set_updated_at before update on public.%I
         for each row execute function public.set_updated_at()', t);
  end loop;
end
$$;


-- 5. SEGURANÇA — QUEM PODE VER/EDITAR O QUÊ ------------------------------------

-- 5.1 Funções que as regras (políticas) usam para saber se o usuário pertence à empresa
create or replace function public.is_tenant_member(p_tenant_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
      from public.tenant_members m
     where m.tenant_id = p_tenant_id
       and m.user_id = (select auth.uid())
  );
$$;

create or replace function public.has_tenant_role(p_tenant_id uuid, p_roles text[])
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
      from public.tenant_members m
     where m.tenant_id = p_tenant_id
       and m.user_id = (select auth.uid())
       and m.role = any (p_roles)
  );
$$;

revoke all on function public.is_tenant_member(uuid)         from public, anon;
revoke all on function public.has_tenant_role(uuid, text[])  from public, anon;
grant execute on function public.is_tenant_member(uuid)        to authenticated, service_role;
grant execute on function public.has_tenant_role(uuid, text[]) to authenticated, service_role;

-- 5.2 Liga o RLS em TODAS as tabelas
--     (sem política = ninguém acessa; a chave de servidor service_role ignora o RLS)
do $$
declare
  t text;
begin
  foreach t in array array[
    'tenants','tenant_members','social_credentials','media_assets','posts_schedule',
    'post_media','post_targets','whatsapp_accounts','contacts','whatsapp_conversations',
    'whatsapp_messages','knowledge_sources','knowledge_base','event_logs'
  ] loop
    execute format('alter table public.%I enable row level security', t);
  end loop;
end
$$;

-- 5.3 Políticas
--     Hoje o n8n e o Streamlit usam a chave de servidor (service_role). As políticas
--     abaixo valem para quando houver login por empresa: cada usuário só enxerga
--     os dados das empresas das quais é membro.

drop policy if exists tenants_select_members on public.tenants;
create policy tenants_select_members on public.tenants
  for select to authenticated
  using (public.is_tenant_member(id));

drop policy if exists tenant_members_select_members on public.tenant_members;
create policy tenant_members_select_members on public.tenant_members
  for select to authenticated
  using (public.is_tenant_member(tenant_id));

drop policy if exists event_logs_select_members on public.event_logs;
create policy event_logs_select_members on public.event_logs
  for select to authenticated
  using (tenant_id is not null and public.is_tenant_member(tenant_id));

do $$
declare
  t text;
begin
  -- LEITURA: qualquer membro da empresa
  foreach t in array array[
    'social_credentials','media_assets','posts_schedule','post_media','post_targets',
    'whatsapp_accounts','contacts','whatsapp_conversations','whatsapp_messages',
    'knowledge_sources','knowledge_base'
  ] loop
    execute format('drop policy if exists %I on public.%I', t || '_select_members', t);
    execute format(
      'create policy %I on public.%I for select to authenticated
         using (public.is_tenant_member(tenant_id))',
      t || '_select_members', t);
  end loop;

  -- ESCRITA de conteúdo: somente dono, admin ou editor da empresa
  foreach t in array array[
    'media_assets','posts_schedule','post_media','contacts','knowledge_sources'
  ] loop
    execute format('drop policy if exists %I on public.%I', t || '_write_editors', t);
    execute format(
      'create policy %I on public.%I for all to authenticated
         using (public.has_tenant_role(tenant_id, array[''owner'',''admin'',''editor'']))
         with check (public.has_tenant_role(tenant_id, array[''owner'',''admin'',''editor'']))',
      t || '_write_editors', t);
  end loop;
end
$$;

-- 5.4 Permissões das tabelas: visitantes anônimos NUNCA acessam nada
grant usage on schema public to authenticated, service_role;

do $$
declare
  t text;
begin
  foreach t in array array[
    'tenants','tenant_members','social_credentials','media_assets','posts_schedule',
    'post_media','post_targets','whatsapp_accounts','contacts','whatsapp_conversations',
    'whatsapp_messages','knowledge_sources','knowledge_base','event_logs'
  ] loop
    execute format('revoke all on table public.%I from anon', t);
    execute format('grant select, insert, update, delete on table public.%I to authenticated', t);
    execute format('grant all on table public.%I to service_role', t);
  end loop;
end
$$;


-- 6. ARMAZENAMENTO DE MÍDIA (Supabase Storage) ---------------------------------
--    Bucket público "media": a Meta e as demais redes precisam de uma URL pública
--    HTTPS para baixar imagens e vídeos. Organize os arquivos por empresa:
--    media/<tenant_id>/<ano>/<mês>/<arquivo>
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'media', 'media', true, 104857600,
  array['image/jpeg','image/png','image/webp','image/gif','video/mp4','video/quicktime']
)
on conflict (id) do nothing;


-- 7. PRIMEIRA EMPRESA: a Cred Finance, que já validou o MVP -------------------
insert into public.tenants (slug, name, status)
values ('cred-finance', 'Cred Finance', 'active')
on conflict (slug) do nothing;


-- 8. DESCRIÇÕES (aparecem no Table Editor do Supabase) -------------------------
comment on table public.tenants                is 'Empresas clientes da plataforma. Raiz do isolamento multi-empresa.';
comment on table public.tenant_members         is 'Usuários que pertencem a cada empresa, com seu papel (owner, admin, editor, viewer).';
comment on table public.social_credentials     is 'Contas de redes sociais de cada empresa. Guarda só referências ao Vault, nunca o token.';
comment on table public.media_assets           is 'Servidor de mídia central: imagens e vídeos com URL pública HTTPS.';
comment on table public.posts_schedule         is 'Postagens e agendamento (imagem, carrossel, vídeo ou texto).';
comment on table public.post_media             is 'Mídias de cada postagem, em ordem (ordem do carrossel).';
comment on table public.post_targets           is 'Fila de publicação: uma linha por rede/conta, com tentativas, erros e resultado.';
comment on table public.whatsapp_accounts      is 'Números de WhatsApp de cada empresa e configuração do agente de IA.';
comment on table public.contacts               is 'Contatos e leads com etapa do funil de vendas.';
comment on table public.whatsapp_conversations is 'Conversas de WhatsApp (IA, aguardando humano, humano ou encerrada).';
comment on table public.whatsapp_messages      is 'Mensagens de WhatsApp, com proteção contra duplicidade e auditoria da IA.';
comment on table public.knowledge_sources      is 'Documentos de origem da base de conhecimento de cada empresa.';
comment on table public.knowledge_base         is 'Trechos vetorizados (pgvector) usados pelo agente de IA para responder com RAG.';
comment on table public.event_logs             is 'Registro de eventos e erros do sistema (terminal de logs).';

comment on column public.social_credentials.access_token_secret_id  is 'Referência ao Supabase Vault. O token nunca fica nesta tabela.';
comment on column public.social_credentials.refresh_token_secret_id is 'Referência ao Supabase Vault. O token nunca fica nesta tabela.';
comment on column public.whatsapp_accounts.access_token_secret_id   is 'Referência ao Supabase Vault. O token nunca fica nesta tabela.';
