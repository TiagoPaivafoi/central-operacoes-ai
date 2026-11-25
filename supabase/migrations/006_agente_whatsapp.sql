-- =====================================================================
--  PLATAFORMA SAAS MULTI-EMPRESA — MARKETING E ATENDIMENTO 360°
--  ARQUIVO 6 — AGENTE DE WHATSAPP: LÓGICA, FUNIL E PASSAGEM PARA HUMANO (etapa d)
--  Supabase / PostgreSQL 15+
-- ---------------------------------------------------------------------
--  COMO USAR
--    1. Os arquivos 001 a 005 já foram rodados.
--    2. Supabase → SQL Editor → New query → cole ESTE ARQUIVO INTEIRO → Run
--    3. Resultado esperado: uma tabela com 9 linhas, TODAS começando com "OK".
--
--  SEGURANÇA DA EXECUÇÃO
--    • Roda como uma única transação: se algum comando falhar, nada muda.
--    • Pode ser executado mais de uma vez sem duplicar nada e SEM apagar
--      o que você já tiver mudado nas configurações do agente.
--    • NÃO liga o agente. Ele continua desligado no número de teste até você
--      ligar (pelo TESTAR_AGENTE.bat ou pela aba "Atendimento" do painel).
--
--  O QUE ESTE ARQUIVO ACRESCENTA
--    1. whatsapp_agent_settings: configuração do agente POR EMPRESA (nome do
--       agente, tom de voz, regras, horário, frases fixas, palavras de saída e
--       de "falar com atendente", etapas do funil, dados a coletar, limites de
--       uso, modelo do Gemini). Nada disso fica escrito no código.
--    2. Etapas novas do funil em contacts.funnel_stage: simulation (Simulação),
--       documents (Documentos) e contracting (Contratação).
--    3. Controle de "mensagem já atendida pelo robô" (agent_status) e trava
--       por conversa (o robô nunca responde duas vezes a mesma mensagem).
--    4. Funções usadas pela função do Supabase "whatsapp-agente":
--       wa_agente_reservar, wa_agente_contexto, wa_agente_concluir,
--       wa_agente_registrar_envio, wa_agente_confirmar_envio,
--       wa_agente_falhou, wa_agente_liberar, wa_dentro_do_horario,
--       wa_token_da_conta.
--    5. Configuração inicial da Cred Finance (regras dela ficam AQUI, no banco).
--  Todas as funções só podem ser executadas pela chave de servidor
--  (service_role). Visitantes e usuários logados NÃO conseguem chamá-las.
-- =====================================================================


-- 0. CONFERÊNCIA: o arquivo 005 precisa ter sido rodado ----------------------------
do $$
begin
  if to_regprocedure('public.wa_receber_webhook(jsonb)') is null
     or not exists (select 1 from information_schema.columns
                     where table_schema = 'public' and table_name = 'contacts' and column_name = 'wa_user_id') then
    raise exception 'Rode antes o arquivo 005_recebimento_whatsapp.sql.';
  end if;
end
$$;


-- 1. ETAPAS DO FUNIL ---------------------------------------------------------------
--    Mantém as antigas (para não quebrar nada) e acrescenta as do funil decidido:
--    new = Contato · qualifying = Qualificação · simulation = Simulação ·
--    documents = Documentos · contracting = Contratação · won = Cliente · lost = Perdido
do $$
declare
  r record;
begin
  for r in
    select conname from pg_constraint
     where conrelid = 'public.contacts'::regclass and contype = 'c'
       and pg_get_constraintdef(oid) ilike '%funnel_stage%'
  loop
    execute format('alter table public.contacts drop constraint %I', r.conname);
  end loop;
end
$$;

alter table public.contacts
  add constraint contacts_funnel_stage_check
  check (funnel_stage in ('new','qualifying','qualified','simulation','documents','contracting',
                          'proposal','negotiation','won','lost'));


-- 2. CONTROLE DO ROBÔ NAS MENSAGENS E CONVERSAS ----------------------------------
--    agent_status (só mensagens recebidas): vazio = robô ainda não olhou;
--    answered/handoff/skipped/opt_out/opt_in/expired/error/ignored = já tratada.
do $$
begin
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'whatsapp_messages'
                    and column_name = 'agent_status') then
    alter table public.whatsapp_messages
      add column agent_status     text,
      add column agent_handled_at timestamptz,
      add column agent_attempts   smallint not null default 0;
    -- Mensagens que JÁ existiam (ex.: testes da etapa c) nunca serão respondidas
    update public.whatsapp_messages
       set agent_status = 'skipped', agent_handled_at = now()
     where direction = 'inbound';
  end if;
end
$$;

do $$
begin
  if not exists (select 1 from pg_constraint
                  where conname = 'whatsapp_messages_agent_status_check'
                    and conrelid = 'public.whatsapp_messages'::regclass) then
    alter table public.whatsapp_messages
      add constraint whatsapp_messages_agent_status_check
      check (agent_status is null or agent_status in
             ('answered','handoff','skipped','opt_out','opt_in','expired','error','ignored'));
  end if;
end
$$;

alter table public.whatsapp_conversations
  add column if not exists agent_locked_until timestamptz,
  add column if not exists agent_locked_by    text;

create index if not exists idx_wa_msg_agent_pending
  on public.whatsapp_messages (conversation_id, created_at)
  where direction = 'inbound' and agent_status is null;
create index if not exists idx_wa_msg_ai_tenant
  on public.whatsapp_messages (tenant_id, created_at)
  where sender = 'ai';

comment on column public.whatsapp_messages.agent_status is
  'Só mensagens recebidas: vazio = o robô ainda não tratou; senão, o que o robô fez com ela.';
comment on column public.whatsapp_conversations.agent_locked_until is
  'Trava do robô: enquanto valer, só um robô mexe nesta conversa.';


-- 3. CONFIGURAÇÃO DO AGENTE POR EMPRESA --------------------------------------------
create table if not exists public.whatsapp_agent_settings (
  tenant_id                 uuid primary key references public.tenants(id) on delete cascade,
  enabled                   boolean not null default true,     -- chave geral da empresa (o número tem a sua: agent_enabled)
  agent_name                text not null default 'Assistente virtual',
  company_summary           text,                              -- quem é a empresa, em 1 ou 2 frases
  tone                      text not null default
    'Cordial, simples e próximo. Frases curtas, sem jargão, tratando o cliente por você.',
  rules                     text not null default '',          -- regras obrigatórias da empresa
  model                     text not null default 'gemini-3.1-flash-lite',
  fallback_model            text default 'gemini-3.5-flash-lite',
  embedding_model           text not null default 'gemini-embedding-2',
  temperature               numeric(3,2),                      -- vazio = padrão do modelo (recomendado no Gemini 3)
  thinking_level            text default 'minimal',            -- vazio = padrão do modelo
  max_output_tokens         int not null default 700 check (max_output_tokens between 100 and 4000),
  gemini_timeout_ms         int not null default 15000 check (gemini_timeout_ms between 3000 and 45000),
  kb_public_count           int not null default 5 check (kb_public_count between 0 and 12),
  kb_internal_count         int not null default 3 check (kb_internal_count between 0 and 8),
  history_messages          int not null default 12 check (history_messages between 0 and 40),
  business_hours            jsonb not null default
    '{"seg":[["09:00","18:00"]],"ter":[["09:00","18:00"]],"qua":[["09:00","18:00"]],
      "qui":[["09:00","18:00"]],"sex":[["09:00","18:00"]],"sab":[],"dom":[]}'::jsonb,
  holidays                  date[] not null default '{}',
  out_of_hours_mode         text not null default 'humano'
                            check (out_of_hours_mode in ('humano','robo')),
  phrases                   jsonb not null default '{}'::jsonb,
  handoff_keywords          text[] not null default
    array['atendente','humano','falar com uma pessoa','falar com alguem','pessoa de verdade',
          'pessoa real','consultor','especialista','gerente'],
  opt_out_keywords          text[] not null default
    array['sair','parar','pare','stop','cancelar','descadastrar','nao quero mais mensagens'],
  opt_in_keywords           text[] not null default array['voltar','quero voltar'],
  blocked_patterns          text[] not null default
    array['(envie|mande|informe|passe|digite|me diga|nos diga|me fale)\W+(\w+\W+){0,4}(senha|pin|c[óo]digo|token|cvv)'],
  funnel                    jsonb not null default
    '[{"etapa":"new","nome":"Contato","quem":"robo","objetivo":"Cumprimentar, entender o que o cliente procura e pegar o primeiro nome."},
      {"etapa":"qualifying","nome":"Qualificação","quem":"robo","objetivo":"Fazer as perguntas de qualificação, uma por vez, e indicar o produto ou serviço mais adequado."},
      {"etapa":"simulation","nome":"Simulação","quem":"humano","objetivo":"A equipe prepara a proposta ou simulação."},
      {"etapa":"documents","nome":"Documentos","quem":"humano","objetivo":"A equipe pede e confere os documentos."},
      {"etapa":"contracting","nome":"Contratação","quem":"humano","objetivo":"A equipe acompanha a assinatura e a conclusão."}]'::jsonb,
  collect_fields            jsonb not null default
    '[{"campo":"nome","descricao":"primeiro nome"},
      {"campo":"interesse","descricao":"o que o cliente procura"},
      {"campo":"cidade","descricao":"cidade"}]'::jsonb,
  max_ai_replies_conversation_day int not null default 30 check (max_ai_replies_conversation_day between 1 and 500),
  max_ai_replies_tenant_day       int not null default 300 check (max_ai_replies_tenant_day between 1 and 100000),
  pending_max_age_minutes         int not null default 360 check (pending_max_age_minutes between 5 and 1440),
  created_at                timestamptz not null default now(),
  updated_at                timestamptz not null default now(),
  check (jsonb_typeof(business_hours) = 'object'),
  check (jsonb_typeof(phrases) = 'object'),
  check (jsonb_typeof(funnel) = 'array' and jsonb_array_length(funnel) > 0),
  check (jsonb_typeof(collect_fields) = 'array')
);

drop trigger if exists trg_set_updated_at on public.whatsapp_agent_settings;
create trigger trg_set_updated_at before update on public.whatsapp_agent_settings
  for each row execute function public.set_updated_at();

alter table public.whatsapp_agent_settings enable row level security;
drop policy if exists whatsapp_agent_settings_select_members on public.whatsapp_agent_settings;
create policy whatsapp_agent_settings_select_members on public.whatsapp_agent_settings
  for select to authenticated using (public.is_tenant_member(tenant_id));
drop policy if exists whatsapp_agent_settings_write_admins on public.whatsapp_agent_settings;
create policy whatsapp_agent_settings_write_admins on public.whatsapp_agent_settings
  for all to authenticated
  using (public.has_tenant_role(tenant_id, array['owner','admin']))
  with check (public.has_tenant_role(tenant_id, array['owner','admin']));
revoke all on table public.whatsapp_agent_settings from anon;
grant select, insert, update, delete on table public.whatsapp_agent_settings to authenticated;
grant all on table public.whatsapp_agent_settings to service_role;

comment on table public.whatsapp_agent_settings is
  'Configuração do agente de WhatsApp de cada empresa (regras, horário, frases, funil, limites, modelo).';

-- Frases fixas padrão (a empresa pode trocar qualquer uma; {horario} vira o horário de atendimento)
create or replace function public.wa_frases_padrao()
returns jsonb
language sql
immutable
set search_path = ''
as $$
  select jsonb_build_object(
    'passar_humano',   'Certo! Vou chamar uma pessoa da nossa equipe para continuar com você. Em breve alguém responde por aqui. 🙂',
    'fora_horario',    'Olá! Recebemos sua mensagem. Nosso atendimento funciona {horario}. Assim que o expediente começar, uma pessoa da nossa equipe responde por aqui.',
    'ia_indisponivel', 'Recebi sua mensagem! Vou chamar uma pessoa da nossa equipe para te responder. Em breve alguém fala com você por aqui.',
    'midia',           'Por enquanto eu só consigo ler mensagens de texto. 🙏 Pode me escrever? Se preferir falar com uma pessoa da equipe, é só digitar ATENDENTE.',
    'opt_out',         'Pronto, você não vai mais receber mensagens nossas por aqui. Se mudar de ideia, é só mandar VOLTAR.',
    'opt_in',          'Que bom ter você de volta! Como posso ajudar?'
  );
$$;


-- 4. FUNÇÕES AUXILIARES ------------------------------------------------------------

-- 4.1 Está dentro do horário de atendimento? (sem horário cadastrado = sempre aberto)
--     business_hours: {"seg":[["09:00","18:00"]], ..., "dom":[]}
create or replace function public.wa_dentro_do_horario(
  p_horas     jsonb,
  p_feriados  date[],
  p_fuso      text,
  p_quando    timestamptz default now()
)
returns boolean
language plpgsql
stable
set search_path = ''
as $$
declare
  v_local  timestamp;
  v_dia    text;
  v_hora   time;
  r        jsonb;
begin
  if p_horas is null or jsonb_typeof(p_horas) <> 'object' or p_horas = '{}'::jsonb then
    return true;
  end if;
  v_local := p_quando at time zone coalesce(nullif(btrim(p_fuso), ''), 'America/Sao_Paulo');
  if v_local::date = any (coalesce(p_feriados, '{}'::date[])) then
    return false;
  end if;
  v_dia  := (array['seg','ter','qua','qui','sex','sab','dom'])[extract(isodow from v_local)::int];
  v_hora := v_local::time;
  for r in select x from jsonb_array_elements(coalesce(p_horas -> v_dia, '[]'::jsonb)) x loop
    if v_hora >= (r ->> 0)::time and v_hora < (r ->> 1)::time then
      return true;
    end if;
  end loop;
  return false;
exception when others then
  return true;   -- horário mal preenchido nunca trava o atendimento
end;
$$;

-- 4.2 Configuração da empresa (cria a padrão se ainda não existir) + ajustes do número
create or replace function public.wa_agente_config(p_tenant_id uuid, p_account_id uuid default null)
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  v_cfg jsonb;
begin
  insert into public.whatsapp_agent_settings (tenant_id) values (p_tenant_id)
  on conflict (tenant_id) do nothing;

  select to_jsonb(s) - 'created_at' - 'updated_at' into v_cfg
    from public.whatsapp_agent_settings s where s.tenant_id = p_tenant_id;

  v_cfg := jsonb_set(v_cfg, '{phrases}', public.wa_frases_padrao() || coalesce(v_cfg -> 'phrases', '{}'::jsonb));

  -- ajustes específicos do número (whatsapp_accounts.agent_config), se houver
  if p_account_id is not null then
    v_cfg := v_cfg || coalesce((select a.agent_config from public.whatsapp_accounts a
                                 where a.id = p_account_id and a.tenant_id = p_tenant_id
                                   and jsonb_typeof(a.agent_config) = 'object'), '{}'::jsonb);
  end if;
  return v_cfg;
end;
$$;

-- 4.3 Token do WhatsApp da conta guardado no cofre (Vault). Vazio = usar o segredo
--     WHATSAPP_TOKEN da função (hoje: número de teste da Cred Finance).
create or replace function public.wa_token_da_conta(p_account_id uuid)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_sid  uuid;
  v_tok  text;
begin
  select a.access_token_secret_id into v_sid from public.whatsapp_accounts a where a.id = p_account_id;
  if v_sid is null then
    return null;
  end if;
  execute 'select decrypted_secret from vault.decrypted_secrets where id = $1' into v_tok using v_sid;
  return nullif(btrim(v_tok), '');
exception when others then
  return null;
end;
$$;


-- 5. RESERVA: pega conversas com mensagem nova para o robô (uma por vez, sem disputa) ---
create or replace function public.wa_agente_reservar(
  p_worker           text,
  p_limite           int  default 5,
  p_conversation_id  uuid default null
)
returns setof uuid
language plpgsql
set search_path = ''
as $$
begin
  if coalesce(btrim(p_worker), '') = '' then
    raise exception 'Informe o identificador do robô (p_worker).';
  end if;
  return query
  with candidatas as (
    select w.id
      from public.whatsapp_conversations w
     where (p_conversation_id is null or w.id = p_conversation_id)
       and (w.agent_locked_until is null or w.agent_locked_until < now())
       and exists (select 1 from public.whatsapp_messages m
                    where m.conversation_id = w.id
                      and m.direction = 'inbound'
                      and m.agent_status is null)
     order by w.last_inbound_at nulls last
     limit greatest(1, least(coalesce(p_limite, 5), 20))
     for update skip locked
  )
  update public.whatsapp_conversations w
     set agent_locked_until = now() + interval '90 seconds',
         agent_locked_by    = p_worker
    from candidatas c
   where w.id = c.id
  returning w.id;
end;
$$;

-- 5.1 Libera a trava (sempre chamada no fim, mesmo se der erro)
create or replace function public.wa_agente_liberar(p_conversation_id uuid, p_worker text)
returns void
language sql
set search_path = ''
as $$
  update public.whatsapp_conversations w
     set agent_locked_until = null, agent_locked_by = null
   where w.id = p_conversation_id and w.agent_locked_by = p_worker;
$$;


-- 6. CONTEXTO: tudo o que o robô precisa saber para responder, numa chamada só ------
create or replace function public.wa_agente_contexto(p_conversation_id uuid, p_worker text)
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  v_conv     public.whatsapp_conversations%rowtype;
  v_ct       public.contacts%rowtype;
  v_acc      public.whatsapp_accounts%rowtype;
  v_ten      public.tenants%rowtype;
  v_cfg      jsonb;
  v_hist     jsonb;
  v_pend     jsonb;
  v_resp_c   int;
  v_resp_e   int;
  v_tz       text;
  v_local    timestamp;
begin
  select * into v_conv from public.whatsapp_conversations w where w.id = p_conversation_id;
  if not found then
    raise exception 'Conversa % não encontrada.', p_conversation_id;
  end if;
  if v_conv.agent_locked_by is distinct from p_worker or v_conv.agent_locked_until < now() then
    raise exception 'A trava desta conversa não é deste robô (ou venceu).';
  end if;

  select * into v_ct  from public.contacts c where c.id = v_conv.contact_id and c.tenant_id = v_conv.tenant_id;
  select * into v_acc from public.whatsapp_accounts a where a.id = v_conv.whatsapp_account_id and a.tenant_id = v_conv.tenant_id;
  select * into v_ten from public.tenants t where t.id = v_conv.tenant_id;
  v_cfg := public.wa_agente_config(v_conv.tenant_id, v_acc.id);
  v_tz  := coalesce(nullif(btrim(v_ten.timezone), ''), 'America/Sao_Paulo');
  begin
    v_local := now() at time zone v_tz;
  exception when others then
    v_tz := 'America/Sao_Paulo';
    v_local := now() at time zone v_tz;
  end;

  -- mensagens que o robô ainda não tratou (as mais antigas primeiro)
  select coalesce(jsonb_agg(jsonb_build_object(
           'id', m.id, 'wa_message_id', m.wa_message_id, 'tipo', m.message_type,
           'texto', left(m.body, 2000), 'em', m.created_at,
           'idade_min', floor(extract(epoch from (now() - m.created_at)) / 60)::int)
           order by m.created_at, m.id), '[]'::jsonb)
    into v_pend
    from (select * from public.whatsapp_messages m
           where m.conversation_id = v_conv.id and m.direction = 'inbound' and m.agent_status is null
           order by m.created_at, m.id
           limit 30) m;

  -- histórico recente (sem as pendentes), do mais antigo para o mais novo
  select coalesce(jsonb_agg(jsonb_build_object(
           'direcao', h.direction, 'quem', h.sender, 'tipo', h.message_type,
           'texto', left(h.body, 1500), 'em', h.created_at)
           order by h.created_at, h.id), '[]'::jsonb)
    into v_hist
    from (select m.* from public.whatsapp_messages m
           where m.conversation_id = v_conv.id
             and m.message_type <> 'reaction'
             and not (m.direction = 'inbound' and m.agent_status is null)
             and not (m.direction = 'outbound' and m.delivery_status = 'failed')
           order by m.created_at desc, m.id desc
           limit greatest(0, coalesce((v_cfg ->> 'history_messages')::int, 12))) h;

  select count(*) into v_resp_c
    from public.whatsapp_messages m
   where m.conversation_id = v_conv.id and m.sender = 'ai' and m.created_at > now() - interval '24 hours';

  select count(*) into v_resp_e
    from public.whatsapp_messages m
   where m.tenant_id = v_conv.tenant_id and m.sender = 'ai'
     and m.created_at >= (date_trunc('day', v_local) at time zone v_tz);

  return jsonb_build_object(
    'conversa', jsonb_build_object(
        'id', v_conv.id, 'status', v_conv.status, 'ai_state', v_conv.ai_state,
        'last_inbound_at', v_conv.last_inbound_at, 'handoff_reason', v_conv.handoff_reason,
        'janela_aberta', coalesce(v_conv.last_inbound_at > now() - interval '24 hours', false)),
    'contato', jsonb_build_object(
        'id', v_ct.id, 'wa_id', v_ct.wa_id, 'wa_user_id', v_ct.wa_user_id, 'nome', v_ct.name,
        'etapa', v_ct.funnel_stage, 'opt_out', v_ct.opt_out_at is not null),
    'conta', jsonb_build_object(
        'id', v_acc.id, 'phone_number_id', v_acc.phone_number_id, 'status', v_acc.status,
        'provider', v_acc.provider, 'agent_enabled', v_acc.agent_enabled,
        'tem_token_proprio', v_acc.access_token_secret_id is not null),
    'empresa', jsonb_build_object(
        'id', v_ten.id, 'nome', v_ten.name, 'status', v_ten.status, 'fuso', v_tz),
    'config', v_cfg,
    'pendentes', v_pend,
    'historico', v_hist,
    'uso', jsonb_build_object('respostas_ia_conversa_24h', v_resp_c, 'respostas_ia_empresa_hoje', v_resp_e),
    'agora', jsonb_build_object(
        'local', to_char(v_local, 'DD/MM/YYYY HH24:MI'),
        'dia_semana', (array['segunda-feira','terça-feira','quarta-feira','quinta-feira','sexta-feira','sábado','domingo'])
                      [extract(isodow from v_local)::int],
        'dentro_horario', public.wa_dentro_do_horario(v_cfg -> 'business_hours',
                            array(select jsonb_array_elements_text(coalesce(v_cfg -> 'holidays', '[]'::jsonb)))::date[],
                            v_tz, now()))
  );
end;
$$;


-- 7. REGISTRA UMA RESPOSTA ANTES DE ENVIAR (fica "na fila" até a Meta confirmar) -----
create or replace function public.wa_agente_registrar_envio(
  p_conversation_id    uuid,
  p_sender             text,
  p_body               text,
  p_ai_model           text    default null,
  p_prompt_tokens      int     default null,
  p_completion_tokens  int     default null,
  p_chunk_ids          uuid[]  default '{}',
  p_payload            jsonb   default '{}'::jsonb
)
returns uuid
language plpgsql
set search_path = ''
as $$
declare
  v_tenant  uuid;
  v_id      uuid;
begin
  if p_sender not in ('ai','human','system') then
    raise exception 'Remetente inválido: %', p_sender;
  end if;
  if coalesce(btrim(p_body), '') = '' then
    raise exception 'Resposta vazia.';
  end if;
  select w.tenant_id into v_tenant from public.whatsapp_conversations w where w.id = p_conversation_id;
  if v_tenant is null then
    raise exception 'Conversa % não encontrada.', p_conversation_id;
  end if;
  insert into public.whatsapp_messages
    (tenant_id, conversation_id, direction, sender, message_type, body, payload, delivery_status,
     ai_model, ai_prompt_tokens, ai_completion_tokens, retrieved_chunk_ids)
  values
    (v_tenant, p_conversation_id, 'outbound', p_sender, 'text', left(p_body, 4096),
     coalesce(p_payload, '{}'::jsonb), 'queued',
     p_ai_model, p_prompt_tokens, p_completion_tokens, coalesce(p_chunk_ids, '{}'::uuid[]))
  returning id into v_id;
  return v_id;
end;
$$;


-- 8. CONFIRMA O ENVIO (ou registra a falha) ----------------------------------------
--    Os avisos de status da Meta podem chegar ANTES desta confirmação: por isso o
--    status final é calculado também a partir de whatsapp_message_statuses.
create or replace function public.wa_agente_confirmar_envio(
  p_message_id  uuid,
  p_wamid       text,
  p_erro        text default null
)
returns text
language plpgsql
set search_path = ''
as $$
declare
  v_msg     public.whatsapp_messages%rowtype;
  v_ordem   text[] := array['received','queued','sent','delivered','read'];
  v_final   text := 'sent';
  v_err     text;
  r         record;
begin
  select * into v_msg from public.whatsapp_messages m where m.id = p_message_id and m.direction = 'outbound';
  if not found then
    raise exception 'Mensagem enviada % não encontrada.', p_message_id;
  end if;

  if coalesce(btrim(p_wamid), '') = '' then
    update public.whatsapp_messages m
       set delivery_status = 'failed', error = left(coalesce(p_erro, 'falha ao enviar'), 500)
     where m.id = p_message_id;
    update public.whatsapp_conversations w
       set status = case when w.status = 'bot' then 'waiting_human' else w.status end,
           handoff_reason = case when w.status = 'bot'
                                 then left('Falha ao enviar resposta: ' || coalesce(p_erro, ''), 300)
                                 else w.handoff_reason end
     where w.id = v_msg.conversation_id;
    insert into public.event_logs (tenant_id, source, level, event_type, entity_type, entity_id, message)
    values (v_msg.tenant_id, 'whatsapp_agent', 'error', 'whatsapp.send_failed', 'whatsapp_conversation',
            v_msg.conversation_id, left('Resposta não enviada: ' || coalesce(p_erro, ''), 500));
    return 'failed';
  end if;

  for r in select s.status, s.errors from public.whatsapp_message_statuses s
            where s.tenant_id = v_msg.tenant_id and s.wa_message_id = p_wamid loop
    if r.status = 'failed' then
      v_err := left(coalesce(r.errors -> 0 ->> 'title', r.errors -> 0 ->> 'message', 'falha'), 500);
    elsif coalesce(array_position(v_ordem, r.status), 0) > coalesce(array_position(v_ordem, v_final), 0) then
      v_final := r.status;
    end if;
  end loop;
  if v_err is not null and v_final not in ('delivered','read') then
    v_final := 'failed';
  end if;

  update public.whatsapp_messages m
     set wa_message_id = p_wamid, delivery_status = v_final,
         error = case when v_final = 'failed' then v_err else null end
   where m.id = p_message_id;
  update public.whatsapp_conversations w
     set last_outbound_at = greatest(w.last_outbound_at, now())
   where w.id = v_msg.conversation_id;
  return v_final;
end;
$$;


-- 9. CONCLUI O ATENDIMENTO DAS MENSAGENS (tudo de uma vez) -------------------------
--    • marca as mensagens como tratadas (nunca são respondidas de novo);
--    • passa para humano (só se a conversa ainda estiver com o robô);
--    • guarda o que o robô coletou (ai_state) e avança a etapa do funil
--      (só para frente; "won"/"lost" ficam só com a equipe);
--    • registra saída (SAIR/PARAR) ou volta (VOLTAR) do cliente.
create or replace function public.wa_agente_concluir(
  p_conversation_id  uuid,
  p_worker           text,
  p_message_ids      uuid[],
  p_resultado        text,
  p_novo_status      text  default null,
  p_motivo           text  default null,
  p_ai_state         jsonb default null,
  p_etapa            text  default null,
  p_contato          text  default null
)
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  v_conv     public.whatsapp_conversations%rowtype;
  v_ct       public.contacts%rowtype;
  v_funil    jsonb;
  v_pos_at   int;
  v_pos_nova int;
  v_marcadas int := 0;
  v_mudou    boolean := false;
  v_etapa_ok text;
  v_estado   jsonb;
begin
  if p_resultado not in ('answered','handoff','skipped','opt_out','opt_in','expired','error','ignored') then
    raise exception 'Resultado inválido: %', p_resultado;
  end if;
  if p_novo_status is not null and p_novo_status <> 'waiting_human' then
    raise exception 'O robô só pode passar a conversa para "waiting_human".';
  end if;
  if p_contato is not null and p_contato not in ('opt_out','opt_in') then
    raise exception 'Valor inválido para p_contato: %', p_contato;
  end if;

  select * into v_conv from public.whatsapp_conversations w where w.id = p_conversation_id for update;
  if not found then
    raise exception 'Conversa % não encontrada.', p_conversation_id;
  end if;
  if v_conv.agent_locked_by is distinct from p_worker then
    raise exception 'A trava desta conversa não é deste robô.';
  end if;

  update public.whatsapp_messages m
     set agent_status = p_resultado, agent_handled_at = now()
   where m.conversation_id = p_conversation_id
     and m.direction = 'inbound'
     and m.agent_status is null
     and m.id = any (coalesce(p_message_ids, '{}'::uuid[]));
  get diagnostics v_marcadas = row_count;

  -- memória do robô: junta o novo com o que já tinha ("dados" é somado, não trocado)
  if p_ai_state is not null and jsonb_typeof(p_ai_state) = 'object' then
    v_estado := coalesce(v_conv.ai_state, '{}'::jsonb) || (p_ai_state - 'dados');
    if jsonb_typeof(p_ai_state -> 'dados') = 'object' then
      v_estado := jsonb_set(v_estado, '{dados}',
                  coalesce(v_conv.ai_state -> 'dados', '{}'::jsonb) || (p_ai_state -> 'dados'));
    end if;
    update public.whatsapp_conversations w set ai_state = v_estado where w.id = p_conversation_id;
  end if;

  -- passagem para humano
  if p_novo_status = 'waiting_human' and v_conv.status = 'bot' then
    update public.whatsapp_conversations w
       set status = 'waiting_human', handoff_reason = left(coalesce(p_motivo, 'robô passou para humano'), 300)
     where w.id = p_conversation_id;
    v_mudou := true;
    insert into public.event_logs (tenant_id, source, level, event_type, entity_type, entity_id, message)
    values (v_conv.tenant_id, 'whatsapp_agent', 'info', 'whatsapp.handoff', 'whatsapp_conversation',
            p_conversation_id, left('Conversa passada para humano: ' || coalesce(p_motivo, ''), 500));
  end if;

  -- contato: etapa do funil (só para frente) e saída/volta
  select * into v_ct from public.contacts c where c.id = v_conv.contact_id for update;
  if p_etapa is not null and v_ct.funnel_stage not in ('won','lost') then
    v_funil := public.wa_agente_config(v_conv.tenant_id) -> 'funnel';
    select e.ordem into v_pos_nova
      from jsonb_array_elements(v_funil) with ordinality e(item, ordem)
     where e.item ->> 'etapa' = p_etapa;
    select e.ordem into v_pos_at
      from jsonb_array_elements(v_funil) with ordinality e(item, ordem)
     where e.item ->> 'etapa' = v_ct.funnel_stage;
    if v_pos_nova is not null and p_etapa not in ('won','lost')
       and (v_pos_at is null or v_pos_nova > v_pos_at) then
      update public.contacts c set funnel_stage = p_etapa where c.id = v_ct.id;
      v_etapa_ok := p_etapa;
    end if;
  end if;

  if p_contato = 'opt_out' then
    update public.contacts c set opt_out_at = coalesce(c.opt_out_at, now()) where c.id = v_ct.id;
    insert into public.event_logs (tenant_id, source, level, event_type, entity_type, entity_id, message)
    values (v_conv.tenant_id, 'whatsapp_agent', 'info', 'whatsapp.opt_out', 'contact', v_ct.id,
            'Cliente pediu para não receber mais mensagens.');
  elsif p_contato = 'opt_in' then
    update public.contacts c set opt_out_at = null, opt_in_at = now() where c.id = v_ct.id;
  end if;

  update public.contacts c set last_interaction_at = greatest(c.last_interaction_at, now()) where c.id = v_ct.id;

  return jsonb_build_object('marcadas', v_marcadas, 'passou_para_humano', v_mudou, 'etapa', v_etapa_ok);
end;
$$;


-- 10. FALHA INESPERADA DO ROBÔ: tenta de novo até 3 vezes; depois passa para humano ---
create or replace function public.wa_agente_falhou(
  p_conversation_id  uuid,
  p_worker           text,
  p_message_ids      uuid[],
  p_erro             text
)
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  v_conv      public.whatsapp_conversations%rowtype;
  v_desistiu  int := 0;
begin
  select * into v_conv from public.whatsapp_conversations w where w.id = p_conversation_id for update;
  if not found or v_conv.agent_locked_by is distinct from p_worker then
    return jsonb_build_object('ignorado', true);
  end if;

  update public.whatsapp_messages m
     set agent_attempts = m.agent_attempts + 1
   where m.conversation_id = p_conversation_id and m.direction = 'inbound'
     and m.agent_status is null and m.id = any (coalesce(p_message_ids, '{}'::uuid[]));

  update public.whatsapp_messages m
     set agent_status = 'error', agent_handled_at = now()
   where m.conversation_id = p_conversation_id and m.direction = 'inbound'
     and m.agent_status is null and m.agent_attempts >= 3;
  get diagnostics v_desistiu = row_count;

  if v_desistiu > 0 and v_conv.status = 'bot' then
    update public.whatsapp_conversations w
       set status = 'waiting_human', handoff_reason = left('Erro no robô: ' || coalesce(p_erro, ''), 300)
     where w.id = p_conversation_id;
  end if;

  insert into public.event_logs (tenant_id, source, level, event_type, entity_type, entity_id, message)
  values (v_conv.tenant_id, 'whatsapp_agent', 'error', 'whatsapp.agent_error', 'whatsapp_conversation',
          p_conversation_id, left('Erro no robô: ' || coalesce(p_erro, ''), 500));

  update public.whatsapp_conversations w
     set agent_locked_until = now() + interval '20 seconds'   -- pequena pausa antes de tentar de novo
   where w.id = p_conversation_id;

  return jsonb_build_object('desistiu', v_desistiu);
end;
$$;


-- 11. PERMISSÕES: somente a chave de servidor (service_role) ----------------------
do $$
declare
  r record;
begin
  for r in
    select p.oid::regprocedure::text as sig
      from pg_proc p
      join pg_namespace n on n.oid = p.pronamespace
     where n.nspname = 'public'
       and p.proname in ('wa_frases_padrao','wa_dentro_do_horario','wa_agente_config','wa_token_da_conta',
                         'wa_agente_reservar','wa_agente_liberar','wa_agente_contexto',
                         'wa_agente_registrar_envio','wa_agente_confirmar_envio',
                         'wa_agente_concluir','wa_agente_falhou')
  loop
    execute format('revoke all on function %s from public, anon, authenticated', r.sig);
    execute format('grant execute on function %s to service_role', r.sig);
  end loop;
end
$$;


-- 12. CONFIGURAÇÃO INICIAL DA CRED FINANCE ------------------------------------------
--     Só é gravada na PRIMEIRA vez (rodar de novo não apaga o que você mudou).
insert into public.whatsapp_agent_settings
  (tenant_id, agent_name, company_summary, tone, rules, blocked_patterns, funnel, collect_fields, business_hours)
select t.id,
  'Assistente virtual da Cred Finance',
  'A Cred Finance Soluções Financeiras é uma intermediadora de crédito do Rio de Janeiro: compara as opções de vários bancos e parceiros e leva o cliente à solução mais adequada, com atendimento humano do início ao fim.',
  'Cordial, simples e próximo, como um consultor de confiança. Frases curtas, sem jargão bancário, tratando o cliente por você.',
  $regras$- Nunca prometa nem sugira aprovação garantida: tudo depende da análise do parceiro.
- Nunca cite taxa, juros, valor de parcela, prazo de pagamento nem prazo de liberação: isso só sai na simulação feita pela equipe.
- Nunca peça senha, código de verificação, número de cartão, dados bancários, CPF nem foto de documento. A equipe pede o que for preciso na etapa de documentos.
- Nunca peça depósito, Pix, boleto ou qualquer pagamento antecipado. Se o cliente perguntar, diga que a Cred Finance nunca pede pagamento antecipado para liberar crédito.
- Não fale de outras empresas, bancos ou concorrentes, nem compare. Parceiros só podem ser citados como aparecem nos trechos da base.
- A Cred Finance é intermediadora: não empresta dinheiro próprio; conecta o cliente ao parceiro certo.
- Verifique, com naturalidade, se o cliente também se encaixa em outro produto do portfólio.
- Garantia de veículo, Mercado Livre de Energia e Capital de Giro ainda não estão confirmados: colete os dados e passe para a equipe.$regras$,
  array[
    '(envie|mande|informe|passe|digite|me diga|nos diga|me fale)\W+(\w+\W+){0,4}(senha|pin|c[óo]digo|token|cvv)',
    '\d+([.,]\d+)?\s*%',
    '\b(ao m[êe]s|a\.m\.|ao ano|a\.a\.)',
    'aprova[çc][ãa]o\s+(garantida|certa|imediata|na hora)',
    'garant(o|imos|ida|ido)\W+(\w+\W+){0,2}aprova',
    '\b\d+\s*(dias?(\s+[úu]teis)?|meses|anos|parcelas|x)\b',
    '\bmesmo dia\b',
    '(fa[çc]a|fazer|envie|mande|transfira|pague|realize|deposite)\W+(\w+\W+){0,4}(dep[óo]sito|pix|pagamento|boleto|transfer[êe]ncia)'
  ],
  '[{"etapa":"new","nome":"Contato","quem":"robo","objetivo":"Cumprimentar, entender o que o cliente procura e pegar o primeiro nome."},
    {"etapa":"qualifying","nome":"Qualificação","quem":"robo","objetivo":"Descobrir, uma pergunta por vez: o perfil (aposentado ou pensionista do INSS, servidor público, CLT, autônomo ou empresa), para que precisa do dinheiro, o valor aproximado e se tem imóvel ou veículo. Depois, indicar o produto mais adequado do portfólio."},
    {"etapa":"simulation","nome":"Simulação","quem":"humano","objetivo":"Quando o cliente estiver qualificado e quiser seguir, a equipe faz a simulação com os parceiros."},
    {"etapa":"documents","nome":"Documentos","quem":"humano","objetivo":"A equipe pede e confere os documentos."},
    {"etapa":"contracting","nome":"Contratação","quem":"humano","objetivo":"A equipe acompanha a assinatura e a liberação."}]'::jsonb,
  '[{"campo":"nome","descricao":"primeiro nome"},
    {"campo":"perfil","descricao":"aposentado/pensionista INSS, servidor público, CLT, autônomo ou empresa"},
    {"campo":"produto","descricao":"produto do portfólio que mais combina"},
    {"campo":"objetivo","descricao":"para que precisa do dinheiro ou da solução"},
    {"campo":"valor_aproximado","descricao":"valor aproximado que precisa"},
    {"campo":"garantia","descricao":"se tem imóvel ou veículo (quitado ou financiado)"},
    {"campo":"cidade","descricao":"cidade e estado"}]'::jsonb,
  '{"seg":[["09:00","18:00"]],"ter":[["09:00","18:00"]],"qua":[["09:00","18:00"]],
    "qui":[["09:00","18:00"]],"sex":[["09:00","18:00"]],"sab":[],"dom":[]}'::jsonb
  from public.tenants t
 where t.slug = 'cred-finance'
on conflict (tenant_id) do nothing;


-- 13. TESTE INTERNO: simula uma conversa completa e DESFAZ tudo em seguida ---------
create temp table if not exists _teste_006 (resultado text);
truncate _teste_006;

do $$
declare
  v_payload  jsonb;
  v_conv     uuid;
  v_ctx      jsonb;
  v_ids      uuid[];
  v_out      uuid;
  v_final    text;
  v_res      jsonb;
  v_etapa    text;
  v_status   text;
  v_pend     int;
  v_reserva  uuid;
begin
  if not exists (select 1 from public.whatsapp_accounts where phone_number_id = 'SEU_PHONE_NUMBER_ID') then
    insert into _teste_006 values ('FALHA — número de teste não cadastrado (rode o 005)');
    return;
  end if;

  v_payload := jsonb_build_object(
    'object', 'whatsapp_business_account',
    'entry', jsonb_build_array(jsonb_build_object(
      'id', 'SEU_WABA_ID',
      'changes', jsonb_build_array(jsonb_build_object(
        'field', 'messages',
        'value', jsonb_build_object(
          'messaging_product', 'whatsapp',
          'metadata', jsonb_build_object('display_phone_number', '15551628212', 'phone_number_id', 'SEU_PHONE_NUMBER_ID'),
          'contacts', jsonb_build_array(jsonb_build_object(
             'profile', jsonb_build_object('name', 'Teste interno 006'),
             'wa_id', '5500000000998', 'user_id', 'BR.TESTE006')),
          'messages', jsonb_build_array(jsonb_build_object(
             'from', '5500000000998', 'id', 'wamid.TESTE-006-INTERNO',
             'timestamp', extract(epoch from now())::bigint::text,
             'type', 'text', 'text', jsonb_build_object('body', 'Quero saber do consignado')))))))));

  begin
    perform public.wa_receber_webhook(v_payload);
    select w.id into v_conv
      from public.whatsapp_conversations w
      join public.contacts c on c.id = w.contact_id
     where c.wa_id = '5500000000998' and w.status <> 'closed';

    select r into v_reserva from public.wa_agente_reservar('teste-006', 5, v_conv) r;
    if v_reserva is distinct from v_conv then
      raise exception using message = '__FALHOU__ reserva';
    end if;
    -- segunda reserva da mesma conversa tem de vir vazia (trava)
    if exists (select 1 from public.wa_agente_reservar('outro-robo', 5, v_conv)) then
      raise exception using message = '__FALHOU__ trava';
    end if;

    v_ctx := public.wa_agente_contexto(v_conv, 'teste-006');
    if jsonb_array_length(v_ctx -> 'pendentes') <> 1
       or v_ctx -> 'config' ->> 'model' is null
       or v_ctx -> 'config' -> 'phrases' ->> 'passar_humano' is null then
      raise exception using message = '__FALHOU__ contexto ' || left(v_ctx::text, 200);
    end if;
    select array_agg((p ->> 'id')::uuid) into v_ids from jsonb_array_elements(v_ctx -> 'pendentes') p;

    v_out := public.wa_agente_registrar_envio(v_conv, 'ai', 'Olá! Posso te ajudar com o consignado.',
               'teste', 100, 20, '{}'::uuid[], '{}'::jsonb);
    -- aviso "entregue" chega ANTES da confirmação
    insert into public.whatsapp_message_statuses (tenant_id, wa_message_id, status)
    select tenant_id, 'wamid.TESTE-006-OUT', 'delivered' from public.whatsapp_conversations where id = v_conv;
    v_final := public.wa_agente_confirmar_envio(v_out, 'wamid.TESTE-006-OUT', null);

    v_res := public.wa_agente_concluir(v_conv, 'teste-006', v_ids, 'handoff', 'waiting_human', 'teste',
               '{"dados":{"produto":"consignado"}}'::jsonb, 'qualifying', null);
    perform public.wa_agente_liberar(v_conv, 'teste-006');

    select c.funnel_stage, w.status into v_etapa, v_status
      from public.whatsapp_conversations w join public.contacts c on c.id = w.contact_id where w.id = v_conv;
    select count(*) into v_pend from public.whatsapp_messages m
     where m.conversation_id = v_conv and m.direction = 'inbound' and m.agent_status is null;

    if v_final = 'delivered' and v_etapa = 'qualifying' and v_status = 'waiting_human'
       and v_pend = 0 and (v_res ->> 'marcadas')::int = 1 then
      raise exception using message = '__TESTE_OK__';
    else
      raise exception using message = '__FALHOU__ ' || coalesce(v_final, '?') || '/' || coalesce(v_etapa, '?')
                                     || '/' || coalesce(v_status, '?') || '/' || v_pend || '/' || coalesce(v_res::text, '');
    end if;
  exception when others then
    if sqlerrm = '__TESTE_OK__' then
      insert into _teste_006 values ('OK — reservou, travou, respondeu, passou para humano e desfez o teste');
    else
      insert into _teste_006 values ('FALHA — ' || left(sqlerrm, 300));
    end if;
  end;
end
$$;


-- 14. VERIFICAÇÃO (resultado esperado: 9 linhas, todas "OK") -----------------------
select ordem, verificacao, resultado from (
  select 1 as ordem, 'Etapas novas do funil (simulation, documents, contracting)' as verificacao,
         case when exists (select 1 from pg_constraint
                            where conname = 'contacts_funnel_stage_check'
                              and pg_get_constraintdef(oid) like '%contracting%')
              then 'OK' else 'FALHA' end as resultado
  union all
  select 2, 'Controle do robô nas mensagens e conversas',
         case when (select count(*) from information_schema.columns
                     where table_schema = 'public'
                       and ((table_name = 'whatsapp_messages' and column_name in ('agent_status','agent_handled_at','agent_attempts'))
                         or (table_name = 'whatsapp_conversations' and column_name in ('agent_locked_until','agent_locked_by')))) = 5
              then 'OK' else 'FALHA' end
  union all
  select 3, 'Tabela de configuração do agente (com RLS)',
         case when exists (select 1 from pg_tables where schemaname = 'public'
                            and tablename = 'whatsapp_agent_settings' and rowsecurity)
              then 'OK' else 'FALHA' end
  union all
  select 4, 'Funções novas criadas (11)',
         case when (select count(distinct p.proname) from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                     where n.nspname = 'public'
                       and p.proname in ('wa_frases_padrao','wa_dentro_do_horario','wa_agente_config','wa_token_da_conta',
                                         'wa_agente_reservar','wa_agente_liberar','wa_agente_contexto',
                                         'wa_agente_registrar_envio','wa_agente_confirmar_envio',
                                         'wa_agente_concluir','wa_agente_falhou')) = 11
              then 'OK' else 'FALHA' end
  union all
  select 5, 'Funções bloqueadas para visitantes e usuários logados',
         case when not exists (
                select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                 where n.nspname = 'public'
                   and p.proname like 'wa\_%'
                   and (has_function_privilege('anon', p.oid, 'execute')
                     or has_function_privilege('authenticated', p.oid, 'execute')))
              then 'OK' else 'FALHA' end
  union all
  select 6, 'Visitantes (anon) sem acesso à configuração do agente',
         case when not has_table_privilege('anon', 'public.whatsapp_agent_settings', 'select')
              then 'OK' else 'FALHA' end
  union all
  select 7, 'Configuração da Cred Finance gravada (regras no banco)',
         case when exists (select 1 from public.whatsapp_agent_settings s
                             join public.tenants t on t.id = s.tenant_id
                            where t.slug = 'cred-finance' and s.rules <> '')
              then 'OK' else 'FALHA — empresa cred-finance não encontrada' end
  union all
  select 8, 'Agente no número de teste',
         case when exists (select 1 from public.whatsapp_accounts
                            where phone_number_id = 'SEU_PHONE_NUMBER_ID' and agent_enabled)
              then 'OK — LIGADO'
              else 'OK — desligado (ligue pelo TESTAR_AGENTE.bat ou pela aba Atendimento)' end
  union all
  select 9, 'Teste interno (conversa simulada)',
         coalesce((select resultado from _teste_006 limit 1), 'FALHA — teste não rodou')
) v
order by ordem;
