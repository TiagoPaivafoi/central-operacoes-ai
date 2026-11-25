"""Regras explícitas de estratégia e público; não simulam aprovação da Meta."""
import json

class PublicoInvalido(ValueError):pass

def montar_publico(plano):
 locais=plano.get('locais') or [plano['local']]
 if not 1<=len(locais)<=10:raise PublicoInvalido('Selecione entre uma e dez localidades.')
 geo={}
 for l in locais:
  tipo=l.get('type');key=str(l.get('key',''))
  if l.get('country_code')!='BR' or not key.isdigit() or not(tipo=='region' and key=='454' or tipo=='city' and str(l.get('region_id'))=='454'):raise PublicoInvalido('Localidade fora do piloto RJ.')
  item={'key':key};raio=plano.get('raio_km')
  if raio:
   if tipo!='city' or not isinstance(raio,int) or not 25<=raio<=80:raise PublicoInvalido('Raio deve ser de 25 a 80 km em torno de cidade.')
   item.update(radius=raio,distance_unit='kilometer')
  geo.setdefault('cities' if tipo=='city' else 'regions',[]).append(item)
 if plano.get('modo_geo')=='ponto':
  lat=plano.get('latitude');lon=plano.get('longitude');r=plano.get('raio_ponto_km')
  if not plano.get('ponto_confirmado') or not isinstance(lat,(int,float)) or not isinstance(lon,(int,float)) or not(-23.2<=lat<=-22.3 and -43.9<=lon<=-42.6) or not isinstance(r,int) or not 2<=r<=25:raise PublicoInvalido('Confira o ponto público do recorte metropolitano e raio entre 2 e 25km.')
  geo={'custom_locations':[{'latitude':lat,'longitude':lon,'radius':r,'distance_unit':'kilometer'}]}
 minimo=plano.get('idade_min',18);maximo=plano.get('idade_max',65)
 if not all(isinstance(x,int) for x in [minimo,maximo]) or not 18<=minimo<=maximo<=65:raise PublicoInvalido('Faixa de idade inválida.')
 p={'geo_locations':geo,'age_min':minimo,'age_max':maximo}
 redes=plano.get('redes',['facebook','instagram'])
 if not redes or any(x not in ('facebook','instagram') for x in redes):raise PublicoInvalido('Escolha Facebook e/ou Instagram.')
 p['publisher_platforms']=redes
 interesses=plano.get('interesses',[])
 if len(interesses)>5 or any(not str(x.get('id','')).isdigit() or not x.get('name') for x in interesses):raise PublicoInvalido('Escolha até cinco interesses retornados pela Meta.')
 if interesses:p['flexible_spec']=[{'interests':[{'id':str(x['id']),'name':x['name']} for x in interesses]}]
 return p

def recomendar(plano):
 total=int(plano.get('diario',20))*int(plano.get('dias',5));locais=plano.get('locais') or ([plano['local']] if plano.get('local') else [])
 avisos=[]
 if len(locais)>3:avisos.append('Muitas localidades para este piloto; prefira um recorte inicial com objetivo de aprender.')
 if plano.get('interesses'):avisos.append('Interesses podem reduzir o alcance e não comprovam propriedade, conta ou distribuidora. Compare qualidade antes de estreitar mais.')
 if plano.get('raio_km'):avisos.append('O raio parte do centro reconhecido pela Meta para a cidade; não delimita ruas, edifícios, distribuidoras ou apenas moradores.')
 return {'objetivo':'Conversas no WhatsApp','publico':plano.get('perfil','Residências e comércios'),'verba_total_reais':total,'estrutura':'Um conjunto e uma peça inicial; mudanças posteriores exigem nova revisão.',
 'avaliacao':['Conversas iniciadas','Conversas qualificadas: cidade, distribuidora e conta >= R$400','Custo por conversa qualificada','Encaminhamentos ao cadastro e retornos confirmados pelo parceiro'],
 'orientacao':'Qualificar residência e comércio no criativo e no atendimento. Não confundir clique ou mensagem com contratação. Não aumentar verba automaticamente.',
 'avisos':avisos,'libera_veiculacao':False}

def pesquisar_interesses(meta,termo):
 if not termo.strip() or len(termo)>100:raise PublicoInvalido('Informe um termo de interesse.')
 r=meta.get('search',{'type':'adinterest','q':termo,'limit':20})
 return [{'id':str(x['id']),'name':x['name']} for x in r.get('data',[]) if str(x.get('id','')).isdigit() and x.get('name')]

def controles(st,meta):
 st.subheader('Público e estratégia')
 perfil=st.selectbox('Público da mensagem',['Residências e comércios','Residências','Comércios'])
 st.caption('O perfil orienta o texto e a qualificação. Ele não identifica quem paga a conta nem cria um filtro de titularidade na Meta.')
 faixa=st.slider('Faixa etária',18,65,(18,65))
 redes=st.multiselect('Onde exibir',['facebook','instagram'],default=['facebook','instagram'],format_func=str.title)
 modo=st.radio('Delimitação geográfica',['Cidade ou região selecionada','Raio do centro da cidade','Ponto e raio — recorte metropolitano'])
 raio=st.slider('Raio do centro da cidade em km',25,80,25) if modo=='Raio do centro da cidade' else None
 ponto={}
 if modo=='Ponto e raio — recorte metropolitano':
  st.caption('Use coordenadas de um endereço público conferido no mapa. Não informe endereço residencial de cliente. O raio é aproximado e não representa cobertura da distribuidora.')
  lat=st.number_input('Latitude do ponto público',value=None,format='%.6f')
  lon=st.number_input('Longitude do ponto público',value=None,format='%.6f')
  rp=st.slider('Raio ao redor do ponto em km',2,25,5)
  conferido=st.checkbox('Conferi no mapa que este ponto público fica na região metropolitana do RJ.')
  ponto={'modo_geo':'ponto','latitude':lat,'longitude':lon,'raio_ponto_km':rp,'ponto_confirmado':conferido}
  if lat is not None and lon is not None and -23.2<=lat<=-22.3 and -43.9<=lon<=-42.6:st.map([{'lat':lat,'lon':lon}],zoom=11)
 st.caption('A Meta confirma os limites aceitos antes da criação. O sistema não amplia automaticamente o raio escolhido.')
 with st.expander('Interesses opcionais'):
  termo=st.text_input('Pesquisar interesse na Meta',value='energia solar')
  if st.button('Consultar interesses disponíveis'):
   try:st.session_state['interesses_meta_piloto']=pesquisar_interesses(meta,termo)
   except Exception:st.error('A Meta não disponibilizou essa pesquisa. É possível continuar com público amplo.')
  interesses=st.multiselect('Interesses retornados pela Meta',st.session_state.get('interesses_meta_piloto',[]),format_func=lambda x:x['name'],max_selections=5)
 return {**ponto,'perfil':perfil,'idade_min':faixa[0],'idade_max':faixa[1],'redes':redes,'raio_km':raio,'interesses':interesses}
