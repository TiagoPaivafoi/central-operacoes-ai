"""Integração Meta Ads, restrita ao piloto Cred Finance. Não realiza pagamentos."""
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager

ACCOUNT='act_625009734906327'
PAGE='532973629903806'
PHONE='5521993545312'
LINK='https://lps.genialinvestimentos.com.br/genial-solar-gensol01-a/?gcode=GENIALENERGY-CREDFINANCE'

class Bloqueio(Exception):pass

@contextmanager
def trava(path):
 lock=Path(str(path)+'.lock')
 try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
 except FileExistsError:raise Bloqueio('Outra operação está em andamento. Não repita o envio.')
 try:yield
 finally:os.close(fd);lock.unlink(missing_ok=True)

def gravar(path,d):
 path=Path(path);temp=path.with_suffix('.tmp');temp.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temp,path)

def conferir_conta(meta):
 a=meta.get(ACCOUNT,{'fields':'id,name,account_status,currency,timezone_name,is_prepay_account,balance,amount_spent,spend_cap,disable_reason'})
 if a.get('id')!=ACCOUNT or a.get('currency')!='BRL':raise Bloqueio('Conta ou moeda diferente do piloto.')
 status=int(a.get('account_status',0));pre=a.get('is_prepay_account')
 return {'id':a['id'],'nome':a.get('name'),'ativa':status==1,'status':status,
  'regime':'Pré-pago' if pre is True else 'Cobrança automática/pós-paga' if pre is False else 'Não informado',
  'saldo_confirmado':False,
  'aviso':('A Meta informa pendência de cobrança. Confira saldo ou pagamento na Meta antes de ativar.' if status in (3,8,9) else 'Conta não liberada pela Meta. Confira as restrições.' if status!=1 else 'Conta ativa. A Meta não confirma saldo utilizável nesta consulta. Confira as condições de cobrança antes de ativar.'),
  'conferido_em':datetime.now(timezone.utc).isoformat()}

def pesquisar_locais(meta,termo):
 if not termo or len(termo)>100:raise Bloqueio('Informe cidade ou região.')
 r=meta.get('search',{'type':'adgeolocation','q':termo,'country_code':'BR','location_types':json.dumps(['city','region']),'limit':20})
 return [{k:v for k,v in x.items() if k in ('key','name','type','country_code','region','region_id')} for x in r.get('data',[]) if x.get('country_code')=='BR' and (x.get('type')=='region' and str(x.get('key'))=='454' or x.get('type')=='city' and str(x.get('region_id'))=='454')]

def payload_conjunto(plano,cid):
 from estrategia_trafego import montar_publico
 if plano.get('dias')!=5 or plano.get('diario')!=20:raise Bloqueio('Piloto limitado a cinco dias e R$100 no total.')
 inicio=datetime.fromisoformat(plano['inicio'])
 if inicio.tzinfo is None or inicio<datetime.now(timezone.utc)+timedelta(minutes=10):raise Bloqueio('Escolha início futuro com fuso horário.')
 return {'name':'Genial Solar RJ | WhatsApp | cinco dias','campaign_id':cid,'lifetime_budget':10000,'billing_event':'IMPRESSIONS','optimization_goal':'CONVERSATIONS','destination_type':'WHATSAPP','bid_strategy':'LOWEST_COST_WITHOUT_CAP','promoted_object':json.dumps({'page_id':PAGE,'whatsapp_phone_number':PHONE}),'targeting':json.dumps(montar_publico(plano)),'start_time':inicio.isoformat(),'end_time':(inicio+timedelta(days=5)).isoformat(),'status':'PAUSED'}

class Gestor:
 def __init__(self,meta,path):self.meta=meta;self.path=Path(path)
 def ler(self):return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'etapas':{}}
 def etapa(self,estado,chave,path,payload):
  etapas=estado.setdefault('etapas',{});ant=etapas.get(chave,{})
  if ant.get('id'):return ant['id']
  if ant and ant.get('estado')!='recusado':raise Bloqueio('O envio anterior ficou sem confirmação. Confira na Meta antes de repetir; criação automática bloqueada para evitar duplicação.')
  etapas[chave]={'estado':'enviando','nome':payload.get('name')};gravar(self.path,estado)
  try:r=self.meta.post(path,payload)
  except Exception as ex:
   etapas[chave]['estado']='recusado' if getattr(ex,'codigo',None) in (10,100,190,200) else 'sem_confirmacao';gravar(self.path,estado);raise
  if not str(r.get('id','')).isdigit():raise Bloqueio('A Meta não confirmou o código criado. Não repetir automaticamente.')
  etapas[chave]={'estado':'criado','id':r['id'],'nome':payload.get('name')};gravar(self.path,estado);return r['id']
 def campanha(self):
  if self.ler().get('migracao_conta',{}).get('bloquear_origem'):raise Bloqueio('Conta bloqueada para migração. Nenhuma campanha criada ou alterada.')
  with trava(self.path):
   conta=conferir_conta(self.meta)
   if not conta['ativa']:raise Bloqueio(conta['aviso'])
   e=self.ler();cid=self.etapa(e,'campanha',ACCOUNT+'/campaigns',{'name':'Cred Finance | Genial Solar RJ | WhatsApp | piloto 100','objective':'OUTCOME_ENGAGEMENT','status':'PAUSED','special_ad_categories':'[]','is_adset_budget_sharing_enabled':'false'})
   remoto=self.meta.get(cid,{'fields':'id,name,status,objective'})
   if remoto.get('status')!='PAUSED':raise Bloqueio('A campanha não está pausada. Conferência necessária.')
   e['campanha_verificada']=remoto;gravar(self.path,e);return remoto
 def validar_configuracao(self,plano):
  cid=self.ler().get('etapas',{}).get('campanha',{}).get('id')
  if not cid:raise Bloqueio('Prepare primeiro a campanha pausada.')
  r=self.meta.post(ACCOUNT+'/adsets',{**payload_conjunto(plano,cid),'execution_options':json.dumps(['validate_only'])})
  if r.get('success') is not True:raise Bloqueio('A Meta não confirmou a validação desta configuração.')
  return r
 def preparar_anuncio(self,plano):
  if self.ler().get('migracao_conta',{}).get('bloquear_origem'):raise Bloqueio('Conta bloqueada para migração. Nenhum anúncio criado.')
  # Preparar nunca significa ativar. Imagem deve ser um asset já aprovado da plataforma.
  if plano.get('dias')!=5 or plano.get('diario')!=20:raise Bloqueio('Piloto limitado a cinco dias, R$20/dia e teto de mídia R$100.')
  if LINK not in plano.get('legenda','') or 'wa.me' in plano['legenda']:raise Bloqueio('Use legenda paga com link Genial intacto e WhatsApp apenas no botão.')
  inicio=datetime.fromisoformat(plano['inicio']);fim=inicio+timedelta(days=5)
  if inicio.tzinfo is None or inicio<datetime.now(timezone.utc)+timedelta(minutes=10):raise Bloqueio('Escolha início futuro com fuso horário.')
  from estrategia_trafego import montar_publico
  targeting=montar_publico(plano)
  imagem=plano.get('imagem','')
  if not imagem.startswith('https://SEU-PROJETO.supabase.co/storage/v1/object/public/media/00000000-0000-0000-0000-000000000001/'):raise Bloqueio('Selecione uma imagem da Cred Finance armazenada na plataforma.')
  # Hash impede reaproveitar uma aprovação ou estrutura após alteração de conteúdo.
  h=hashlib.sha256(json.dumps(plano,sort_keys=True).encode()).hexdigest()
  with trava(self.path):
   e=self.ler();cid=e.get('etapas',{}).get('campanha',{}).get('id')
   if not cid:raise Bloqueio('Prepare primeiro a campanha pausada.')
   if not conferir_conta(self.meta)['ativa']:raise Bloqueio('A conta não está liberada pela Meta.')
   if self.meta.get(cid,{'fields':'status'}).get('status')!='PAUSED':raise Bloqueio('Pause e confira a campanha antes de alterar a estrutura.')
   if e.get('plano_hash') and e['plano_hash']!=h and any(v.get('id') or v.get('estado') in ('enviando','sem_confirmacao') for k,v in e.get('etapas',{}).items() if k!='campanha'):raise Bloqueio('Plano mudou. Revise a estrutura existente antes de criar outra versão.')
   if not e.get('etapas',{}).get('conjunto',{}).get('id'):self.validar_configuracao(plano)
   e.update(plano_hash=h,plano=plano);gravar(self.path,e)
   conjunto=self.etapa(e,'conjunto',ACCOUNT+'/adsets',payload_conjunto(plano,cid))
   creative=self.etapa(e,'criativo',ACCOUNT+'/adcreatives',{'name':'Genial Solar | '+h[:12],'object_story_spec':json.dumps({'page_id':PAGE,'instagram_user_id':'17841470457516155','link_data':{'message':plano['legenda'],'link':'https://wa.me/'+PHONE,'picture':imagem,'name':'Peça seu desconto agora','call_to_action':{'type':'WHATSAPP_MESSAGE','value':{'app_destination':'WHATSAPP','link':'https://wa.me/'+PHONE}}}})})
   ad=self.etapa(e,'anuncio',ACCOUNT+'/ads',{'name':'Genial Solar | '+h[:12],'adset_id':conjunto,'creative':json.dumps({'creative_id':creative}),'status':'PAUSED'})
   r=self.meta.get(ad,{'fields':'id,status,effective_status,issues_info'});e['anuncio_verificado']=r;gravar(self.path,e)
   if r.get('status')!='PAUSED':raise Bloqueio('Estado do anúncio não confirmado como pausado.')
   return r
 def desempenho(self):
  e=self.ler();cid=e.get('etapas',{}).get('campanha',{}).get('id')
  if not cid:raise Bloqueio('Nenhuma campanha criada por esta integração.')
  return self.meta.get(cid+'/insights',{'fields':'spend,impressions,reach,clicks,actions','date_preset':'maximum'})

def painel(st,empresa,dados,pasta,db):
 if empresa['id']!='00000000-0000-0000-0000-000000000001':return
 from meta import Meta
 token=str(st.secrets.get('META_SYSTEM_TOKEN','')).strip()
 if not token:st.error('Conexão Meta não configurada.');return
 meta=Meta(token,versao='v26.0');gestor=Gestor(meta,pasta/'meta_genial_piloto_625009734906327.json')
 st.subheader('Operar na Meta pela Central')
 estado=gestor.ler()
 st.info('Conta conectada: Tiago Paiva — 625009734906327 — pré-paga. A recarga é feita pelo titular na Meta. A campanha anterior permanece pausada.')
 if estado.get('migracao_conta',{}).get('bloquear_origem'):
  st.warning('Campanha pausada a pedido do usuário. A conta 949839644489025 não será usada para novas campanhas pela Central.')
  st.info('Conta preferida: 625009734906327. Integração pendente de acesso na Meta. Nenhuma nova campanha será criada enquanto a conexão não for confirmada.')
  return
 if estado.get('ativacao_verificada'):
  inicio_piloto=datetime.fromisoformat(estado['plano']['inicio'])
  fim_piloto=inicio_piloto+timedelta(days=int(estado['plano'].get('dias',5)))
  st.success('Campanha ativada: '+inicio_piloto.strftime('%d/%m/%Y às %Hh%M')+' até '+fim_piloto.strftime('%d/%m/%Y às %Hh%M')+', horário de Brasília. Mídia: R$100 no total.')
  st.info('Anúncio enviado à Meta. A entrega depende da análise e liberação da Meta; ativado não comprova impressões ou gasto.')
  st.write('WhatsApp do piloto habilitado: texto, áudio até 30 segundos, imagens até 2 MB e PDF sem senha até 10 páginas / 2 MB. Reserva de IA: até R$5, com bloqueio conservador de consumo.')
  st.caption('Horários anteriores permanecem pausados. Alterações desta campanha exigem nova revisão. Otimização automática não liberada.')
  if estado.get('plano',{}).get('imagem'):st.image(estado['plano']['imagem'],width=300)
  if st.button('Consultar desempenho da campanha'):
   try:st.json(gestor.desempenho())
   except Exception:st.error('Não foi possível consultar o desempenho agora. Confira na Meta.')
  return
 if estado.get('campanha_verificada'):
  st.success('Última confirmação na Meta — campanha pausada: '+estado['campanha_verificada']['id'])
 st.caption('As criações abaixo ficam pausadas. Esta integração não adiciona saldo nem paga cobranças.')
 st.info('Piloto: orçamento total de 100 reais por cinco dias, média de 20 reais por dia. O gasto diário pode variar.')
 def erro(ex):
  mensagem=str(ex).replace(token,'***');st.error(mensagem[:450])
 if st.button('1. Conferir conta e cobrança na Meta'):
  try:
   r=conferir_conta(meta);st.session_state['meta_conta_piloto']=r
  except Exception as ex:erro(ex)
 if st.session_state.get('meta_conta_piloto'):
  r=st.session_state['meta_conta_piloto'];st.write(r['nome']+' • '+r['regime']);st.info(r['aviso'])
 if st.button('2. Criar ou conferir campanha pausada'):
  try:st.success('Campanha pausada confirmada: '+gestor.campanha()['id'])
  except Exception as ex:erro(ex)
 termo=st.text_input('Cidade ou região para pesquisar na Meta',value='Rio de Janeiro')
 if st.button('Pesquisar localização'):
  try:st.session_state['meta_locais_piloto']=pesquisar_locais(meta,termo)
  except Exception as ex:erro(ex)
 locais=st.session_state.get('meta_locais_piloto',[])
 selecionados=st.multiselect('Cidades ou região do piloto',locais,format_func=lambda x:f"{x['name']} — {x.get('region','')} ({x['type']})",max_selections=10) if locais else []
 import estrategia_trafego,importlib
 importlib.reload(estrategia_trafego)
 from estrategia_trafego import controles,recomendar,montar_publico
 estrategia=controles(st,meta)
 recomendacao=recomendar({**estrategia,'locais':selecionados,'diario':20,'dias':5})
 st.write(recomendacao['estrutura'])
 for aviso in recomendacao['avisos']:st.info(aviso)
 assets=db.c.table('media_assets').select('id,public_url,alt_text').eq('tenant_id',empresa['id']).eq('kind','image').limit(100).execute().data or []
 asset=st.selectbox('Imagem final da plataforma',assets,index=None,format_func=lambda x:(x.get('alt_text') or x['id'])[:100]) if assets else None
 from zoneinfo import ZoneInfo
 tz=ZoneInfo('America/Sao_Paulo');amanha=datetime.now(tz)+timedelta(days=1)
 data=st.date_input('Início pretendido',amanha.date());hora=st.time_input('Hora de início',amanha.time().replace(second=0,microsecond=0))
 if st.button('Validar público e datas na Meta sem criar anúncio',disabled=not selecionados):
  try:
   gestor.validar_configuracao({**estrategia,'locais':selecionados,'dias':5,'diario':20,'inicio':datetime.combine(data,hora,tzinfo=tz).isoformat()});st.success('Configuração aceita na validação da Meta. Nada foi criado ou ativado por esta consulta.')
  except Exception as ex:erro(ex)
 if asset:st.image(asset['public_url'],width=300)
 revisado=st.checkbox('Revisei esta imagem, legenda paga, localização e orçamento de cinco dias / R$100. Preparar pausado.')
 if st.button('3. Preparar conjunto e anúncio pausados',disabled=not(selecionados and asset and revisado)):
  try:
   plano={'dias':5,'diario':20,'legenda':dados.get('legenda_paga',''),**estrategia,'locais':selecionados,'imagem':asset['public_url'],'inicio':datetime.combine(data,hora,tzinfo=tz).isoformat()}
   st.success('Anúncio pausado confirmado: '+gestor.preparar_anuncio(plano)['id'])
  except Exception as ex:erro(ex)
 if st.button('Consultar desempenho da campanha'):
  try:st.json(gestor.desempenho())
  except Exception as ex:erro(ex)
 if estado.get('ativacao_verificada'):
  st.success('Campanha ativada para 07/11 às 22h00, até 12/11 às 22h00. Orçamento total de mídia: R$100. A entrega depende da análise e liberação da Meta.')
  st.info('Atendimento do piloto: texto, áudio de até 30 segundos, imagens até 2 MB e PDF sem senha de até 10 páginas / 2 MB. Reserva conservadora de IA: até R$5 no piloto, com bloqueio por orçamento e expiração.')
  st.caption('Conjuntos anteriores continuam pausados. Não há otimização automática liberada. Consulte o desempenho para conferir gasto e entrega reais.')
 else:
  st.button('4. Ativar anúncio — atendimento ainda em validação',disabled=True)
  st.caption('Ativação bloqueada enquanto o atendimento comercial e as condições de cobrança não forem validados. Não há rotina de otimização automática liberada.')
