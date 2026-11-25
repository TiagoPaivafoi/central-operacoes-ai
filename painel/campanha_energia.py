"""Campanha piloto Genial Solar: planejamento e revisão sem gasto automático."""
import hashlib
import json
from pathlib import Path
import streamlit as st

TENANT='00000000-0000-0000-0000-000000000001'
WHATSAPP='https://wa.me/5521993545312?text=Ol%C3%A1%21%20Quero%20aderir%20ao%20desconto%20da%20Genial%20Solar%20na%20minha%20conta%20de%20luz.'
LINK='https://lps.genialinvestimentos.com.br/genial-solar-gensol01-a/?gcode=GENIALENERGY-CREDFINANCE'
LEGENDA=('Sua residência ou seu comércio tem conta da Light a partir de R$ 400? ☀️\n\n'
 'Peça seu desconto com a Genial Solar, sem instalar placas no seu imóvel.\n'
 'Cadastro e adesão sem custo.\n\n'
 'Peça seu desconto agora! Fale com a Cred Finance no WhatsApp:\n'+WHATSAPP+
 '\n\nPrefere se cadastrar? Use nosso link da Genial Solar:\n'+LINK+
 '\n\n#CredFinance #GenialSolar #EnergiaSolar #RioDeJaneiro')

LEGENDA_PAGA=LEGENDA.replace('Peça seu desconto agora! Fale com a Cred Finance no WhatsApp:\n'+WHATSAPP,'Peça seu desconto agora! Toque no botão Enviar mensagem.').replace('Prefere se cadastrar? Use nosso link da Genial Solar:','Prefere se cadastrar direto? Use nosso link da Genial Solar:')

def assinatura(dados):
 return hashlib.sha256(json.dumps(dados,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def verificar(d):
 texto=d['legenda']; erros=[]
 for termo in ['Light','400']:
  if termo.casefold() not in texto.casefold(): erros.append('Falta condição essencial: '+termo)
 if LINK not in texto: erros.append('O link de indicação completo deve ser preservado.')
 for termo in ['garantido','100% de desconto','aprovação garantida','menor preço do mercado']:
  if termo.casefold() in texto.casefold(): erros.append('Revisar promessa: '+termo)
 if d['dias']!=5 or d['diario']!=20: erros.append('O piloto autorizado para preparação é de 5 dias a R$20/dia.')
 if len(texto)>2200: erros.append('Legenda excede limite de 2200 caracteres.')
 return erros

def pagina(empresa,db):
 st.title('☀️ Campanha de energia')
 if empresa['id']!=TENANT:
  st.info('Este piloto pertence à Cred Finance. Selecione essa empresa.');return
 pasta=Path(__file__).resolve().parent/'campanhas';pasta.mkdir(exist_ok=True)
 arquivo=pasta/'genial_solar_rj_piloto.json'
 inicial={'marca':'Cred Finance','produto':'Genial Solar — geração distribuída','dias':5,'diario':20,
  'legenda':LEGENDA,'legenda_paga':LEGENDA_PAGA,'fonte':LINK,'fonte_conferida_em':'2026-11-06','estado':'rascunho',
  'destino':'WhatsApp — mais conversas','objetivo':'Conversas no WhatsApp','whatsapp_duvidas':WHATSAPP,'meta_campaign_id':None}
 dados=json.loads(arquivo.read_text(encoding='utf-8')) if arquivo.exists() else inicial
 operacao=pasta/'meta_genial_piloto_625009734906327.json'
 ativo=bool(json.loads(operacao.read_text(encoding='utf-8')).get('ativacao_verificada')) if operacao.exists() else False
 st.caption('Piloto de cinco dias • Light e Enel RJ • campanha ativada com autorização' if ativo else 'Piloto de cinco dias • Light e Enel RJ • preparação')
 a,b,c=st.columns(3);a.metric('Por dia','R$ 20');b.metric('Duração','5 dias');c.metric('Mídia prevista','R$ 100')
 st.info('A distribuição orgânica depende do alcance das redes. Nos anúncios, RJ é um filtro geográfico; cidade, distribuidora e disponibilidade precisam de confirmação no cadastro.')
 abas=st.tabs(['Briefing e consultoria','Texto e edição','Revisão e liberação','Orgânico e anúncios'])
 with abas[0]:
  st.subheader('Briefing do piloto')
  st.write('Objetivo: iniciar conversas qualificadas no WhatsApp para orientar e conduzir a adesão à Genial Solar, preservando o link de indicação da Cred Finance.')
  st.write('Público: residências e negócios no RJ, baixa tensão Light ou Enel RJ, conta a partir de R$ 400. Não afirmar cobertura para todo município nem usar lista de clientes.')
  st.write('Oferta: geração distribuída, sem instalação de placas no imóvel. A fonte anuncia até 15%; este primeiro texto evita percentual fixo e promessa de economia sobre toda a conta.')
  st.link_button('Conferir oferta e cadastro',LINK)
  st.link_button('Peça seu desconto agora',WHATSAPP)
  st.caption('Fonte consultada em 06/11/2026. Revalidar antes de futuras campanhas.')
  st.write('Roteiro de qualificação: cidade/UF → Light ou Enel RJ → valor aproximado da conta → disponibilidade da unidade → proposta do parceiro.')
  st.info('Atendimento do piloto habilitado para texto, áudio, imagem e PDF, com orçamento de IA limitado. Cadastro da Genial pelo link de indicação; contratação depende do parceiro.' if ativo else 'Campanha destinada ao WhatsApp. Conferir a liberação do atendimento antes da ativação.')
 with abas[1]:
  st.subheader('Texto proposto')
  legenda_paga=st.text_area('Legenda do anúncio pago — WhatsApp pelo botão',dados.get('legenda_paga',LEGENDA_PAGA),height=260)
  legenda=st.text_area('Legenda orgânica — inclui link do WhatsApp',dados['legenda'],height=260)
  st.write('Direção de arte: formato vertical 1080 × 1350, azul institucional com detalhe solar; título “Energia solar sem instalar placas”; chamada “Peça seu desconto agora • Cadastro e adesão sem custo”; selo “Light e Enel RJ”; condição “Contas a partir de R$ 400”. Não usar logotipo do parceiro sem material autorizado.')
  st.caption('Arte final ainda pendente. Este texto foi preparado sem chamadas pagas de IA.')
  if st.button('Salvar edição do rascunho'):
   dados.update(legenda=legenda,legenda_paga=legenda_paga,estado='rascunho');dados.pop('revisao_hash',None)
   arquivo.write_text(json.dumps(dados,ensure_ascii=False,indent=2),encoding='utf-8');st.success('Rascunho salvo. Mudanças exigem nova revisão.')
 with abas[2]:
  st.subheader('Fiscal da edição')
  st.write('A conferência abaixo verifica condições e link. Ela não substitui a revisão editorial e visual completa.')
  if st.button('Conferir condições e link'):
   erros=verificar({**dados,'legenda':legenda})
   if erros:
    for erro in erros:st.error(erro)
   else:st.success('Critérios básicos e link presentes. Ainda falta revisar a arte e aprovar a versão final.')
  st.write('Etapas pendentes: arte final → revisão editorial e visual → aprovação da primeira peça → publicação orgânica → anúncio pausado → revisão final de público e orçamento → liberação.')
  st.caption('A criação na Meta fica pausada. A conferência textual não substitui a revisão completa da peça.')
 with abas[3]:
  st.subheader('Distribuição orgânica')
  st.write('Canais conectados: Facebook Cred Finance Soluções Financeiras e Instagram @cred_finance_. Piloto proposto: um post de feed nos dois canais; link clicável no Facebook e perfil/Story para Instagram, conforme suporte da conta. Link na legenda do Instagram não é clicável.')
  st.write('Botão principal: Enviar mensagem pelo WhatsApp. Link Genial completo nas legendas e enviado pelo agente para cadastro. No Instagram, o link da legenda aparece como texto; o agente o envia clicável na conversa. Não acrescentar parâmetros extras ao link de indicação sem validar com o parceiro; não usar link genérico da Genial.')
  st.subheader('Medição dos caminhos')
  st.write('Separar conversas no WhatsApp de acessos ao cadastro. Contagem exclusiva do link Genial ainda não configurada. Opções: relatório do parceiro por indicação ou link de medição que redirecione ao endereço original, com gcode intacto. Contagem de acessos pode incluir robôs e repetições; não prova cadastro concluído nem ausência de conversa no WhatsApp.')
  st.caption('No Instagram, o link da legenda não é clicável. Para medição, usar um ponto clicável como bio ou Story. UTMs sozinhas não geram relatório para a Cred Finance sem acesso aos dados de destino.')
  st.subheader('Tráfego pago')
  st.text('Tiago Paiva — conta 625009734906327 • pré-paga • BRL • cinco dias • orçamento total R$100 (média R$20/dia).')
  st.caption('Consulte o período vigente na seção Operar na Meta pela Central.' if ativo else 'Defina as datas antes de ativar.')
  st.write('Público proposto: RJ, adultos, sem lista de clientes ou segmentação por situação financeira. Qualificação pelo texto e conversa no WhatsApp. Localização da Meta não comprova a distribuidora da unidade.')
  st.write('Orçamento total do conjunto: R$100 em cinco dias. A média de R$20 por dia não é um teto diário rígido. A veiculação depende da aprovação da Meta.')
  st.link_button('Abrir Gerenciador de Anúncios','https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=625009734906327')
  import trafego_meta
  import importlib
  importlib.reload(trafego_meta)
  trafego_meta.painel(st,empresa,{**dados,'legenda_paga':legenda_paga},pasta,db)
 st.download_button('Baixar briefing e rascunho',json.dumps({**dados,'legenda':legenda,'legenda_paga':legenda_paga},ensure_ascii=False,indent=2),file_name='campanha_genial_solar_rj.json',mime='application/json')
 if not arquivo.exists():arquivo.write_text(json.dumps(inicial,ensure_ascii=False,indent=2),encoding='utf-8')
