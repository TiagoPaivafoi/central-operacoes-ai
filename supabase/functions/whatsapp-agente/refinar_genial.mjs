import {retirarPercentualEnergia} from './corrigir_percentual_energia.mjs';
// Ajustes determinísticos para o fluxo Genial; não produzem oferta ou aprovação.
export function refinarGenial(dados,ctx){
 if(!dados||typeof dados.resposta!=='string')return dados;
 const contexto=JSON.stringify([dados.produto_interesse,dados.resposta,ctx?.conversa?.ai_state?.produto_interesse,(ctx?.pendentes||[]).map(p=>p.texto),(ctx?.historico||[]).map(h=>h.texto)]);
 if(!/genial/i.test(contexto))return dados;
 const r={...dados};
 r.resposta=r.resposta.replace(/seu perfil é compatível/gi,'seu perfil pode se enquadrar').replace(/um especialista entrará em contato em breve\.?/gi,'').trim();
 if(r.acao==='passar_humano'&&r.resposta.length>220)r.resposta='Vou encaminhar seu pedido à equipe para ajudar no cadastro pelo WhatsApp.';
 r.resposta=r.resposta.replace(/poderia me informar qual a sua cidade e a distribuidora de energia\?/gi,'qual é a sua cidade?');
 if(r.acao!=='passar_humano')r.resposta=retirarPercentualEnergia(r.resposta);
 return r;
}
