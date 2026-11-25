-- =====================================================================
--  ARQUIVO 3 DE 3 — VERIFICAÇÃO DA INSTALAÇÃO (só leitura, não altera nada)
--  Rode DEPOIS dos arquivos 001 e 002.
--  Supabase → SQL Editor → New query → cole tudo → Run
--  Resultado esperado: uma tabela com 9 linhas, TODAS começando com "OK".
--  Se alguma linha disser FALHA, copie a tabela inteira e me envie.
-- =====================================================================

with
tabelas(nome) as (
  values ('tenants'),('tenant_members'),('social_credentials'),('media_assets'),
         ('posts_schedule'),('post_media'),('post_targets'),('whatsapp_accounts'),
         ('contacts'),('whatsapp_conversations'),('whatsapp_messages'),
         ('knowledge_sources'),('knowledge_base'),('event_logs')
),
funcoes_secretas(nome) as (
  values ('set_social_credential_tokens'),('get_social_credential'),('refresh_post_status'),
         ('claim_due_post_targets'),('complete_post_target'),('fail_post_target'),
         ('sweep_stuck_post_targets'),('match_knowledge')
),
checks as (
  select 1 as ordem,
         'pgvector instalado (busca por similaridade)' as verificacao,
         coalesce((select 'OK — versão ' || extversion from pg_extension where extname = 'vector'),
                  'FALHA — extensão ausente') as resultado
  union all
  select 2, 'Supabase Vault instalado (cofre de segredos)',
         case when exists (select 1 from pg_extension where extname = 'supabase_vault')
              then 'OK' else 'FALHA — extensão ausente' end
  union all
  select 3, 'Tabelas criadas (esperado: 14)',
         (select case when count(*) = 14 then 'OK — 14 de 14'
                      else 'FALHA — apenas ' || count(*) || ' de 14' end
            from pg_tables t
            join tabelas x on x.nome = t.tablename
           where t.schemaname = 'public')
  union all
  select 4, 'RLS ligado em todas as tabelas',
         (select case when count(*) = 0 then 'OK'
                      else 'FALHA — sem RLS: ' || string_agg(t.tablename, ', ') end
            from pg_tables t
            join tabelas x on x.nome = t.tablename
           where t.schemaname = 'public' and not t.rowsecurity)
  union all
  select 5, 'Visitantes anônimos (anon) sem acesso às tabelas',
         (select case when count(*) = 0 then 'OK'
                      else 'FALHA — acessíveis: ' || string_agg(x.nome, ', ') end
            from tabelas x
           where has_table_privilege('anon', 'public.' || x.nome, 'select'))
  union all
  select 6, 'Funções secretas criadas (esperado: 8)',
         (select case when count(*) = 8 then 'OK — 8 de 8'
                      else 'FALHA — apenas ' || count(*) || ' de 8 (rode o arquivo 2)' end
            from pg_proc p
            join pg_namespace n on n.oid = p.pronamespace
            join funcoes_secretas f on f.nome = p.proname
           where n.nspname = 'public')
  union all
  select 7, 'Funções secretas bloqueadas para anon e authenticated',
         (select case when count(*) = 0 then 'OK'
                      else 'FALHA — expostas: ' || string_agg(p.proname, ', ') end
            from pg_proc p
            join pg_namespace n on n.oid = p.pronamespace
            join funcoes_secretas f on f.nome = p.proname
           where n.nspname = 'public'
             and (has_function_privilege('anon', p.oid, 'execute')
               or has_function_privilege('authenticated', p.oid, 'execute')))
  union all
  select 8, 'Bucket de mídia "media" criado',
         case when exists (select 1 from storage.buckets where id = 'media')
              then 'OK' else 'FALHA — bucket ausente' end
  union all
  select 9, 'Empresas cadastradas',
         (select 'OK — ' || count(*) || ' empresa(s)' from public.tenants)
)
select verificacao, resultado
  from checks
 order by ordem;
