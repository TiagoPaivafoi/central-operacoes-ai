// Reserva conservadora, atômica e não reembolsável para chamadas incertas.
// (v19.2) O banco zera a reserva todo dia 1º (horário de Brasília) e devolve avisar_80 / avisar_100
// uma única vez por mês; aoAvisarOrcamento (opcional) recebe esse retorno para avisar a equipe.
/** @param {{tenant:string, rpc:(f:string,a:Record<string,unknown>)=>Promise<any>, fetchImpl?:typeof fetch, aoAvisarOrcamento?:((r:any)=>Promise<void>)|null}} opcoes */
export function criarFetchOrcado({tenant,rpc,fetchImpl=fetch,aoAvisarOrcamento=null}){
 return async function(url,options={}){
  if(!String(url).startsWith('https://generativelanguage.googleapis.com/'))return fetchImpl(url,options);
  if(!String(url).endsWith(':generateContent')){
   if(String(url).endsWith(':countTokens'))return fetchImpl(url,options);
   throw Error('servico_ia_nao_orcado');
  }
  if(!String(url).endsWith('/gemini-3.1-flash-lite:generateContent'))throw Error('modelo_nao_orcado');
  const body=JSON.parse(options.body),max=Number(body.generationConfig?.maxOutputTokens);
  if(!Number.isInteger(max)||max<1||max>700)throw Error('saida_nao_orcada');
  if(body.generationConfig?.thinkingConfig?.thinkingLevel)throw Error('raciocinio_nao_orcado');
  const endpoint=String(url).replace(':generateContent',':countTokens');
  const counted=await fetchImpl(endpoint,{...options,signal:AbortSignal.timeout(10000),body:JSON.stringify({generateContentRequest:{model:'models/gemini-3.1-flash-lite',...body}})});
  if(!counted.ok)throw Error('contagem_orcamento_indisponivel');
  const n=Number((await counted.json()).totalTokens);
  if(!Number.isInteger(n)||n<1||n>32000)throw Error('entrada_acima_limite');
  // Entrada/saída USD por milhão; câmbio técnico R$8/USD e margem 100%.
  const centavos=Math.max(1,Math.ceil(((n+1024)*.25+max*1.5)/1e6*8*2*100));
  const reserved=await rpc('wa_piloto_reservar_custo',{p_tenant_id:tenant,p_request_id:crypto.randomUUID(),p_centavos:centavos});
  if(aoAvisarOrcamento&&(reserved?.avisar_80===true||reserved?.avisar_100===true)){
   try{await aoAvisarOrcamento(reserved);}catch{/* o aviso nunca derruba o atendimento */}
  }
  if(reserved?.autorizado!==true)throw Error('orcamento_piloto_esgotado_ou_expirado');
  return fetchImpl(url,options);
 };
}

/** (v19.2) Texto do aviso para a equipe (Telegram). Só valores do teto; nada de cliente. */
export function textoAvisoOrcamento(res){
 const reais=(c)=>'R$ '+(Number(c||0)/100).toFixed(2).replace('.',',');
 const usado=reais(res?.reservado_centavos),teto=reais(res?.limite_centavos);
 if(res?.avisar_100===true)return [
  '🔴 Teto de gasto de IA do robô do WhatsApp atingido',
  `Reservado no mês: ${usado} de ${teto}.`,
  'O robô parou de usar a IA: as conversas novas vão para a equipe (aguardando humano).',
  'Volta sozinho no dia 1º do próximo mês, ou antes se o teto for aumentado no Supabase.',
 ].join('\n');
 return [
  '🟡 Gasto de IA do robô do WhatsApp chegou a 80% do teto do mês',
  `Reservado no mês: ${usado} de ${teto}.`,
  'Ao chegar em 100%, o robô passa as conversas novas para a equipe.',
 ].join('\n');
}
