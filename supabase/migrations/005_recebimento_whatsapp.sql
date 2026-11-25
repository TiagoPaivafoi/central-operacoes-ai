-- =====================================================================
--  PLATAFORMA SAAS MULTI-EMPRESA — MARKETING E ATENDIMENTO 360°
--  ARQUIVO 5 — RECEBIMENTO DAS MENSAGENS DO WHATSAPP (etapa c)
--  Supabase / PostgreSQL 15+
-- ---------------------------------------------------------------------
--  COMO USAR
--    1. Os arquivos 001, 002, 003 e 004 já foram rodados.
--    2. Supabase → SQL Editor → New query → cole ESTE ARQUIVO INTEIRO → Run
--    3. Resultado esperado: uma tabela com 8 linhas, TODAS começando com "OK".
--
--  SEGURANÇA DA EXECUÇÃO
--    • Roda como uma única transação: se algum comando falhar, nada muda.
--    • Pode ser executado mais de uma vez sem duplicar nada.
--    • Não apaga nenhum dado que já exista.
--
--  O QUE ESTE ARQUIVO ACRESCENTA
--    1. contacts: colunas wa_user_id (identificador do WhatsApp por empresa,
--       o "BSUID", que a Meta manda desde abril/2026 e que pode vir SEM o
--       telefone) e wa_username (nome de usuário do WhatsApp, se houver).
--    2. whatsapp_message_statuses: histórico dos avisos de status
--       (enviada, entregue, lida, falhou), sem repetir o mesmo aviso.
--    3. whatsapp_webhook_events: cópia bruta de cada aviso recebido da Meta
--       (para auditoria e para reprocessar se algo der erro). Guardada por
--       30 dias e só acessível pela chave de servidor.
--    4. wa_receber_webhook   → recebe o aviso inteiro, grava contato, conversa,
--                              mensagem e status, SEM duplicar. Se der erro,
--                              o aviso fica guardado com o erro (nada se perde).
--    5. wa_reprocessar_eventos → tenta de novo os avisos que deram erro.
--    6. wa_apagar_testes     → apaga só os dados do teste automático.
--    7. Cadastro do número de teste da Meta (+1 555 162 8212) na Cred Finance.
--  Todas as funções só podem ser executadas pela chave de servidor
--  (service_role). Visitantes e usuários logados NÃO conseguem chamá-las.
-- =====================================================================


-- 0. CONFERÊNCIA: as tabelas do arquivo 001 precisam existir ----------------------
do $$
begin
  if to_regclass('public.whatsapp_accounts') is null
     or to_regclass('public.contacts') is null
     or to_regclass('public.whatsapp_conversations') is null
     or to_regclass('public.whatsapp_messages') is null then
    raise exception 'Rode antes o arquivo 001_estrutura_base.sql.';
  end if;
end
$$;


-- 1. CONTATOS: identificador novo do WhatsApp (BSUID) ------------------------------
alter table public.contacts
  add column if not exists wa_user_id  text,
  add column if not exists wa_username text;

create unique index if not exists uq_contacts_wa_user_id
  on public.contacts (tenant_id, wa_user_id)
  where wa_user_id is not null;

comment on column public.contacts.wa_user_id  is 'BSUID: identificador do cliente no WhatsApp desta empresa (pode vir sem o telefone).';
comment on column public.contacts.wa_username is 'Nome de usuário do WhatsApp (@usuario), quando o cliente tiver.';


-- 2. HISTÓRICO DOS AVISOS DE STATUS --------------------------------------------------
create table if not exists public.whatsapp_message_statuses (
  id                   bigint generated always as identity primary key,
  tenant_id            uuid not null references public.tenants(id) on delete cascade,
  whatsapp_account_id  uuid,
  wa_message_id        text not null,                     -- ID da mensagem na Meta (wamid...)
  status               text not null,                     -- sent, delivered, read, failed...
  status_at            timestamptz,                       -- hora informada pela Meta
  recipient_wa_id      text,                              -- telefone do cliente (pode faltar)
  recipient_user_id    text,                              -- BSUID do cliente
  errors               jsonb,                             -- detalhe da falha, se houver
  pricing              jsonb,                             -- categoria/cobrança informada pela Meta
  payload              jsonb not null default '{}'::jsonb,
  created_at           timestamptz not null default now(),
  constraint wa_status_account_fk
    foreign key (tenant_id, whatsapp_account_id) references public.whatsapp_accounts (tenant_id, id) on delete set null (whatsapp_account_id),
  unique (tenant_id, wa_message_id, status)               -- o mesmo aviso nunca entra duas vezes
);

create index if not exists idx_wa_status_message
  on public.whatsapp_message_statuses (tenant_id, wa_message_id);
create index if not exists idx_wa_status_created
  on public.whatsapp_message_statuses (tenant_id, created_at desc);

comment on table public.whatsapp_message_statuses
  is 'Avisos de status da Meta (enviada, entregue, lida, falhou) de cada mensagem enviada.';


-- 3. CÓPIA BRUTA DOS AVISOS (auditoria e reprocessamento) ---------------------------
create table if not exists public.whatsapp_webhook_events (
  id            bigint generated always as identity primary key,
  received_at   timestamptz not null default now(),
  status        text not null default 'received'
                check (status in ('received','processed','ignored','error')),
  attempts      int not null default 0,
  error         text,
  summary       jsonb not null default '{}'::jsonb,
  payload       jsonb not null,
  processed_at  timestamptz
);

create index if not exists idx_wa_events_received on public.whatsapp_webhook_events (received_at);
create index if not exists idx_wa_events_errors   on public.whatsapp_webhook_events (received_at)
  where status = 'error';

comment on table public.whatsapp_webhook_events
  is 'Cópia bruta dos avisos do webhook do WhatsApp (30 dias). Somente service_role.';


-- 4. SEGURANÇA DAS TABELAS NOVAS ----------------------------------------------------
alter table public.whatsapp_message_statuses enable row level security;
alter table public.whatsapp_webhook_events   enable row level security;

-- Status: membros da empresa podem LER (para a futura aba "Atendimento")
drop policy if exists whatsapp_message_statuses_select_members on public.whatsapp_message_statuses;
create policy whatsapp_message_statuses_select_members on public.whatsapp_message_statuses
  for select to authenticated
  using (public.is_tenant_member(tenant_id));

-- Cópia bruta: nenhuma política = ninguém além da chave de servidor
revoke all on table public.whatsapp_message_statuses from anon, authenticated;
revoke all on table public.whatsapp_webhook_events   from anon, authenticated;
grant select on table public.whatsapp_message_statuses to authenticated;
grant all on table public.whatsapp_message_statuses to service_role;
grant all on table public.whatsapp_webhook_events   to service_role;
grant usage, select on all sequences in schema public to service_role;


-- 5. FUNÇÃO AUXILIAR: acha ou cria o contato -----------------------------------------
create or replace function public.wa_upsert_contato(
  p_tenant_id  uuid,
  p_wa_id      text,      -- telefone, só dígitos (pode ser nulo)
  p_user_id    text,      -- BSUID (pode ser nulo)
  p_name       text,
  p_username   text,
  p_at         timestamptz
)
returns uuid
language plpgsql
set search_path = ''
as $$
declare
  v_id uuid;
begin
  if p_wa_id is null and p_user_id is null then
    raise exception 'Mensagem sem remetente (a Meta não mandou nem telefone nem BSUID).';
  end if;

  if p_user_id is not null then
    select c.id into v_id from public.contacts c
     where c.tenant_id = p_tenant_id and c.wa_user_id = p_user_id;
  end if;
  if v_id is null and p_wa_id is not null then
    select c.id into v_id from public.contacts c
     where c.tenant_id = p_tenant_id and c.wa_id = p_wa_id;
  end if;

  if v_id is null then
    insert into public.contacts (tenant_id, wa_id, wa_user_id, wa_username, name, last_interaction_at)
    values (p_tenant_id, p_wa_id, p_user_id, p_username, nullif(btrim(p_name), ''), p_at)
    on conflict do nothing
    returning id into v_id;

    if v_id is null then
      -- outro aviso criou o mesmo contato no mesmo instante: usa o que já existe
      select c.id into v_id from public.contacts c
       where c.tenant_id = p_tenant_id
         and ((p_user_id is not null and c.wa_user_id = p_user_id)
           or (p_wa_id   is not null and c.wa_id      = p_wa_id))
       limit 1;
    end if;
    if v_id is null then
      raise exception 'Não consegui criar nem encontrar o contato.';
    end if;
  else
    -- completa o que faltava, sem apagar o que a equipe já editou (ex.: nome)
    update public.contacts c set
      wa_id = case when c.wa_id is null and p_wa_id is not null
                    and not exists (select 1 from public.contacts x
                                     where x.tenant_id = p_tenant_id and x.wa_id = p_wa_id)
                   then p_wa_id else c.wa_id end,
      wa_user_id = case when c.wa_user_id is null and p_user_id is not null
                         and not exists (select 1 from public.contacts x
                                          where x.tenant_id = p_tenant_id and x.wa_user_id = p_user_id)
                        then p_user_id else c.wa_user_id end,
      wa_username = coalesce(p_username, c.wa_username),
      name = coalesce(c.name, nullif(btrim(p_name), '')),
      last_interaction_at = greatest(c.last_interaction_at, p_at)
    where c.id = v_id;
  end if;

  return v_id;
end;
$$;


-- 6. PROCESSAMENTO DE UM AVISO DA META -------------------------------------------------
--    Lê o JSON que a Meta manda e grava tudo. Dá erro (e desfaz) se algo falhar;
--    quem chama é a wa_receber_webhook, que guarda o erro sem perder o aviso.
create or replace function public.wa_processar_webhook(p_payload jsonb)
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  v_entry       jsonb;
  v_change      jsonb;
  v_value       jsonb;
  v_msg         jsonb;
  v_st          jsonb;
  v_ct          jsonb;
  v_phone_id    text;
  v_acc_id      uuid;
  v_tenant_id   uuid;
  v_wamid       text;
  v_from        text;
  v_from_user   text;
  v_type        text;
  v_body        text;
  v_at          timestamptz;
  v_contact_id  uuid;
  v_conv_id     uuid;
  v_status      text;
  v_ordem       text[] := array['received','queued','sent','delivered','read'];
  v_novas       int := 0;
  v_repetidas   int := 0;
  v_status_n    int := 0;
  v_status_rep  int := 0;
  v_ignorados   int := 0;
  v_desconhec   int := 0;
  v_inserido    uuid;
  v_falhas      int := 0;
  v_erro        text;
begin
  if coalesce(p_payload->>'object', '') <> 'whatsapp_business_account' then
    return jsonb_build_object('ignorado', true, 'motivo', 'não é aviso do WhatsApp');
  end if;

  for v_entry in select e from jsonb_array_elements(coalesce(p_payload->'entry', '[]'::jsonb)) e loop
    for v_change in select c from jsonb_array_elements(coalesce(v_entry->'changes', '[]'::jsonb)) c loop

      if coalesce(v_change->>'field', '') <> 'messages' then
        v_ignorados := v_ignorados + 1;
        continue;
      end if;

      v_value    := coalesce(v_change->'value', '{}'::jsonb);
      v_phone_id := v_value->'metadata'->>'phone_number_id';

      -- Identifica a EMPRESA pelo número que recebeu a mensagem
      select a.id, a.tenant_id into v_acc_id, v_tenant_id
        from public.whatsapp_accounts a
       where a.provider = 'meta_cloud' and a.phone_number_id = v_phone_id;

      if v_acc_id is null then
        v_desconhec := v_desconhec + 1;
        insert into public.event_logs (tenant_id, source, level, event_type, message, context)
        values (null, 'whatsapp_webhook', 'warning', 'whatsapp.unknown_number',
                'Aviso recebido para um número que não está cadastrado em whatsapp_accounts.',
                jsonb_build_object('phone_number_id', v_phone_id, 'waba_id', v_entry->>'id'));
        continue;
      end if;

      -- 6.1 MENSAGENS RECEBIDAS ------------------------------------------------------
      for v_msg in select m from jsonb_array_elements(coalesce(v_value->'messages', '[]'::jsonb)) m loop
        v_wamid := nullif(btrim(v_msg->>'id'), '');
        if v_wamid is null then
          v_ignorados := v_ignorados + 1;
          continue;
        end if;
        -- cada mensagem é gravada à parte: se UMA tiver defeito, as outras entram
        begin

        -- já gravada? (a Meta pode mandar o mesmo aviso mais de uma vez)
        if exists (select 1 from public.whatsapp_messages w
                    where w.tenant_id = v_tenant_id and w.wa_message_id = v_wamid) then
          v_repetidas := v_repetidas + 1;
          continue;
        end if;

        v_from      := nullif(regexp_replace(coalesce(v_msg->>'from', ''), '\D', '', 'g'), '');
        v_from_user := nullif(btrim(v_msg->>'from_user_id'), '');

        -- dados do perfil do cliente (nome, @usuario), do bloco "contacts"
        select c into v_ct
          from jsonb_array_elements(coalesce(v_value->'contacts', '[]'::jsonb)) c
         where (v_from is not null and regexp_replace(coalesce(c->>'wa_id', ''), '\D', '', 'g') = v_from)
            or (v_from_user is not null and c->>'user_id' = v_from_user)
         limit 1;
        if v_ct is null and jsonb_array_length(coalesce(v_value->'contacts', '[]'::jsonb)) = 1 then
          v_ct := v_value->'contacts'->0;
        end if;
        v_from      := coalesce(v_from, nullif(regexp_replace(coalesce(v_ct->>'wa_id', ''), '\D', '', 'g'), ''));
        v_from_user := coalesce(v_from_user, nullif(btrim(v_ct->>'user_id'), ''));

        -- hora da mensagem (a Meta manda em segundos); nunca no futuro
        v_at := case when (v_msg->>'timestamp') ~ '^\d{1,12}$'
                     then least(to_timestamp((v_msg->>'timestamp')::bigint), now())
                     else now() end;

        v_type := coalesce(v_msg->>'type', 'unsupported');
        v_body := case v_type
          when 'text'        then v_msg->'text'->>'body'
          when 'image'       then v_msg->'image'->>'caption'
          when 'video'       then v_msg->'video'->>'caption'
          when 'document'    then coalesce(v_msg->'document'->>'caption', v_msg->'document'->>'filename')
          when 'button'      then v_msg->'button'->>'text'
          when 'interactive' then coalesce(v_msg->'interactive'->'button_reply'->>'title',
                                           v_msg->'interactive'->'list_reply'->>'title',
                                           v_msg->'interactive'->'nfm_reply'->>'body')
          when 'reaction'    then v_msg->'reaction'->>'emoji'
          when 'location'    then concat_ws(' — ', v_msg->'location'->>'name', v_msg->'location'->>'address',
                                            (v_msg->'location'->>'latitude') || ',' || (v_msg->'location'->>'longitude'))
          else null end;
        if v_type not in ('text','image','audio','video','document','sticker','location','contacts',
                          'interactive','button','template','reaction') then
          v_type := 'unsupported';
        end if;

        v_contact_id := public.wa_upsert_contato(
          v_tenant_id, v_from, v_from_user,
          v_ct->'profile'->>'name', v_ct->'profile'->>'username', v_at);

        -- uma conversa aberta por contato em cada número (se a última foi encerrada, abre outra)
        insert into public.whatsapp_conversations (tenant_id, whatsapp_account_id, contact_id)
        values (v_tenant_id, v_acc_id, v_contact_id)
        on conflict (whatsapp_account_id, contact_id) where status <> 'closed' do nothing;

        select w.id into v_conv_id
          from public.whatsapp_conversations w
         where w.whatsapp_account_id = v_acc_id and w.contact_id = v_contact_id and w.status <> 'closed';

        v_inserido := null;
        insert into public.whatsapp_messages
          (tenant_id, conversation_id, direction, sender, wa_message_id, message_type,
           body, payload, delivery_status, error, created_at)
        values
          (v_tenant_id, v_conv_id, 'inbound', 'contact', v_wamid, v_type,
           left(v_body, 10000),
           v_msg || jsonb_build_object('_phone_number_id', v_phone_id),
           'received',
           left(coalesce(v_msg->'errors'->0->>'title', v_msg->'errors'->0->>'message'), 500),
           v_at)
        on conflict (tenant_id, wa_message_id) where wa_message_id is not null do nothing
        returning id into v_inserido;

        if v_inserido is null then
          v_repetidas := v_repetidas + 1;
        else
          v_novas := v_novas + 1;
          update public.whatsapp_conversations w
             set last_inbound_at = greatest(w.last_inbound_at, v_at)
           where w.id = v_conv_id;
        end if;
        exception when others then
          v_falhas := v_falhas + 1;
          v_erro := left(sqlerrm, 300);
        end;
      end loop;

      -- 6.2 AVISOS DE STATUS (das mensagens que a empresa enviou) -------------------
      for v_st in select s from jsonb_array_elements(coalesce(v_value->'statuses', '[]'::jsonb)) s loop
        v_wamid  := nullif(btrim(v_st->>'id'), '');
        v_status := nullif(btrim(lower(v_st->>'status')), '');
        if v_wamid is null or v_status is null then
          v_ignorados := v_ignorados + 1;
          continue;
        end if;
        begin
        v_at := case when (v_st->>'timestamp') ~ '^\d{1,12}$'
                     then least(to_timestamp((v_st->>'timestamp')::bigint), now())
                     else now() end;

        insert into public.whatsapp_message_statuses
          (tenant_id, whatsapp_account_id, wa_message_id, status, status_at,
           recipient_wa_id, recipient_user_id, errors, pricing, payload)
        values
          (v_tenant_id, v_acc_id, v_wamid, v_status, v_at,
           nullif(regexp_replace(coalesce(v_st->>'recipient_id', ''), '\D', '', 'g'), ''),
           nullif(btrim(v_st->>'recipient_user_id'), ''),
           v_st->'errors', v_st->'pricing', v_st)
        on conflict (tenant_id, wa_message_id, status) do nothing;

        if not found then
          v_status_rep := v_status_rep + 1;
          continue;
        end if;
        v_status_n := v_status_n + 1;

        -- atualiza a mensagem (se ela já estiver no banco), sem "voltar" de etapa:
        -- os avisos podem chegar fora de ordem (ex.: "lida" antes de "entregue")
        if v_status in ('sent','delivered','read','failed') then
          update public.whatsapp_messages w set
            delivery_status = v_status,
            error = case when v_status = 'failed'
                         then left(coalesce(v_st->'errors'->0->>'title', v_st->'errors'->0->>'message', 'falha'), 500)
                         else w.error end
          where w.tenant_id = v_tenant_id
            and w.wa_message_id = v_wamid
            and w.direction = 'outbound'
            and (
                  (v_status = 'failed' and w.delivery_status not in ('delivered','read'))
               or (v_status <> 'failed'
                   and coalesce(array_position(v_ordem, v_status), 0)
                       > coalesce(array_position(v_ordem, w.delivery_status), 0))
            );
        end if;
        exception when others then
          v_falhas := v_falhas + 1;
          v_erro := left(sqlerrm, 300);
        end;
      end loop;

      -- 6.3 ERROS que a Meta manda no próprio aviso ----------------------------------
      if jsonb_typeof(v_value->'errors') = 'array' then
        insert into public.event_logs (tenant_id, source, level, event_type, message, context)
        values (v_tenant_id, 'whatsapp_webhook', 'warning', 'whatsapp.meta_error',
                left(coalesce(v_value->'errors'->0->>'title', v_value->'errors'->0->>'message', 'Erro informado pela Meta'), 500),
                jsonb_build_object('errors', v_value->'errors', 'phone_number_id', v_phone_id));
      end if;

      v_acc_id := null;
      v_tenant_id := null;
    end loop;
  end loop;

  return jsonb_build_object(
    'mensagens_novas',     v_novas,
    'mensagens_repetidas', v_repetidas,
    'status_novos',        v_status_n,
    'status_repetidos',    v_status_rep,
    'ignorados',           v_ignorados,
    'numero_desconhecido', v_desconhec,
    'falhas',              v_falhas,
    'ultimo_erro',         v_erro);
end;
$$;


-- 7. PORTA DE ENTRADA (chamada pela função HTTPS do Supabase) -------------------------
--    1) guarda a cópia bruta; 2) processa; 3) se der erro, guarda o erro e
--    responde normalmente (assim a Meta não fica reenviando sem fim, e nada se perde).
create or replace function public.wa_receber_webhook(p_payload jsonb)
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  v_event_id  bigint;
  v_resumo    jsonb;
  v_erro      text;
begin
  if p_payload is null or jsonb_typeof(p_payload) <> 'object' then
    raise exception 'Aviso vazio ou em formato inválido.';
  end if;

  insert into public.whatsapp_webhook_events (payload) values (p_payload)
  returning id into v_event_id;

  begin
    v_resumo := public.wa_processar_webhook(p_payload);
    if coalesce((v_resumo->>'falhas')::int, 0) > 0 then
      -- parte do aviso não entrou: fica guardado para reprocessar
      -- (o que já entrou continua gravado e não duplica no reprocessamento)
      update public.whatsapp_webhook_events
         set status = 'error', error = left(v_resumo->>'ultimo_erro', 500),
             summary = v_resumo, attempts = attempts + 1
       where id = v_event_id;
      insert into public.event_logs (tenant_id, source, level, event_type, message, context)
      values (null, 'whatsapp_webhook', 'error', 'whatsapp.webhook_error',
              'Parte de um aviso do WhatsApp não foi gravada (guardado para reprocessar): '
                || coalesce(v_resumo->>'ultimo_erro', ''),
              jsonb_build_object('webhook_event_id', v_event_id, 'falhas', v_resumo->'falhas'));
    else
      update public.whatsapp_webhook_events
         set status = case when coalesce((v_resumo->>'ignorado')::boolean, false) then 'ignored' else 'processed' end,
             summary = v_resumo, attempts = attempts + 1, processed_at = now()
       where id = v_event_id;
    end if;
  exception when others then
    v_erro := left(sqlerrm, 500);
    update public.whatsapp_webhook_events
       set status = 'error', error = v_erro, attempts = attempts + 1
     where id = v_event_id;
    insert into public.event_logs (tenant_id, source, level, event_type, message, context)
    values (null, 'whatsapp_webhook', 'error', 'whatsapp.webhook_error',
            'Erro ao gravar um aviso do WhatsApp (guardado para reprocessar): ' || v_erro,
            jsonb_build_object('webhook_event_id', v_event_id));
    v_resumo := jsonb_build_object('erro', true);
  end;

  -- limpeza ocasional: cópias brutas com mais de 30 dias (LGPD: guardar só o necessário)
  if random() < 0.05 then
    delete from public.whatsapp_webhook_events
     where received_at < now() - interval '30 days'
       and status in ('processed', 'ignored');
  end if;

  return v_resumo || jsonb_build_object('evento', v_event_id);
end;
$$;


-- 8. REPROCESSAR avisos que deram erro (seguro: não duplica nada) ---------------------
create or replace function public.wa_reprocessar_eventos(p_limite int default 50)
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  r        record;
  v_ok     int := 0;
  v_falha  int := 0;
  v_resumo jsonb;
begin
  for r in
    select id, payload from public.whatsapp_webhook_events
     where status = 'error'
     order by received_at
     limit greatest(1, least(coalesce(p_limite, 50), 500))
     for update skip locked
  loop
    begin
      v_resumo := public.wa_processar_webhook(r.payload);
      if coalesce((v_resumo->>'falhas')::int, 0) > 0 then
        update public.whatsapp_webhook_events
           set error = left(v_resumo->>'ultimo_erro', 500), summary = v_resumo, attempts = attempts + 1
         where id = r.id;
        v_falha := v_falha + 1;
      else
        update public.whatsapp_webhook_events
           set status = 'processed', summary = v_resumo, error = null,
               attempts = attempts + 1, processed_at = now()
         where id = r.id;
        v_ok := v_ok + 1;
      end if;
    exception when others then
      update public.whatsapp_webhook_events
         set error = left(sqlerrm, 500), attempts = attempts + 1
       where id = r.id;
      v_falha := v_falha + 1;
    end;
  end loop;
  return jsonb_build_object('reprocessados', v_ok, 'ainda_com_erro', v_falha);
end;
$$;


-- 9. APAGAR SÓ OS DADOS DO TESTE AUTOMÁTICO ---------------------------------------
--    O teste usa telefones 5500000000xxx e mensagens "wamid.TESTE-...".
create or replace function public.wa_apagar_testes()
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  v_contatos int;
  v_status   int;
  v_eventos  int;
begin
  delete from public.whatsapp_message_statuses where wa_message_id like 'wamid.TESTE-%';
  get diagnostics v_status = row_count;

  delete from public.contacts where wa_id like '5500000000%';   -- leva junto conversas e mensagens
  get diagnostics v_contatos = row_count;

  delete from public.whatsapp_webhook_events where payload::text like '%wamid.TESTE-%';
  get diagnostics v_eventos = row_count;

  return jsonb_build_object('contatos', v_contatos, 'status', v_status, 'eventos', v_eventos);
end;
$$;


-- 10. PERMISSÕES: somente a chave de servidor (service_role) ----------------------
do $$
declare
  r record;
begin
  for r in
    select p.oid::regprocedure::text as sig
      from pg_proc p
      join pg_namespace n on n.oid = p.pronamespace
     where n.nspname = 'public'
       and p.proname in ('wa_upsert_contato', 'wa_processar_webhook', 'wa_receber_webhook',
                         'wa_reprocessar_eventos', 'wa_apagar_testes')
  loop
    execute format('revoke all on function %s from public, anon, authenticated', r.sig);
    execute format('grant execute on function %s to service_role', r.sig);
  end loop;
end
$$;

comment on function public.wa_receber_webhook(jsonb)
  is 'Recebe um aviso do webhook do WhatsApp e grava contato, conversa, mensagem e status sem duplicar. Somente service_role.';
comment on function public.wa_reprocessar_eventos(int)
  is 'Tenta de novo os avisos do WhatsApp que deram erro. Somente service_role.';
comment on function public.wa_apagar_testes()
  is 'Apaga apenas os dados criados pelo teste automático (TESTAR_WEBHOOK.bat). Somente service_role.';


-- 11. NÚMERO DE TESTE DA META NA CRED FINANCE ---------------------------------------
--     IDs não são segredo. O agente continua DESLIGADO (agent_enabled = false).
insert into public.whatsapp_accounts
  (tenant_id, provider, label, display_phone_number, phone_number_id, waba_id, status, agent_enabled)
select t.id, 'meta_cloud', 'Número de teste da Meta', '+1 555 162 8212',
       'SEU_PHONE_NUMBER_ID', 'SEU_WABA_ID', 'active', false
  from public.tenants t
 where t.slug = 'cred-finance'
on conflict (phone_number_id) where phone_number_id is not null
do update set waba_id = excluded.waba_id,
              display_phone_number = excluded.display_phone_number;


-- 12. TESTE INTERNO: grava uma mensagem simulada e DESFAZ tudo em seguida ------------
create temp table if not exists _teste_005 (resultado text);
truncate _teste_005;

do $$
declare
  v_payload jsonb;
  v_r1 jsonb;
  v_r2 jsonb;
  v_msgs int;
  v_status text;
begin
  if not exists (select 1 from public.whatsapp_accounts where phone_number_id = 'SEU_PHONE_NUMBER_ID') then
    insert into _teste_005 values ('FALHA — número de teste não cadastrado (falta a empresa cred-finance)');
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
             'profile', jsonb_build_object('name', 'Teste interno 005'),
             'wa_id', '5500000000999', 'user_id', 'BR.TESTE005')),
          'messages', jsonb_build_array(jsonb_build_object(
             'from', '5500000000999', 'from_user_id', 'BR.TESTE005',
             'id', 'wamid.TESTE-005-INTERNO', 'timestamp', extract(epoch from now())::bigint::text,
             'type', 'text', 'text', jsonb_build_object('body', 'Olá, teste interno'))),
          'statuses', jsonb_build_array(
             jsonb_build_object('id', 'wamid.TESTE-005-INTERNO', 'status', 'read',
                                'timestamp', extract(epoch from now())::bigint::text,
                                'recipient_id', '5500000000999'))
        )))))) ;

  begin
    v_r1 := public.wa_receber_webhook(v_payload);
    v_r2 := public.wa_receber_webhook(v_payload);   -- mesma mensagem de novo
    select count(*) into v_msgs from public.whatsapp_messages where wa_message_id = 'wamid.TESTE-005-INTERNO';
    select delivery_status into v_status from public.whatsapp_messages where wa_message_id = 'wamid.TESTE-005-INTERNO';
    if coalesce((v_r1->>'mensagens_novas')::int, 0) = 1
       and coalesce((v_r2->>'mensagens_repetidas')::int, 0) = 1
       and v_msgs = 1
       and v_status = 'received'      -- mensagem recebida não muda de status com aviso de "lida"
       and coalesce((v_r1->>'status_novos')::int, 0) = 1
       and coalesce((v_r2->>'status_repetidos')::int, 0) = 1 then
      raise exception using message = '__TESTE_OK__';
    else
      raise exception using message = '__TESTE_FALHOU__ ' || coalesce(v_r1::text, '') || ' / ' || coalesce(v_r2::text, '');
    end if;
  exception when others then
    if sqlerrm = '__TESTE_OK__' then
      insert into _teste_005 values ('OK — gravou 1 vez, recusou a repetida e desfez o teste');
    else
      insert into _teste_005 values ('FALHA — ' || left(sqlerrm, 300));
    end if;
  end;
end
$$;


-- 13. VERIFICAÇÃO (resultado esperado: 8 linhas, todas "OK") -----------------------
select ordem, verificacao, resultado from (
  select 1 as ordem, 'Colunas novas em contacts (wa_user_id, wa_username)' as verificacao,
         case when (select count(*) from information_schema.columns
                     where table_schema = 'public' and table_name = 'contacts'
                       and column_name in ('wa_user_id','wa_username')) = 2
              then 'OK' else 'FALHA' end as resultado
  union all
  select 2, 'Tabelas novas (status e cópia bruta)',
         case when to_regclass('public.whatsapp_message_statuses') is not null
               and to_regclass('public.whatsapp_webhook_events') is not null
              then 'OK' else 'FALHA' end
  union all
  select 3, 'RLS ligado nas tabelas novas',
         case when (select count(*) from pg_tables
                     where schemaname = 'public'
                       and tablename in ('whatsapp_message_statuses','whatsapp_webhook_events')
                       and rowsecurity) = 2
              then 'OK' else 'FALHA' end
  union all
  select 4, 'Visitantes (anon) sem acesso às tabelas novas',
         case when not has_table_privilege('anon', 'public.whatsapp_message_statuses', 'select')
               and not has_table_privilege('anon', 'public.whatsapp_webhook_events', 'select')
               and not has_table_privilege('authenticated', 'public.whatsapp_webhook_events', 'select')
              then 'OK' else 'FALHA' end
  union all
  select 5, 'Funções novas criadas (5)',
         case when (select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                     where n.nspname = 'public'
                       and p.proname in ('wa_upsert_contato','wa_processar_webhook','wa_receber_webhook',
                                         'wa_reprocessar_eventos','wa_apagar_testes')) = 5
              then 'OK' else 'FALHA' end
  union all
  select 6, 'Funções bloqueadas para visitantes e usuários logados',
         case when not exists (
                select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                 where n.nspname = 'public'
                   and p.proname in ('wa_upsert_contato','wa_processar_webhook','wa_receber_webhook',
                                     'wa_reprocessar_eventos','wa_apagar_testes')
                   and (has_function_privilege('anon', p.oid, 'execute')
                     or has_function_privilege('authenticated', p.oid, 'execute')))
              then 'OK' else 'FALHA' end
  union all
  select 7, 'Número de teste (+1 555 162 8212) ligado à Cred Finance',
         case when exists (select 1 from public.whatsapp_accounts a
                             join public.tenants t on t.id = a.tenant_id
                            where a.phone_number_id = 'SEU_PHONE_NUMBER_ID' and t.slug = 'cred-finance')
              then 'OK — agente desligado até a etapa (d)'
              else 'FALHA — empresa cred-finance não encontrada' end
  union all
  select 8, 'Teste interno (mensagem simulada, sem duplicar)',
         coalesce((select resultado from _teste_005 limit 1), 'FALHA — teste não rodou')
) v
order by ordem;
