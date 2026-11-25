// Referências públicas e limites de operação; não representam ofertas aprovadas.
export const REFERENCIAS_ELECTY={
 SP:['Neoenergia Elektro','CPFL Paulista','CPFL Piratininga','CPFL Santa Cruz','Energisa ESS','EDP SP'],ES:['EDP'],RJ:['Light','Enel RJ'],PR:['Copel'],SC:['Celesc','DCELT'],RS:['RGE','CEEE'],MT:['Energisa EMT'],AM:['AME'],MS:['Energisa MS','Neoenergia Elektro'],DF:['Neoenergia Brasília'],GO:['Equatorial GO'],CE:['Enel CE'],PI:['Equatorial PI'],MA:['Equatorial MA'],TO:['ETO'],PA:['Equatorial PA'],PB:['Energisa PB'],RN:['Cosern'],PE:['Neoenergia PE'],AL:['Equatorial AL'],BA:['Neoenergia BA'],MG:['Cemig']
};
export function referenciaEnergia(uf,distribuidora=''){
 const estado=String(uf).trim().toUpperCase();const referencias=[];
 if(estado==='RJ'&&/light|enel/i.test(distribuidora))referencias.push('Genial Solar: referência inicial Light/Enel RJ, baixa tensão, conta a partir de R$400.');
 if(REFERENCIAS_ELECTY[estado])referencias.push('Electy: lista pública para conferência: '+REFERENCIAS_ELECTY[estado].join(', ')+'.');
 return {referencias,confirmar_unidade:true,oferta_confirmada:false,fonte:'https://electy.com.br/',data_consulta:'2026-11-06'};
}
export const ORIENTACOES_PORTFOLIO=`
ESCOPO AMPLO DO ATENDIMENTO DA CRED FINANCE:
A campanha de origem não limita a conversa. Se perguntarem por outro estado, distribuidora ou produto, acompanhe a necessidade atual. Não dizer que só atende Light/Enel ou energia Genial. Distinga GD de Mercado Livre. Na abertura de interesse em energia, pergunte apenas o valor médio da conta. Cidade/UF, distribuidora e tipo de unidade serão identificados depois na fatura; só perguntar o que faltar quando necessário para verificar cobertura e condições. Não dispensar essa conferência antes de indicar uma proposta.
REFERÊNCIAS, NÃO DISPONIBILIDADE GARANTIDA: Genial Solar RJ Light/EnelRJ conforme condição específica; Electy publica o mapa abaixo em https://electy.com.br/ (consulta06/11/2026). O mapa mistura texto comercial de modalidades: use como referência para conferência, não como confirmação de GD disponível para toda unidade. Não reproduzir inconsistência Sergipe/BA da página. Mercado Livre nacional não significa GD nacional. Matrix: GD observada anteriormente para EnelRJ, demais unidades conferir; Prime/Shell, Serena e outras parcerias exigem confirmação de modalidade, unidade, vínculo e canal de indicação. Não inventar coverage/cadastro/API ou enviar formulário genérico como se atribuísse comissão à Cred Finance. Não aplicar cobrança de uma fatura Genial a outro fornecedor. Se não houver confirmação na base, explique que a equipe vai conferir e encaminhe com os dados já obtidos.
Empresas pausadas por decisão da Cred Finance: Flora, Enel Energia Livre, Tyr, Esfera, Eletron, Raizen e Sunrent; não oferecer. Safira e Reverde não tiveram validação concluída nesta rodada; conferir antes de ofertar. Não dizer que todas as parcerias têm acesso ou capacidade comprovada.
OUTRAS SOLUÇÕES: CLT, antecipação do saque-aniversário FGTS, consignado INSS/servidores, Home Equity, crédito com garantia de veículo (parceiro ainda em confirmação), saque no cartão e recuperação tributária, conforme base e perfil. Não inventar taxa, liberação, aprovação ou integração de produção. Não solicitar dados de um crédito que o cliente não pediu.
ENCERRAMENTO: apenas depois de conclusão real informada pela equipe/parceiro, uma vez por conversa, perguntar exatamente: Você tem interesse em conhecer outras soluções financeiras da Cred Finance? Não listar tudo antes do interesse, não insistir após recusa e não fazer venda adicional em reclamação, solicitação de humano ou atendimento ainda pendente. O código controla esse oferecimento pelo sinal operacao_concluida e registra outras_solucoes_perguntado. Não declarar fechamento só porque recebeu documentos ou mandou link.
Mapa público Electy para conferência: `+JSON.stringify(REFERENCIAS_ELECTY);

export const PERGUNTA_OUTRAS_SOLUCOES='Você tem interesse em conhecer outras soluções financeiras da Cred Finance?';
export function aplicarOfertaComplementar(dados,ctx){
 if(!dados||typeof dados.resposta!=='string')return dados;
 const estado=ctx?.conversa?.ai_state||{};
 const texto=(ctx?.pendentes||[]).map(p=>p.texto||'').join(' ');
 const pode=estado.operacao_concluida===true&&estado.outras_solucoes_perguntado!==true&&!(ctx?.pendentes||[]).some(p=>p.tipo&& !['text','button','interactive'].includes(p.tipo))&&dados.acao!=='passar_humano'&&!ctx?.contato?.opt_out&&!/\?|sair|parar|atendente|humano|reclama|não quero|nao quero|não tenho interesse/i.test(texto);
 if(pode)return {...dados,resposta:PERGUNTA_OUTRAS_SOLUCOES,oferta_complementar_feita:true};
 return {...dados,resposta:dados.resposta.replace(PERGUNTA_OUTRAS_SOLUCOES,'').trim()||'Posso esclarecer alguma dúvida sobre este atendimento?'};
}
