-- Fila restrita ao servidor. Não habilita áudio ou altera agent_enabled.
begin;
create table if not exists public.whatsapp_audio_jobs (
 message_id uuid primary key references public.whatsapp_messages(id),
 tenant_id uuid not null references public.tenants(id),
 conversation_id uuid not null references public.whatsapp_conversations(id),
 worker text not null,
 status text not null check(status in ('reserved','ready','failed')),
 duration_seconds double precision not null check(duration_seconds>0 and duration_seconds<=30),
 transcript text,
 usage jsonb not null default '{}'::jsonb,
 created_at timestamptz not null default now(),
 updated_at timestamptz not null default now()
);
alter table public.whatsapp_audio_jobs enable row level security;
revoke all on public.whatsapp_audio_jobs from public,anon,authenticated;
grant select,insert,update on public.whatsapp_audio_jobs to service_role;
create index if not exists whatsapp_audio_jobs_tenant_created on public.whatsapp_audio_jobs(tenant_id,created_at);

create or replace function public.wa_audio_reservar(p_message_id uuid,p_worker text,p_duration double precision)
returns jsonb language plpgsql security definer set search_path='' as $f$
declare c public.whatsapp_conversations%rowtype; m public.whatsapp_messages%rowtype;
 a public.whatsapp_accounts%rowtype; j public.whatsapp_audio_jobs%rowtype;
begin
 select * into m from public.whatsapp_messages where id=p_message_id;
 if not found or m.message_type<>'audio' or m.direction<>'inbound' then raise exception 'Mensagem inválida';end if;
 select * into c from public.whatsapp_conversations where id=m.conversation_id and tenant_id=m.tenant_id for update;
 if not found or c.agent_locked_by is distinct from p_worker or c.agent_locked_until<=now() or c.agent_locked_until is null or c.status<>'bot' then raise exception 'Conversa não reservada para este worker';end if;
 select * into a from public.whatsapp_accounts where id=c.whatsapp_account_id and tenant_id=c.tenant_id;
 if not found or not coalesce(a.agent_enabled,false) or a.status<>'active' or
 coalesce((public.wa_agente_config(c.tenant_id,a.id)->>'audio_enabled')::boolean,false) is not true then
  raise exception 'Áudio desabilitado';
 end if;
 if exists(select 1 from public.contacts where id=c.contact_id and opt_out_at is not null) then raise exception 'Contato sem automação';end if;
 if p_duration is null or p_duration<=0 or p_duration>30 or p_duration='NaN'::float8 then raise exception 'Duração não habilitada';end if;
 perform pg_advisory_xact_lock(hashtextextended(c.tenant_id::text,17));
 select * into j from public.whatsapp_audio_jobs where message_id=m.id;
 if found then
  if j.status='ready' then return jsonb_build_object('status','ready','texto',j.transcript);end if;
  -- Reserva incerta não é refeita: exige conferência, evitando segunda cobrança.
  return jsonb_build_object('status','review_required');
 end if;
 if (select count(*) from public.whatsapp_audio_jobs where tenant_id=c.tenant_id and created_at>=date_trunc('day',now()))>=10 or
 (select count(*) from public.whatsapp_audio_jobs where conversation_id=c.id and created_at>now()-interval '24 hours')>=3 then
  return jsonb_build_object('status','limit');
 end if;
 insert into public.whatsapp_audio_jobs(message_id,tenant_id,conversation_id,worker,status,duration_seconds)
 values(m.id,m.tenant_id,c.id,p_worker,'reserved',p_duration);
 return jsonb_build_object('status','reserved');
end $f$;

create or replace function public.wa_audio_concluir(p_message_id uuid,p_worker text,p_text text,p_usage jsonb default '{}'::jsonb)
returns jsonb language plpgsql security definer set search_path='' as $f$
declare j public.whatsapp_audio_jobs%rowtype; t text;
begin
 select * into j from public.whatsapp_audio_jobs where message_id=p_message_id for update;
 if not found or j.worker is distinct from p_worker or j.status<>'reserved' then raise exception 'Reserva inválida';end if;
 if p_text is null or length(btrim(p_text))=0 or length(p_text)>12000 then raise exception 'Transcrição inválida';end if;
 t:=regexp_replace(p_text,'[0-9]{3}[.[:space:]]?[0-9]{3}[.[:space:]]?[0-9]{3}[-[:space:]]?[0-9]{2}','[CPF omitido]','g');
 t:=regexp_replace(t,'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}','[e-mail omitido]','g');
 update public.whatsapp_audio_jobs set status='ready',transcript=t,usage=jsonb_build_object(
 'promptTokenCount',p_usage->'promptTokenCount','candidatesTokenCount',p_usage->'candidatesTokenCount'),updated_at=now() where message_id=j.message_id;
 update public.whatsapp_messages set body='[Áudio transcrito] '||t,updated_at=now() where id=j.message_id and tenant_id=j.tenant_id and conversation_id=j.conversation_id;
 return jsonb_build_object('status','ready','texto',t);
end $f$;
revoke all on function public.wa_audio_reservar(uuid,text,double precision) from public,anon,authenticated;
revoke all on function public.wa_audio_concluir(uuid,text,text,jsonb) from public,anon,authenticated;
grant execute on function public.wa_audio_reservar(uuid,text,double precision) to service_role;
grant execute on function public.wa_audio_concluir(uuid,text,text,jsonb) to service_role;
commit;
