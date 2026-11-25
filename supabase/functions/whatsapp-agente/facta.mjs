// =====================================================================
//  facta.mjs — Facta CLT (Crédito do Trabalhador) de ponta a ponta (v21.1, 23/11/2026)
//  v21.4: início direto (o cliente já disse CLT): sem a pergunta da carteira, pede o CPF e depois o nome
//         (CPF antes do nome também depois do SIM da carteira).
//  v21.2: sem a etapa do termo no WhatsApp: depois do nome e do CPF, a consulta e o link da Facta saem direto
//         para o número do próprio WhatsApp (o link É a autorização); conversa para resolver problemas na
//         autorização (link com erro, não chegou, WhatsApp/SMS de novo, outro número, já autorizei, equipe).
//  v21.1: pergunta única antes do CPF ("Hoje você trabalha de carteira assinada, no regime CLT?");
//         sempre simula o valor MÁXIMO; pedido acima do máximo → "o máximo é R$ X";
//         desistência na oferta → 1 tentativa de retenção com a tabela que paga mais ao cliente (comissão menor).
//  Base: manuais Facta (Crédito do Trabalhador v9.0, Consulta Dados CLT v2.0, Consulta de Propostas v8.0)
//  + testes reais em homologação de 23/11 (onde o manual e o teste divergem, vale o teste):
//    caminhos COM hífen; produto D / tipo_operacao 13 / averbador 10010 / convênio 3;
//    ordem obrigatória autorização → margem → tabelas; opcao_valor=1 exige o campo "valor".
//
//  Etapas (coluna etapa da tabela facta_operacoes):
//   clt_inicio → clt_dados → clt_termo → clt_autorizacao → clt_oferta → (política) → clt_coleta
//   → clt_confirmacao → (etapa1/2/3) → clt_assinatura → clt_assinado → concluida | encerrada | equipe
//
//  Tudo aqui é CÓDIGO FIXO (sem IA). CPF e dados pessoais nunca vão para o Gemini, para a memória do
//  robô (ai_state), para o motivo da passagem nem para o Telegram. Ficam só em facta_operacoes.dados_cliente
//  até a proposta ser cadastrada na Facta (ou a operação terminar); depois são apagados.
//  O token da Facta fica só em memória (1 hora), nunca no banco.
//  Funções puras e dependências injetadas: testável sem custo (testes_facta.mjs).
// =====================================================================
import { cpfValido, normalizar, ehSim, ehNao, ehDesistir, reais, primeiroNome, buscarCep, extrairCpf } from './novosaque.mjs';

export const FACTA_BASE_HOMOL = 'https://webservice-homol.facta.com.br';
export const FACTA_TERMO_VERSAO = 'facta-clt-v1';
export const ETAPAS_FINAIS = ['concluida', 'encerrada', 'equipe'];
const ESPERA_CLIENTE = ['clt_inicio', 'clt_dados', 'clt_termo', 'clt_oferta', 'clt_coleta', 'clt_confirmacao'];
const ESPERA_SISTEMA = ['clt_autorizacao', 'clt_assinatura', 'clt_assinado'];
const FIXOS = { produto: 'D', tipo_operacao: '13', averbador: '10010', convenio: '3' };
const SEG = 1000, MIN = 60 * SEG, HORA = 60 * MIN, DIA = 24 * HORA;

// ---------------------------------------------------------------------
// Textos para o cliente (curtos, sem markdown além do *negrito* do WhatsApp)
// ---------------------------------------------------------------------
export const TXT = {
  inicioDireto: '💼 Vamos simular o seu *consignado CLT* (Crédito do Trabalhador) com a Facta, banco parceiro da Cred Finance.',
  inicio: '💼 Vamos simular o seu *consignado CLT* (Crédito do Trabalhador) com a Facta, banco parceiro da Cred Finance.\n\nHoje você trabalha de *carteira assinada*, no regime *CLT*?\nResponda *SIM* ou *NÃO*.',
  inicioComCpf: '✅ CPF recebido!\n\nHoje você trabalha de *carteira assinada*, no regime *CLT*?\nResponda *SIM* ou *NÃO*.',
  naoEntendiInicio: 'Hoje você trabalha de *carteira assinada*, no regime *CLT*?\nResponda só *SIM* ou *NÃO*.',
  semCarteira: 'Entendi. O consignado CLT é só para quem trabalha com carteira assinada.\n\n💡 Outra opção é a *antecipação do saque-aniversário do FGTS*.\nSe quiser consultar, me envie o seu *CPF* (só os números).\nSe preferir falar com uma pessoa da equipe, é só pedir.',
  nome: '👤 Qual é o seu nome completo?',
  cpf: '📲 Agora me envie o seu *CPF* (só os números).',
  cpfDireto: '📲 Me envie o seu *CPF* (só os números).',
  celularConf: (fim) => `📱 A Facta vai mandar um link de autorização pelo WhatsApp.\n\nPode ser neste número, final *${fim}*?\n1️⃣ Sim\n2️⃣ Outro número`,
  celular: '📱 Qual número com WhatsApp vai receber o link da Facta?\n(com DDD, ex.: 21 99999-9999)',
  nascimento: '📅 Qual é a sua data de nascimento?\n(ex.: 10/05/1985)',
  termo: (url) => `📄 *Autorização de consulta*\n\nPara ver a sua margem e simular o empréstimo, a Facta Financeira (banco parceiro da Cred Finance) consulta os seus dados de trabalho na Dataprev, sistema do governo: empregador, salário e margem consignável.\n\nOs dados são usados só para esta simulação e proposta.${url ? `\nPolítica de Privacidade: ${url}` : ''}\n\nVocê autoriza a consulta?\nResponda *SIM* ou *NÃO*.`,
  naoEntendiTermo: 'Para consultar a sua margem, preciso da sua autorização.\nResponda *SIM* para autorizar ou *NÃO* para parar por aqui.',
  semAceite: 'Tudo bem, não fiz nenhuma consulta.\n\nSe mudar de ideia, é só me chamar.',
  consultando: '🔎 Obrigado! Consultando a sua margem com a Facta...',
  linkAutorizacao: (fim, url = '') => `📲 Pronto! A Facta acabou de mandar um link no WhatsApp do número final *${fim}*.\n\nEsse link é a sua autorização para a Facta (banco parceiro da Cred Finance) consultar a sua margem na Dataprev, o sistema do governo. Seus dados são usados só para esta simulação.${url ? `\nPolítica de Privacidade: ${url}` : ''}\n\n👉 Abra a mensagem da Facta, toque no link e em *Autorizar*.\nDepois me responda *OK*.\n\nSe o link der erro ou a mensagem não chegar, é só me falar que eu resolvo.`,
  reenviado: (canal, fim) => `🔄 Pedi um *link novo* para a Facta, enviado por *${canal === 'SMS' ? 'SMS' : 'WhatsApp'}* para o número final *${fim}*. Às vezes o primeiro link expira ou dá erro.\n\n👉 Use só o link mais novo: toque nele, autorize e me responda *OK*.`,
  problemas: 'Me diga o que aconteceu, respondendo só o número:\n\n1️⃣ O link deu erro\n2️⃣ A mensagem da Facta não chegou\n3️⃣ Quero receber o link por SMS\n4️⃣ Quero usar outro número\n5️⃣ Já autorizei\n6️⃣ Falar com uma pessoa da equipe',
  aguardeConferindo: '⏳ Ainda não apareceu a sua autorização para mim. Às vezes a Facta leva 1 ou 2 minutos para registrar.\n\nVou conferir sozinho e te aviso aqui assim que aparecer.',
  aindaNaoApareceu: '⏳ A autorização ainda não apareceu para mim.',
  erroPersistente: '⚠️ O link da Facta continua dando problema.',
  pedirOutroNumero: '📱 Qual número com WhatsApp vai receber o link? Envie com DDD (ex.: 21 99999-9999).',
  falhaEnvio: '⚠️ A Facta não aceitou um novo envio agora.',
  limiteEnvios: '👤 Já pedimos o link algumas vezes e a autorização não deu certo. Vou pedir para uma pessoa da nossa equipe continuar com você por aqui.',
  linkAutorizacaoSms: (fim) => `📲 Pedi para a Facta mandar o link de autorização por *SMS* para o número final *${fim}*.\n\nAbra o SMS, toque no link e autorize. Depois me responda *OK*.`,
  comoAutorizar: '📲 É rapidinho:\n\n1️⃣ Procure no seu WhatsApp (ou SMS) a mensagem da *Facta* com o link de autorização.\n2️⃣ Toque no link. Vai abrir uma página com os seus dados.\n3️⃣ Confira e toque em *Autorizar* (pode pedir para confirmar o CPF ou a data de nascimento).\n\nA autorização vale só para a consulta da sua margem. Depois me responda *OK*.',
  aindaNaoAutorizado: '⏳ A Facta ainda não recebeu a sua autorização.\n\nToque no link que a Facta mandou e autorize. Depois me responda *OK*.\nSe a mensagem não chegou, responda *SMS*.',
  lembreteAutorizacao: '📲 Passando para lembrar: para ver a sua margem do consignado CLT, falta só tocar no link da Facta e autorizar.\n\nDepois me responda *OK*.',
  telefoneOutroCpf: '⚠️ Esse número de celular já está ligado a outro CPF na Facta.\n\nTem outro número com WhatsApp? Me envie com DDD.\n(se não tiver, responda *NÃO*)',
  virada: '⏳ O sistema do governo (Dataprev) está em atualização da folha de pagamento e a consulta está fora do ar.\n\nAssim que voltar, eu consulto e te chamo aqui.',
  semVinculo: 'Consultei com a Facta, mas não apareceu um emprego com carteira assinada *elegível* para o consignado CLT no seu CPF.',
  semMargem: 'Consultei com a Facta, mas no momento você não tem *margem disponível* para o consignado CLT.',
  semOfertaFacta: 'Consultei com a Facta, mas no momento ela não tem uma opção de consignado CLT para a sua margem.',
  oferecerFgts: '\n\n💡 Outra opção é a *antecipação do saque-aniversário do FGTS*.\nSe quiser consultar, me envie o seu *CPF* (só os números).\nSe preferir falar com uma pessoa da equipe, é só pedir.',
  naoEntendiOferta: (n) => `Não entendi.\n\n👉 Responda o número da opção (1${n > 1 ? ` a ${n}` : ''}).\nParcela menor: escreva *PARCELA* e o valor (ex.: PARCELA 150).\nValor específico: escreva *VALOR* e o valor (ex.: VALOR 3000).\nPara parar, responda *ENCERRAR*.`,
  semOpcaoPedido: 'Não encontrei opção da Facta com esse valor.\n\nAs opções que eu te mostrei continuam valendo:',
  acimaDoMaximo: (v) => `O valor máximo que você consegue hoje no consignado CLT é *${reais(v)}*.\n\nAs opções que eu te mostrei já são as de maior valor:`,
  jaEhMaximo: (v) => `Esse já é o valor máximo que a sua margem permite hoje: até *${reais(v)}*.\n\nAs opções continuam valendo:`,
  retencao: '💡 Espera! Consegui uma condição *melhor* para você:',
  parcelaAcimaMargem: (m) => `A parcela não pode passar da sua margem disponível (${reais(m)}).\n\nEnvie um valor de parcela menor, ou escolha uma das opções.`,
  verificando: '🔎 Ótima escolha! Estou conferindo a aprovação com a Facta...',
  politicaReduzida: (v) => `A Facta aprovou um valor menor para você: até *${reais(v)}* de empréstimo.\n\nVeja as opções:`,
  inicioDados: '✅ Aprovado na análise inicial da Facta!\n\nPara preencher a proposta, preciso de alguns dados do seu documento e endereço. Um de cada vez, rapidinho.',
  enviando: '📤 Enviando a sua proposta para a Facta...',
  link: (l) => `📝 Sua proposta foi cadastrada na Facta!\n\nPara concluir, assine pelo celular neste link:\n${l}\n\nA assinatura é digital e pode pedir uma selfie.\nDepois de assinar, te aviso aqui cada passo até o dinheiro cair na sua conta.`,
  linkPorWhatsappFacta: '📝 Sua proposta foi cadastrada na Facta!\n\nA Facta mandou o link de assinatura no seu WhatsApp (ou SMS).\nAbra a mensagem da Facta e assine pelo celular. Depois de assinar, te aviso aqui cada passo.',
  lembreteLink: (l) => `📝 Passando para lembrar: a sua proposta do consignado CLT ainda está esperando a assinatura.${l ? `\n\nÉ só abrir este link:\n${l}` : '\n\nAbra a mensagem da Facta com o link de assinatura.'}`,
  aguardandoAssinatura: (l) => `⏳ A sua proposta está esperando a assinatura digital.${l ? `\n\nLink:\n${l}` : ''}`,
  assinado: '✅ Recebemos a sua assinatura!\n\nAgora a Facta faz a averbação do contrato com o seu empregador e, em seguida, envia o pagamento.\nTe aviso aqui quando o dinheiro for enviado.',
  emAnalise: '⏳ O seu contrato já foi assinado e está em averbação na Facta.\nAssim que o pagamento for enviado, te aviso aqui.',
  pago: (v) => `🎉 Pagamento feito!\n\nA Facta enviou ${v ? `*${v}*` : 'o valor da proposta'} para você.\n\nObrigado por escolher a Cred Finance!`,
  cancelada: '⚠️ A Facta informou que a sua proposta foi cancelada.\n\n👤 Vou pedir para uma pessoa da nossa equipe ver com você o que aconteceu.',
  encerrado: 'Tudo bem, encerrei a proposta por aqui.\n\nSe mudar de ideia, é só me chamar.',
  passarEquipe: '👤 Vou pedir para uma pessoa da nossa equipe continuar com você por aqui para concluir a proposta.',
  confirmarResumo: 'Para enviar a proposta, responda *SIM*.\nPara refazer os dados, responda *CORRIGIR*.',
  naoConfirmou: 'Sem problemas.\n\nPara refazer os dados, responda *CORRIGIR*.\nSe preferir parar por aqui, responda *ENCERRAR*.',
};

// Perguntas da coleta (um dado por vez) e ajuda fixa quando o cliente não sabe
const ESTADOS_CIVIS = [['1', 'Solteiro(a)', '4'], ['2', 'Casado(a)', '3'], ['3', 'União estável', '9'], ['4', 'Divorciado(a)', '2'], ['5', 'Separado(a)', '1'], ['6', 'Viúvo(a)', '5'], ['7', 'Outro', '6']];
const PATRIMONIOS = [['1', 'Até R$ 10 mil'], ['2', 'De R$ 10 mil a R$ 100 mil'], ['3', 'De R$ 100 mil a R$ 500 mil'], ['4', 'De R$ 500 mil a R$ 1 milhão'], ['5', 'Acima de R$ 1 milhão']];
const UFS = ['AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MT', 'MS', 'MG', 'PA', 'PB', 'PR', 'PE', 'PI', 'RJ', 'RN', 'RS', 'RO', 'RR', 'SC', 'SP', 'SE', 'TO'];
const ORGAOS_COMUNS = ['SSP', 'DETRAN', 'IFP', 'PC', 'SDS', 'SESP', 'SSPDS', 'SEJUSP', 'DIC', 'IIRGD', 'IGP', 'PM', 'PF', 'MD', 'MAER', 'MEX', 'MMA', 'CNH', 'OAB', 'CRM', 'CREA'];

export const PERGUNTAS = {
  sexo: 'Para o contrato, o banco pede o sexo que consta no seu documento.\nResponda *M* (masculino) ou *F* (feminino).',
  nome_mae: '👩 Qual é o nome completo da sua mãe?',
  rg: '🪪 Qual é o número do seu *RG*?\n(como está no documento, com letras se tiver)',
  rg_orgao: 'Qual é o *órgão emissor* do RG?\n(é a sigla perto do número, ex.: SSP, DETRAN, IFP)',
  rg_uf: 'De qual *estado* é o seu RG?\n(sigla, ex.: RJ)',
  rg_expedicao: '📅 Qual é a *data de expedição* do RG?\n(ex.: 15/03/2010)',
  estado_civil: `💍 Qual é o seu estado civil?\n${ESTADOS_CIVIS.map(([n, t]) => `${n} - ${t}`).join('\n')}\n\nResponda só o número.`,
  naturalidade: '📍 Em qual *cidade e estado* você nasceu?\n(ex.: Duque de Caxias/RJ)',
  cep: '🏠 Qual é o *CEP* da sua casa?\n(só os 8 números)',
  logradouro: 'Qual é o nome da sua rua?',
  bairro: 'Qual é o seu bairro?',
  cidade_uf: 'Qual é a sua cidade e estado?\n(ex.: Rio de Janeiro/RJ)',
  numero: 'Qual é o número da casa ou do prédio?\n(pode incluir o apartamento, ex.: 369 apto 104; sem número, responda S/N)',
  nome_pai: '👨 Qual é o nome completo do seu pai?\n(se não consta no documento, responda *NÃO CONSTA*)',
  patrimonio: `🏦 Qual é o valor aproximado dos seus bens (casa, carro, aplicações)?\n${PATRIMONIOS.map(([n, t]) => `${n} - ${t}`).join('\n')}\n\nResponda só o número.`,
  banco: '🏦 Qual é o seu banco?\n(ex.: Caixa, Itaú, Nubank)',
  agencia: 'Qual é o número da *agência*?\n(sem o dígito)',
  conta: 'Qual é o número da *conta* com o dígito?\n(ex.: 12345-6)',
  tipo_conta: 'É conta *corrente* ou *poupança*?\nResponda *C* ou *P*.',
  pix: '💰 O dinheiro é enviado por PIX para uma chave *no seu nome*.\n\nA sua chave PIX é o seu *CPF*? Responda *SIM*.\nSe for outra, me envie a chave (celular, e-mail ou chave aleatória).',
  email: '📧 Qual é o seu e-mail?\n(se não tiver, responda *PULAR*)',
};
const AJUDA = {
  rg: 'O número do RG fica na frente da carteira de identidade, perto da foto ou no alto ("Registro Geral").\nSe tiver só a CNH, o número do RG aparece no campo "Doc. Identidade".',
  rg_orgao: 'O órgão emissor é a sigla que aparece junto do número do RG, por exemplo: SSP, DETRAN, IFP, PC.\nNa CNH, aparece no campo "Doc. Identidade / Órg. Emissor / UF".',
  rg_uf: 'É a sigla do estado que emitiu o seu RG (ex.: RJ, SP, MG). Aparece junto do órgão emissor, como "SSP/RJ".',
  rg_expedicao: 'A data de expedição fica no verso da identidade, perto de "Data de expedição".',
  naturalidade: 'É a cidade onde você nasceu (aparece no RG como "Naturalidade"), com a sigla do estado. Ex.: Niterói/RJ.',
  cep: 'O CEP aparece na conta de luz ou de água. Se não souber, procure "busca CEP" no Google com o nome da sua rua.',
  agencia: 'O número da agência aparece no app do banco, em "Dados da conta".',
  conta: 'O número da conta com o dígito aparece no app do banco, em "Dados da conta".',
};
const ERROS = {
  nome: 'Preciso do nome completo (nome e sobrenome), sem números.',
  cpf: 'Esse CPF não parece válido. Confira os 11 números e envie de novo.',
  celular: 'Envie o celular com DDD, por exemplo: 21 99999-9999.',
  nascimento: 'Não consegui ler a data. Envie no formato dia/mês/ano, por exemplo 10/05/1985.',
  sexo: 'Responda só M ou F.',
  nome_mae: 'Preciso do nome completo da sua mãe (nome e sobrenome).',
  rg: 'Envie o número do RG (de 5 a 14 números/letras).',
  rg_orgao: 'Não reconheci esse órgão. Envie a sigla, por exemplo SSP, DETRAN ou IFP.',
  rg_uf: 'Envie a sigla do estado com 2 letras, por exemplo RJ.',
  rg_expedicao: 'Não consegui ler a data de expedição. Envie no formato dia/mês/ano, por exemplo 15/03/2010.',
  estado_civil: 'Responda só o número da opção (1 a 7).',
  naturalidade: 'Envie a cidade e a sigla do estado, por exemplo: Duque de Caxias/RJ.',
  naturalidade_nao_achada: 'Não encontrei essa cidade nesse estado. Confira o nome e a sigla, por exemplo: Duque de Caxias/RJ.',
  cep: 'O CEP tem 8 números. Pode conferir?',
  cep_nao_achado: 'Não encontrei esse CEP. Pode conferir os 8 números?',
  cidade_nao_achada: 'Não encontrei a cidade do seu endereço na lista da Facta. Envie a cidade e a sigla do estado, por exemplo: Rio de Janeiro/RJ.',
  logradouro: 'Pode me enviar o nome da rua?',
  bairro: 'Pode me enviar o nome do bairro?',
  cidade_uf: 'Envie a cidade e a sigla do estado, por exemplo: Rio de Janeiro/RJ.',
  numero: 'Envie o número (e, se quiser, o apartamento), ou S/N se não tiver.',
  nome_pai: 'Envie o nome completo do seu pai, ou responda NÃO CONSTA.',
  patrimonio: 'Responda só o número da opção (1 a 5).',
  banco: 'Não encontrei esse banco. Envie o nome do banco, por exemplo: Caixa, Itaú, Bradesco, Nubank.',
  agencia: 'Envie só os números da agência (de 1 a 5 números).',
  conta: 'Envie o número da conta com o dígito, por exemplo 12345-6.',
  tipo_conta: 'Responda C (corrente) ou P (poupança).',
  pix: 'Não reconheci essa chave PIX. Responda SIM se a chave é o seu CPF, ou envie o celular com DDD, o e-mail ou a chave aleatória.',
  pix_cpf_outro: 'A chave PIX precisa estar no seu nome. Se for CPF, tem que ser o seu.',
  email: 'Esse e-mail não parece válido. Pode conferir? (ou responda PULAR)',
};

// ---------------------------------------------------------------------
// Utilidades
// ---------------------------------------------------------------------
export function num(v) {
  if (typeof v === 'number') return v;
  const s = String(v ?? '').trim().replace(/^R\$\s*/i, '');
  if (!s) return NaN;
  return Number(s.includes(',') ? s.replace(/\./g, '').replace(',', '.') : s);
}
const dig = (s) => String(s ?? '').replace(/\D/g, '');
const dinheiroApi = (v) => (Math.round(num(v) * 100) / 100).toFixed(2);

/** Celular só com DDD + número (11 dígitos), a partir do wa_id (55 + DDD + número). */
export function celularDoWa(waId) {
  let d = dig(waId);
  if (d.length === 13 && d.startsWith('55')) d = d.slice(2);
  if (d.length === 12 && d.startsWith('55')) d = d.slice(2); // número antigo sem o 9
  if (d.length === 10 && /^[6-9]/.test(d[2])) d = d.slice(0, 2) + '9' + d.slice(2);
  return d.length === 11 && d[2] === '9' ? d : null;
}
export function lerCelular(texto) {
  let d = dig(texto);
  if (d.length === 13 && d.startsWith('55')) d = d.slice(2);
  return d.length === 11 && d[2] === '9' ? d : null;
}
export function formatarCelular(d) {
  const s = dig(d);
  return s.length === 11 ? `(${s.slice(0, 2)}) ${s.slice(2, 7)}-${s.slice(7)}` : '';
}

/** Data em vários formatos (dd/mm/aaaa, aaaa-mm-dd, aaaa-mm-ddThh...) → dd/mm/aaaa. */
export function dataBr(v) {
  const s = String(v ?? '').trim();
  let m = s.match(/^(\d{1,2})\s*[/.\- ]\s*(\d{1,2})\s*[/.\- ]\s*(\d{4})/);
  if (m) return `${m[1].padStart(2, '0')}/${m[2].padStart(2, '0')}/${m[3]}`;
  m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return `${m[3]}/${m[2]}/${m[1]}`;
  m = s.match(/^(\d{2})(\d{2})(\d{4})$/);
  if (m) return `${m[1]}/${m[2]}/${m[3]}`;
  return '';
}
function msDe(br) {
  const m = String(br).match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  if (!m) return NaN;
  const [d, mes, a] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const t = Date.UTC(a, mes - 1, d);
  const dt = new Date(t);
  return dt.getUTCFullYear() === a && dt.getUTCMonth() === mes - 1 && dt.getUTCDate() === d ? t : NaN;
}
export function lerData(texto, { minAnos = null, maxAnos = null, agora = Date.now() } = {}) {
  const br = dataBr(texto);
  const t = msDe(br);
  if (!Number.isFinite(t) || t > agora) return null;
  const anos = (agora - t) / (365.25 * DIA);
  if (minAnos !== null && anos < minAnos) return null;
  if (maxAnos !== null && anos > maxAnos) return null;
  return br;
}
function lerNomeCompleto(t) {
  const s = String(t ?? '').replace(/\s+/g, ' ').trim();
  if (s.length < 5 || s.length > 120 || /\d/.test(s) || !/^[\p{L}' .-]+$/u.test(s) || s.split(' ').filter((x) => x.length > 1).length < 2) return null;
  return s.toUpperCase();
}
function lerCidadeUf(t) {
  const m = String(t ?? '').trim().match(/^(.{2,80}?)\s*[/,\- ]\s*([A-Za-z]{2})$/);
  if (!m || !UFS.includes(m[2].toUpperCase())) return null;
  return { cidade: m[1].trim(), uf: m[2].toUpperCase() };
}
const verdade = (v) => v === true || v === 1 || /^(true|s|sim|1|y|yes)$/i.test(String(v ?? '').trim());

/** Valor em reais escrito pelo cliente: "R$ 1.500,00", "1500", "1,5 mil", "5 mil", "5k". */
export function lerValorReais(texto) {
  const s = String(texto ?? '').toLowerCase().normalize('NFD').replace(/\p{M}/gu, '').replace(/r\$\s*/g, '');
  const m = s.match(/(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(mil|k)?\b/);
  if (!m) return null;
  let base = m[1];
  if (/^\d{1,3}(\.\d{3})+/.test(base)) base = base.replace(/\./g, '').replace(',', '.');
  else base = base.replace(',', '.');
  let v = Number(base);
  if (!Number.isFinite(v)) return null;
  if (m[2]) v *= 1000;
  return v > 0 ? Math.round(v * 100) / 100 : null;
}

// ---------------------------------------------------------------------
// API da Facta (fetch injetado; usuário, senha e token nunca são registrados)
// ---------------------------------------------------------------------
const tokens = new Map(); // base|usuario → { token, ate } — só em memória

export function criarApiFacta({ usuario, senha, base = FACTA_BASE_HOMOL, fetchImpl = fetch, agora = () => Date.now(), timeoutMs = 25000, pausaMs = 1500 }) {
  const raiz = String(base || FACTA_BASE_HOMOL).trim().replace(/\/+$/, '');
  const chaveCache = `${raiz}|${usuario}`;

  async function token(forcar = false) {
    const c = tokens.get(chaveCache);
    if (!forcar && c && c.ate > agora()) return c.token;
    if (!usuario || !senha) return null;
    for (let t = 0; t < 2; t++) {
      try {
        const r = await fetchImpl(`${raiz}/gera-token`, { headers: { Authorization: `Basic ${btoa(`${usuario}:${senha}`)}` }, signal: AbortSignal.timeout(15000) });
        const texto = await r.text().catch(() => '');
        let d = null;
        try { d = JSON.parse(texto); } catch { /* HTML = Cloudflare */ }
        if (r.ok && d && d.erro !== true && typeof d.token === 'string' && d.token.length > 10) {
          tokens.set(chaveCache, { token: d.token, ate: agora() + 55 * MIN });
          return d.token;
        }
        return null;
      } catch {
        if (t === 0) await new Promise((ok) => setTimeout(ok, pausaMs));
      }
    }
    return null;
  }

  /** Resultado: { status, dados, bloqueado?, semResposta?, semLogin? }. dados = JSON da Facta (ou null). */
  async function chamar(metodo, caminho, params = {}, { repetir = true } = {}) {
    const limpo = Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== null).map(([k, v]) => [k, String(v)]));
    let renovou = false;
    for (let t = 0; t < 3; t++) {
      const tk = await token(renovou);
      if (!tk) return { status: 0, dados: null, semLogin: true };
      const qs = new URLSearchParams(limpo).toString();
      const url = metodo === 'GET' && qs ? `${raiz}${caminho}?${qs}` : `${raiz}${caminho}`;
      const headers = { Authorization: `Bearer ${tk}`, Accept: 'application/json' };
      const init = { method: metodo, headers, signal: AbortSignal.timeout(timeoutMs) };
      if (metodo === 'POST') { headers['Content-Type'] = 'application/x-www-form-urlencoded'; init.body = qs; }
      let r;
      try {
        r = await fetchImpl(url, init);
      } catch (e) {
        // POST que estourou o tempo pode ter sido gravado na Facta: não repete (evita proposta em dobro)
        const tempo = e && (e.name === 'TimeoutError' || e.name === 'AbortError');
        if (repetir && !(metodo === 'POST' && tempo) && t < 2) { await new Promise((ok) => setTimeout(ok, pausaMs * (t + 1))); continue; }
        return { status: 0, dados: null, semResposta: true };
      }
      const texto = await r.text().catch(() => '');
      let dados = null;
      try { dados = JSON.parse(texto); } catch { /* HTML */ }
      if (r.status === 401 && !renovou) { renovou = true; tokens.delete(chaveCache); continue; }
      if (repetir && metodo === 'GET' && [502, 503, 504].includes(r.status) && t < 2) { await new Promise((ok) => setTimeout(ok, pausaMs * 2 * (t + 1))); continue; }
      if (!dados || typeof dados !== 'object') return { status: r.status, dados: null, bloqueado: /cloudflare|attention required|cf-ray/i.test(texto) };
      return { status: r.status, dados };
    }
    return { status: 0, dados: null, semResposta: true };
  }

  const comHifen = (s) => s; // caminhos já com hífen (os sem hífen são barrados pela Cloudflare)
  return {
    token,
    autorizaConsulta: (cpf) => chamar('GET', comHifen('/consignado-trabalhador/autoriza-consulta'), { cpf: dig(cpf) }),
    solicitarAutorizacao: ({ nome, cpf, celular, tipo_envio = 'WHATSAPP' }) => chamar('POST', '/solicita-autorizacao-consulta', {
      averbador: FIXOS.averbador, nome, cpf: dig(cpf), celular: formatarCelular(celular), tipo_envio, representante_legal: 'N',
    }, { repetir: false }),
    operacoes: (p) => chamar('GET', '/proposta/operacoes-disponiveis', { ...FIXOS, ...p }),
    politica: (p) => chamar('GET', '/consignado-trabalhador/analise-politica-credito', p),
    etapa1: (p) => chamar('POST', '/proposta/etapa1-simulador', { ...FIXOS, ...p }, { repetir: false }),
    etapa2: (p) => chamar('POST', '/proposta/etapa2-dados-pessoais', p, { repetir: false }),
    etapa3: (p) => chamar('POST', '/proposta/etapa3-proposta-cadastro', p, { repetir: false }),
    envioLink: (codigo_af, tipo_envio = 'whatsapp') => chamar('POST', '/proposta/envio-link', { codigo_af, tipo_envio }, { repetir: false }),
    andamento: (af) => chamar('GET', '/proposta/andamento-propostas', { af }),
    combo: (nome, params = {}) => chamar('GET', `/proposta-combos/${nome}`, params),
  };
}

/** Combos com cache em memória (cidade, banco, órgão emissor). */
export function criarCombos(api) {
  const cache = new Map();
  async function lista(nome, params, campo) {
    const k = `${nome}|${JSON.stringify(params)}`;
    if (cache.has(k)) return cache.get(k);
    const r = await api.combo(nome, params);
    const obj = r.dados && r.dados.erro !== true ? r.dados[campo] : null;
    const val = obj && typeof obj === 'object' ? obj : null;
    if (val) cache.set(k, val);
    return val;
  }
  return {
    /** id da cidade na Facta (ex.: Rio de Janeiro/RJ = 405) ou null. undefined = combo fora do ar. */
    async cidade(nome, uf) {
      const l = await lista('cidade', { estado: uf, nome_cidade: normalizar(nome).toUpperCase() }, 'cidade');
      if (!l) return undefined;
      const alvo = normalizar(nome);
      const itens = Object.entries(l).map(([id, v]) => ({ id, nome: normalizar(typeof v === 'object' ? v?.nome : v), uf: String(v?.estado ?? uf).toUpperCase() }))
        .filter((x) => x.uf === uf);
      const exata = itens.find((x) => x.nome === alvo);
      if (exata) return exata.id;
      return itens.length === 1 ? itens[0].id : null;
    },
    async banco(nome) {
      const l = await lista('banco', { nome_banco: String(nome).trim() }, 'lista_banco');
      if (!l) return undefined;
      const itens = Object.entries(l);
      if (!itens.length) return null;
      const alvo = normalizar(nome);
      const exata = itens.find(([, v]) => normalizar(v) === alvo || normalizar(v).startsWith(alvo));
      return { id: (exata ?? itens[0])[0], nome: String((exata ?? itens[0])[1]) };
    },
    async orgaos() {
      return await lista('orgao-emissor', {}, 'orgao_emissor');
    },
  };
}

// ---------------------------------------------------------------------
// Leitura das respostas da Facta
// ---------------------------------------------------------------------
/** autoriza-consulta → { tipo: 'dados'|'precisa'|'virada'|'cpf_invalido'|'sem_vinculo'|'falha'|'erro', vinculos, mensagem } */
export function lerAutorizacao(r) {
  if (!r?.dados) return { tipo: 'falha', vinculos: [], mensagem: r?.bloqueado ? 'bloqueado pela Cloudflare' : (r?.semLogin ? 'login da Facta falhou' : 'sem resposta') };
  const msg = String(r.dados.mensagem ?? '');
  const vinculos = Array.isArray(r.dados?.dados_trabalhador?.dados) ? r.dados.dados_trabalhador.dados : [];
  if (/virada de folha/i.test(msg)) return { tipo: 'virada', vinculos, mensagem: msg };
  if (vinculos.length) return { tipo: 'dados', vinculos, mensagem: msg };
  if (/token expirado|solicita-autorizacao-consulta/i.test(msg)) return { tipo: 'precisa', vinculos, mensagem: msg };
  if (/n[aã]o [eé] um cpf v[aá]lido/i.test(msg)) return { tipo: 'cpf_invalido', vinculos, mensagem: msg };
  if (r.dados.erro !== true) return { tipo: 'sem_vinculo', vinculos, mensagem: msg };
  return { tipo: 'erro', vinculos, mensagem: msg };
}

/** solicita-autorizacao-consulta → 'enviada'|'valida'|'telefone_outro_cpf'|'erro' */
export function lerSolicitacao(r) {
  if (!r?.dados) return { tipo: 'erro', mensagem: r?.bloqueado ? 'bloqueado pela Cloudflare' : 'sem resposta' };
  const msg = String(r.dados.mensagem ?? '');
  if (/telefone j[aá] informado para outro cpf/i.test(msg)) return { tipo: 'telefone_outro_cpf', mensagem: msg };
  if (/token v[aá]lido|n[aã]o necessita/i.test(msg)) return { tipo: 'valida', mensagem: msg };
  if (r.dados.erro === true) return { tipo: 'erro', mensagem: msg };
  return { tipo: 'enviada', mensagem: msg };
}

/** Escolhe o vínculo elegível, sem desligamento, com a maior margem disponível. */
export function escolherVinculo(vinculos) {
  const lista = Array.isArray(vinculos) ? vinculos : [];
  if (!lista.length) return { vinculo: null, motivo: 'sem_vinculo' };
  const ativos = lista.filter((v) => !String(v?.dataDesligamento ?? '').trim());
  const elegiveis = ativos.filter((v) => verdade(v?.elegivel));
  if (!elegiveis.length) return { vinculo: null, motivo: 'inelegivel' };
  const comMargem = elegiveis.filter((v) => num(v.valorMargemDisponivel) > 0).sort((a, b) => num(b.valorMargemDisponivel) - num(a.valorMargemDisponivel));
  if (!comMargem.length) return { vinculo: null, motivo: 'sem_margem' };
  return { vinculo: comMargem[0], motivo: null };
}

export function sexoDe(v) {
  const d = normalizar(v?.sexo_descricao ?? '');
  if (d.startsWith('f')) return 'F';
  if (d.startsWith('m')) return 'M';
  const c = String(v?.sexo_codigo ?? '').trim().toUpperCase();
  if (c === 'F' || c === '2') return 'F';
  if (c === 'M' || c === '1') return 'M';
  return null;
}

/** Dados do vínculo que vão para a proposta (a Dataprev já devolve: não pergunta ao cliente). */
export function dadosDoVinculo(v) {
  const margem = Math.floor(num(v.valorMargemDisponivel) * 100) / 100;
  const out = {
    matricula: String(v.matricula ?? '').trim(),
    cnpj_empregador: dig(v.numeroInscricaoEmpregador),
    data_admissao: dataBr(v.dataAdmissao),
    renda: num(v.valorTotalVencimentos) > 0 ? num(v.valorTotalVencimentos) : num(v.valorBaseMargem),
    margem,
    empregador: String(v.nomeEmpregador ?? '').trim().slice(0, 80),
  };
  const nome = lerNomeCompleto(v.nome);
  if (nome) out.nome_oficial = nome;
  const nasc = dataBr(v.dataNascimento);
  if (nasc) out.nascimento = nasc;
  const sexo = sexoDe(v);
  if (sexo) out.sexo = sexo;
  const mae = lerNomeCompleto(v.nomeMae);
  if (mae) out.nome_mae = mae;
  return out;
}

/**
 * Resume as tabelas de operacoes-disponiveis em até cfg.max_ofertas opções (padrão 3).
 * Uma opção por prazo (a de maior valor líquido, respeitando tabelas_preferidas = ordem por comissão);
 * depois ordena por preferência e pelo maior valor líquido. Nunca mostra comissão nem nome de tabela ao cliente.
 */
export function resumirTabelas(tabelas, cfg = {}) {
  const pref = (cfg.tabelas_preferidas ?? []).map((s) => String(s).toUpperCase());
  const pos = (t) => {
    const i = pref.findIndex((p) => p === String(t.codigoTabela ?? '').toUpperCase() || p === String(t.tabela ?? '').toUpperCase());
    return i < 0 ? 999 : i;
  };
  const validas = (Array.isArray(tabelas) ? tabelas : [])
    .filter((t) => t && num(t.valor_liquido) > 0 && num(t.parcela) > 0 && num(t.prazo) > 0 && String(t.codigoTabela ?? '').trim())
    .map((t) => ({
      codigo_tabela: String(t.codigoTabela), prazo: Math.round(num(t.prazo)), parcela: num(t.parcela), contrato: num(t.contrato),
      liquido: num(t.valor_liquido), taxa: num(t.taxa), coeficiente: String(t.coeficiente ?? ''), seguro: num(t.valor_seguro) || 0, pref: pos(t),
    }));
  const porPrazo = new Map();
  for (const t of validas) {
    const a = porPrazo.get(t.prazo);
    if (!a || t.pref < a.pref || (t.pref === a.pref && t.liquido > a.liquido)) porPrazo.set(t.prazo, t);
  }
  const max = Math.max(1, Math.min(5, Number(cfg.max_ofertas) || 3));
  return [...porPrazo.values()].sort((a, b) => a.pref - b.pref || b.liquido - a.liquido).slice(0, max)
    .map(({ pref, ...o }) => o);
}

/** Oferta CLT (decisão de 23/11): parcela + valor líquido, sem taxa e sem CET. */
export function textoOfertas(ofertas, { nome = '', prefixo = '' } = {}) {
  const n = ofertas.length;
  const nums = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣'];
  const linhas = [];
  if (prefixo) linhas.push(prefixo, '');
  else linhas.push(`✅ ${nome ? `${nome}, e` : 'E'}ncontrei ${n > 1 ? 'opções' : 'uma opção'} de consignado CLT para você:`, '');
  ofertas.forEach((o, i) => linhas.push(`${nums[i]} Parcela de *${reais(o.parcela)}* em ${o.prazo}x — você recebe *${reais(o.liquido)}*`));
  linhas.push('', 'A parcela é descontada direto do seu salário. As condições completas estão no contrato que você vai assinar.', '');
  linhas.push(n > 1 ? `👉 Para seguir, responda o número da opção (1 a ${n}).` : '👉 Para seguir, responda *1*.');
  linhas.push('Quer uma parcela menor? Escreva *PARCELA* e o valor (ex.: PARCELA 150).');
  linhas.push('Quer um valor específico? Escreva *VALOR* e o valor (ex.: VALOR 3000).');
  return linhas.join('\n');
}

/** Resposta à oferta: { escolha } | { parcela } | { valor } | 'desistir' | null */
export function lerRespostaOferta(texto, total) {
  if (ehDesistir(texto)) return 'desistir';
  const n = normalizar(texto);
  const m = n.match(/^(?:opcao |a |numero |n )?([1-5])(?:a|o)?$/);
  if (m && Number(m[1]) >= 1 && Number(m[1]) <= total) return { escolha: Number(m[1]) - 1 };
  if (total === 1 && ehSim(texto)) return { escolha: 0 };
  const bruto = String(texto ?? '').replace(/\b\d{1,3}\s*x\b/gi, '');
  if (/\bparcela/.test(n)) { const v = lerValorReais(bruto); if (v) return { parcela: v }; }
  if (/\b(valor|quero|preciso|liberar|receber|emprestimo|pegar)\b/.test(n)) { const v = lerValorReais(bruto); if (v && v >= 100) return { valor: v }; }
  return null;
}

/** Status de andamento-propostas → evento. */
export function lerStatusProposta(p) {
  if (!p) return 'sem_dados';
  const st = normalizar(p.status_proposta ?? '');
  if (/cancelad/.test(st)) return 'cancelada';
  if (String(p.data_pgto_cliente ?? '').trim() || /^16\b/.test(st) || /\bpag(o|a|amento)\b/.test(st)) return 'pago';
  if (/assinatura digital ok|aguarda averbacao|averbad/.test(st)) return 'assinado';
  if (/aguardando assinatura/.test(st)) return 'aguardando_assinatura';
  return 'andamento';
}

// ---------------------------------------------------------------------
// Coleta (um dado por vez)
// ---------------------------------------------------------------------
export function ordemCampos(cfg = {}) {
  return ['sexo', 'nome_mae', 'rg', 'rg_orgao', 'rg_uf', 'rg_expedicao', 'estado_civil', 'naturalidade', 'cep', 'logradouro', 'bairro', 'cidade_uf', 'numero', 'nome_pai', 'patrimonio',
    ...(cfg.pedir_conta_bancaria === true ? ['banco', 'agencia', 'conta', 'tipo_conta'] : []), 'pix', 'email'];
}
const preenchido = (v) => v !== undefined && v !== null && v !== '';
export function proximoCampo(d, cfg = {}) {
  for (const c of ordemCampos(cfg)) {
    if (c === 'cidade_uf') { if (!(d.cidade && d.uf)) return c; continue; }
    if (c === 'naturalidade') { if (!preenchido(d.natural_cidade_id)) return c; continue; }
    if (!preenchido(d[c])) return c;
  }
  return null;
}
const ehNaoSei = (t) => /\b(nao sei|nao lembro|nao tenho|nao acho|nao encontro|onde (fica|acho|vejo|esta)|como (vejo|acho|sei)|ajuda|duvida)\b|\?/.test(normalizar(t)) && !/\bnao consta\b/.test(normalizar(t));

/** Lê um campo síncrono. { ok, patch } | { ok:false, erro } */
export function lerCampo(campo, texto, d = {}, agora = Date.now()) {
  const t = String(texto ?? '').trim();
  const n = normalizar(t);
  switch (campo) {
    case 'sexo':
      if (/^(m|masculino|homem)$/.test(n)) return { ok: true, patch: { sexo: 'M' } };
      if (/^(f|feminino|mulher)$/.test(n)) return { ok: true, patch: { sexo: 'F' } };
      return { ok: false, erro: ERROS.sexo };
    case 'nome_mae': { const v = lerNomeCompleto(t); return v ? { ok: true, patch: { nome_mae: v } } : { ok: false, erro: ERROS.nome_mae }; }
    case 'rg': { const v = t.toUpperCase().replace(/[^0-9A-Z]/g, ''); return v.length >= 5 && v.length <= 14 && /\d{4,}/.test(v) ? { ok: true, patch: { rg: v } } : { ok: false, erro: ERROS.rg }; }
    case 'rg_uf': { const v = t.toUpperCase().replace(/[^A-Z]/g, ''); return UFS.includes(v) ? { ok: true, patch: { rg_uf: v } } : { ok: false, erro: ERROS.rg_uf }; }
    case 'rg_expedicao': {
      const v = lerData(t, { agora });
      if (!v) return { ok: false, erro: ERROS.rg_expedicao };
      if (d.nascimento && msDe(v) <= msDe(d.nascimento)) return { ok: false, erro: ERROS.rg_expedicao };
      return { ok: true, patch: { rg_expedicao: v } };
    }
    case 'estado_civil': {
      const m = n.match(/^([1-7])$/);
      if (m) return { ok: true, patch: { estado_civil: ESTADOS_CIVIS[Number(m[1]) - 1][2], estado_civil_txt: ESTADOS_CIVIS[Number(m[1]) - 1][1] } };
      const porNome = [['solteir', 0], ['casad', 1], ['uniao', 2], ['divorciad', 3], ['separad', 4], ['viuv', 5]].find(([k]) => n.includes(k));
      if (porNome) return { ok: true, patch: { estado_civil: ESTADOS_CIVIS[porNome[1]][2], estado_civil_txt: ESTADOS_CIVIS[porNome[1]][1] } };
      return { ok: false, erro: ERROS.estado_civil };
    }
    case 'cep': { const v = dig(t); return v.length === 8 ? { ok: true, patch: { cep: v } } : { ok: false, erro: ERROS.cep }; }
    case 'logradouro': return t.length >= 3 ? { ok: true, patch: { logradouro: t.slice(0, 120).toUpperCase() } } : { ok: false, erro: ERROS.logradouro };
    case 'bairro': return t.length >= 2 ? { ok: true, patch: { bairro: t.slice(0, 80).toUpperCase() } } : { ok: false, erro: ERROS.bairro };
    case 'cidade_uf': { const v = lerCidadeUf(t); return v ? { ok: true, patch: { cidade: v.cidade.toUpperCase(), uf: v.uf, cidade_id: null } } : { ok: false, erro: ERROS.cidade_uf }; }
    case 'numero': {
      if (/^(s\s*\/?\s*n|sem numero|sem número)$/i.test(t)) return { ok: true, patch: { numero: 'S/N', complemento: null } };
      if (/^\d{1,6}$/.test(t)) return { ok: true, patch: { numero: t, complemento: null } };
      const mc = t.match(/^(\d{1,6})\s*([a-zA-Z](?![a-zA-Z]))?\s*[,\-/]?\s*(.{0,40})$/);
      if (mc) {
        const comp = [mc[2], mc[3]].filter(Boolean).join(' ').trim();
        if (comp && /^\d+$/.test(comp)) return { ok: false, erro: ERROS.numero };
        return { ok: true, patch: { numero: mc[1], complemento: comp ? comp.toUpperCase().slice(0, 40) : null } };
      }
      return { ok: false, erro: ERROS.numero };
    }
    case 'nome_pai': {
      if (/^(nao consta|nao tem|nao possui|nao declarado|sem pai|nao|desconhecido|nao sei)$/.test(n)) return { ok: true, patch: { nome_pai: 'NAO CONSTA' } };
      const v = lerNomeCompleto(t); return v ? { ok: true, patch: { nome_pai: v } } : { ok: false, erro: ERROS.nome_pai };
    }
    case 'patrimonio': { const m = n.match(/^([1-5])$/); return m ? { ok: true, patch: { patrimonio: m[1] } } : { ok: false, erro: ERROS.patrimonio }; }
    case 'agencia': { const v = dig(t); return v.length >= 1 && v.length <= 5 && /^\d[\d\s-]*$/.test(t.replace(/-\d$/, '')) ? { ok: true, patch: { agencia: v.slice(0, 5) } } : { ok: false, erro: ERROS.agencia }; }
    case 'conta': { const v = t.replace(/[^0-9xX-]/g, '').toUpperCase(); return /^\d{3,13}-?[\dX]$/.test(v) ? { ok: true, patch: { conta: v.includes('-') ? v : `${v.slice(0, -1)}-${v.slice(-1)}` } } : { ok: false, erro: ERROS.conta }; }
    case 'tipo_conta':
      if (/^(c|corrente|conta corrente|cc)$/.test(n)) return { ok: true, patch: { tipo_conta: 'C' } };
      if (/^(p|poupanca|conta poupanca)$/.test(n)) return { ok: true, patch: { tipo_conta: 'P' } };
      return { ok: false, erro: ERROS.tipo_conta };
    case 'pix': return lerPixFacta(t, d.cpf);
    case 'email':
      if (/^(pular|nao tenho|nao|sem email|sem e mail|nenhum)$/.test(n)) return { ok: true, patch: { email: '-' } };
      return /^[\w.+-]+@[\w-]+(\.[\w-]+)+$/.test(t) ? { ok: true, patch: { email: t.toLowerCase() } } : { ok: false, erro: ERROS.email };
    default: return { ok: false, erro: '' };
  }
}

/** PIX: tipo 1 CPF (só o do próprio cliente), 2 telefone, 3 e-mail, 4 aleatória (combo tipo-chave-pix). */
export function lerPixFacta(texto, cpfTitular) {
  const s = String(texto ?? '').trim();
  if (ehSim(s) || /^(cpf|meu cpf|e o cpf|o cpf|e meu cpf)$/.test(normalizar(s))) return { ok: true, patch: { pix_tipo: '1', pix: 'CPF' } };
  if (/^[\w.+-]+@[\w-]+(\.[\w-]+)+$/.test(s)) return { ok: true, patch: { pix_tipo: '3', pix: s.toLowerCase() } };
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s)) return { ok: true, patch: { pix_tipo: '4', pix: s.toLowerCase() } };
  if (!/^[\d\s().+\-/]+$/.test(s)) return { ok: false, erro: ERROS.pix };
  let d = dig(s);
  if (d.length === 13 && d.startsWith('55')) d = d.slice(2);
  if (d.length !== 11) return { ok: false, erro: ERROS.pix };
  if (d === dig(cpfTitular)) return { ok: true, patch: { pix_tipo: '1', pix: 'CPF' } };
  if (/^\d{3}\.\d{3}\.\d{3}-\d{2}$/.test(s) || (cpfValido(d) && d[2] !== '9')) return { ok: false, erro: ERROS.pix_cpf_outro };
  if (d[2] === '9') return { ok: true, patch: { pix_tipo: '2', pix: d } };
  return { ok: false, erro: ERROS.pix };
}

export function pergunta(campo) { return PERGUNTAS[campo] ?? ''; }

export function textoResumo(d, oferta) {
  const num_ = d.numero === 'S/N' ? 'S/N' : d.numero;
  const end = `${d.logradouro}, ${num_}${d.complemento ? `, ${d.complemento}` : ''} - ${d.bairro}, ${d.cidade}/${d.uf}, CEP ${String(d.cep).replace(/(\d{5})(\d{3})/, '$1-$2')}`;
  const pix = d.pix_tipo === '1' ? 'o seu CPF' : d.pix_tipo === '2' ? formatarCelular(d.pix) : d.pix;
  return [
    '📋 Confira os seus dados:',
    '',
    `Nome: ${d.nome_oficial || d.nome}`,
    `RG: ${d.rg} ${d.rg_orgao}/${d.rg_uf}`,
    `Estado civil: ${d.estado_civil_txt ?? ''}`,
    `Naturalidade: ${d.natural_cidade}/${d.natural_uf}`,
    `Endereço: ${end}`,
    `Chave PIX: ${pix}`,
    ...(d.email && d.email !== '-' ? [`E-mail: ${d.email}`] : []),
    ...(oferta ? ['', `💰 Parcela de *${reais(oferta.parcela)}* em ${oferta.prazo}x — você recebe *${reais(oferta.liquido)}*`] : []),
    '',
    'Ao responder SIM, você autoriza a Cred Finance a enviar estes dados à Facta Financeira para cadastrar a proposta do consignado CLT. A contratação só acontece quando você assinar o contrato pelo link da Facta.',
    '',
    'Está tudo certo?\nResponda *SIM* para enviar ou *CORRIGIR* para refazer os dados.',
  ].join('\n');
}

/** Campos da etapa 2 (dados pessoais). Banco/agência/conta vazios quando o pagamento é por PIX (testar no 1º caso real). */
export function montarEtapa2(d, idSimulador) {
  const numero = d.numero === 'S/N' || !/^\d+$/.test(String(d.numero)) ? '0' : String(d.numero);
  const p = {
    id_simulador: idSimulador, cpf: dig(d.cpf), nome: d.nome_oficial || d.nome, sexo: d.sexo, estado_civil: d.estado_civil,
    data_nascimento: d.nascimento, rg: d.rg, estado_rg: d.rg_uf, orgao_emissor: d.rg_orgao, data_expedicao: d.rg_expedicao,
    estado_natural: d.natural_uf, cidade_natural: d.natural_cidade_id, nacionalidade: '1',
    celular: formatarCelular(d.celular), renda: String(Math.round(num(d.renda))),
    cep: d.cep, endereco: d.logradouro, numero, complemento: d.complemento || '', bairro: d.bairro, cidade: d.cidade_id, estado: d.uf,
    nome_mae: d.nome_mae, nome_pai: d.nome_pai, valor_patrimonio: d.patrimonio, cliente_iletrado_impossibilitado: 'N',
    tipo_conta: d.tipo_conta || '', banco: d.banco_id || '', agencia: d.agencia || '', conta: d.conta || '',
    matricula: d.matricula, cnpj_empregador: d.cnpj_empregador, data_admissao: d.data_admissao,
    tipo_chave_pix: d.pix_tipo, chave_pix: d.pix_tipo === '1' ? dig(d.cpf) : d.pix,
  };
  if (d.email && d.email !== '-') p.email = d.email;
  return p;
}

// ---------------------------------------------------------------------
// Fluxo (estado na tabela facta_operacoes; dependências injetadas)
// deps: { api, db:{criar(op), atualizar(id, patch)}, combos?, cep?, agora?, cfg }
// Resultado: { textos:[], equipe?:motivo, aviso?:texto p/ Telegram, estado?:ai_state }
// ---------------------------------------------------------------------
export function produtoFacta(produtoInteresse, cfg = {}) {
  const p = String(produtoInteresse ?? '');
  const produtos = (cfg.produtos ?? ['CLT']).map((x) => String(x).toUpperCase());
  if (produtos.includes('CLT') && /\bclt\b|carteira assinada|trabalhador|consignado privado|privado|empresa privada/i.test(p)) return 'CLT';
  return null;
}

/** (v21.1) O cliente falou de consignado CLT / carteira assinada na mensagem? (para o código assumir sem IA) */
export function pedeClt(texto) {
  const n = normalizar(texto);
  if (/\bnao\b.{0,25}\b(carteira|clt)\b/.test(n)) return false;
  return /\bclt\b|carteira assinada|consignado (privado|do trabalhador|para quem tem carteira)|credito do trabalhador|emprestimo (do trabalhador|para quem trabalha de carteira)/.test(n);
}

export function esperaCliente(op) {
  return Boolean(op) && (ESPERA_CLIENTE.includes(op.etapa) || ESPERA_SISTEMA.includes(op.etapa));
}

const ESTADO_FGTS = { produto_interesse: 'Antecipação do saque-aniversário FGTS', aguardando_cpf: true };

export function criarFluxoFacta({ api, db, combos = null, cep = buscarCep, agora = () => Date.now(), cfg = {} }) {
  const cb = combos ?? criarCombos(api);
  const quando = (ms) => new Date(agora() + ms).toISOString();
  const idade = (iso) => agora() - (Date.parse(String(iso ?? '')) || agora());
  const maxTent = Math.max(2, Number(cfg.max_tentativas) || 2);
  async function mudar(op, patch) {
    // o telefone do WhatsApp (_wa) é só de trabalho: nunca vai para o banco
    if (patch.dados_cliente && typeof patch.dados_cliente === 'object') {
      const { _wa, ...resto } = patch.dados_cliente;
      patch = { ...patch, dados_cliente: resto };
    }
    Object.assign(op, patch);
    await db.atualizar(op.id, patch);
  }
  const res = (textos = [], extra = {}) => ({ textos: textos.filter(Boolean), ...extra });
  const est = (etapa, extra = {}) => ({ estado: { facta_etapa: etapa, ...extra } });
  const paraEquipe = async (op, motivo, texto = TXT.passarEquipe) => {
    await mudar(op, { etapa: 'equipe', dados_cliente: null, proxima_consulta: null, campo_atual: null, encerrada_em: quando(0) });
    return res([texto], { equipe: motivo, ...est('equipe') });
  };
  const encerrar = async (op, textos, extra = {}) => {
    await mudar(op, { etapa: 'encerrada', dados_cliente: null, proxima_consulta: null, campo_atual: null, encerrada_em: quando(0) });
    return res(textos, { ...est('encerrada'), ...extra });
  };
  const fimTel = (d) => String(d.celular ?? '').slice(-4);
  const nomeCurto = (ctx, d) => primeiroNome(d?.nome || d?.nome_oficial || ctx?.contato?.nome || '');
  const falhou = async (op, msg, motivo) => {
    const n = Number(op.tentativas ?? 0) + 1;
    if (n >= maxTent) return paraEquipe(op, `Facta CLT: cliente não conseguiu responder (${motivo || op.etapa}${op.campo_atual ? ' / ' + op.campo_atual : ''})`);
    await mudar(op, { tentativas: n });
    return res([msg]);
  };

  /** Começa a conversa do CLT (a IA identificou o produto, ou o CPF chegou com o produto CLT).
   *  @param {{ ctx: any, cpf?: string | null, direto?: boolean }} p */
  async function iniciar({ ctx, cpf = null, direto = false }) {
    const d = {};
    if (cpf && cpfValido(cpf)) d.cpf = dig(cpf);
    const op = await db.criar({
      tenant_id: ctx.empresa.id, conversation_id: ctx.conversa.id, produto: 'CLT', etapa: direto ? 'clt_dados' : 'clt_inicio',
      dados_cliente: d, tentativas: 0, erros: 0, proxima_consulta: null,
    });
    // (v21.4) O cliente já disse que é CLT (menu, anúncio ou mensagem): vai direto aos dados (CPF → nome)
    if (direto) {
      d._wa = ctx?.contato?.wa_id ?? d._wa;
      if (!d.cpf) {
        await mudar(op, { etapa: 'clt_dados', campo_atual: 'cpf', dados_cliente: d, tentativas: 0 });
        return { ok: true, op, ...res([`${TXT.inicioDireto}\n\n${TXT.cpfDireto}`], est('clt_dados')) };
      }
      const r = await proximoDado(op, d, [], ctx);
      if (r?.textos?.[0] === TXT.nome) r.textos[0] = `✅ CPF recebido!\n\n${TXT.nome}`;
      return { ok: true, op, ...r };
    }
    return { ok: true, op, ...res([d.cpf ? TXT.inicioComCpf : TXT.inicio], est('clt_inicio')) };
  }

  // ----- clt_dados: CPF → nome → celular (v21.4: CPF primeiro) -----
  /** (v21.2) nome → CPF → (celular = o próprio WhatsApp) → consulta e link da Facta direto, sem termo no WhatsApp. */
  async function proximoDado(op, d, antes = [], ctx = null) {
    if (!d.cpf) { await mudar(op, { etapa: 'clt_dados', campo_atual: 'cpf', dados_cliente: d, tentativas: 0 }); return res([...antes, TXT.cpf], est('clt_dados')); }
    if (!d.nome) { await mudar(op, { etapa: 'clt_dados', campo_atual: 'nome', dados_cliente: d, tentativas: 0 }); return res([...antes, TXT.nome], est('clt_dados')); }
    if (!d.celular) {
      const doWa = celularDoWa(d._wa);
      if (doWa && d.celular_wa_ok !== false) d.celular = doWa;
      else { await mudar(op, { etapa: 'clt_dados', campo_atual: 'celular', dados_cliente: d, tentativas: 0 }); return res([...antes, TXT.celular], est('clt_dados')); }
    }
    await mudar(op, { etapa: 'clt_autorizacao', campo_atual: null, dados_cliente: d, tentativas: 0, aceite_versao: 'link-facta' });
    const r = await consultarMargem(ctx, op, d, antes);
    if (r) return r;
    await mudar(op, { proxima_consulta: quando(2 * MIN) });
    return res([...antes, TXT.aguardeConferindo], est('clt_autorizacao'));
  }

  // ----- autorização (Dataprev pela Facta) -----
  /** Pede o link de autorização à Facta. reenvio = o cliente pediu de novo (erro no link, não chegou, SMS, outro número).
   *  Limite: 4 envios por operação → equipe. */
  async function pedirLink(ctx, op, d, tipo = 'WHATSAPP', { reenvio = false } = {}) {
    const envios = Number(d.envios ?? 0) + 1;
    if (envios > 4) return paraEquipe(op, 'Facta CLT: 4 links de autorização enviados e a autorização não deu certo', TXT.limiteEnvios);
    const s = lerSolicitacao(await api.solicitarAutorizacao({ nome: d.nome_oficial || d.nome, cpf: d.cpf, celular: d.celular, tipo_envio: tipo }));
    if (s.tipo === 'valida') {
      await mudar(op, { autorizacao_pedida_em: op.autorizacao_pedida_em ?? quando(0) });
      return (await consultarMargem(ctx, op, d, [], { jaPediu: true })) ??
        (await mudar(op, { etapa: 'clt_autorizacao', dados_cliente: d, proxima_consulta: quando(2 * MIN) }), res([TXT.aguardeConferindo], est('clt_autorizacao')));
    }
    if (s.tipo === 'telefone_outro_cpf') {
      const tent = Number(d.tent_celular ?? 0) + 1;
      if (tent > 2) return paraEquipe(op, 'Facta CLT: celular do cliente ligado a outro CPF na Facta (2 números tentados)');
      d.tent_celular = tent;
      d.forcado_outro = true;
      await mudar(op, { etapa: 'clt_autorizacao', campo_atual: 'celular_outro', dados_cliente: d, proxima_consulta: null, tentativas: 0 });
      return res([TXT.telefoneOutroCpf], est('clt_autorizacao'));
    }
    if (s.tipo === 'erro') {
      const msg = s.mensagem.replace(/\d{6,}/g, '***').slice(0, 120);
      if (!reenvio) return paraEquipe(op, `Facta CLT: pedido de autorização recusado (${msg})`);
      const falhas = Number(d.falhas_envio ?? 0) + 1;
      d.falhas_envio = falhas;
      if (falhas >= 2) return paraEquipe(op, `Facta CLT: a Facta recusou o reenvio do link 2 vezes (${msg})`);
      await mudar(op, { etapa: 'clt_autorizacao', campo_atual: null, dados_cliente: d });
      return res([`${TXT.falhaEnvio}\n\n${TXT.problemas}`], { ...est('clt_autorizacao'), aviso: `Facta CLT: reenvio do link recusado (${msg})` });
    }
    d.envios = envios;
    d.canal = tipo;
    await mudar(op, {
      etapa: 'clt_autorizacao', campo_atual: null, dados_cliente: d, tentativas: 0,
      autorizacao_pedida_em: quando(0), lembrete_em: null, proxima_consulta: quando(2 * MIN),
    });
    const texto = reenvio ? TXT.reenviado(tipo, fimTel(d)) : (tipo === 'SMS' ? TXT.linkAutorizacaoSms(fimTel(d)) : TXT.linkAutorizacao(fimTel(d), cfg.politica_url || ''));
    return res([texto], { ...est('clt_autorizacao'), aviso: `Facta CLT: link de autorização ${reenvio ? 'REENVIADO' : 'enviado'} ao cliente (${tipo === 'SMS' ? 'SMS' : 'WhatsApp'}, envio ${envios})` });
  }

  /** Consulta a Dataprev (autoriza-consulta). Com dados → margem → ofertas. Sem autorização → pede o link (uma vez). */
  async function consultarMargem(ctx, op, d, antes = [], { jaPediu = false, doAgendamento = false } = {}) {
    const a = lerAutorizacao(await api.autorizaConsulta(d.cpf));
    if (a.tipo === 'dados') return aposDados(ctx, op, d, a.vinculos, antes);
    if (a.tipo === 'precisa') {
      if (!jaPediu && !op.autorizacao_pedida_em) return pedirLink(ctx, op, d);
      return null; // ainda não autorizou
    }
    if (a.tipo === 'virada') {
      const primeira = !op.virada_em;
      await mudar(op, { etapa: 'clt_autorizacao', dados_cliente: d, virada_em: op.virada_em ?? quando(0), proxima_consulta: quando(30 * MIN) });
      if (idade(op.virada_em) > DIA) return paraEquipe(op, 'Facta CLT: consulta da Dataprev fora do ar (virada de folha) há mais de 1 dia');
      return res(primeira ? [...antes, TXT.virada] : [], { ...est('clt_autorizacao'), ...(primeira ? { aviso: 'Facta CLT: Dataprev em virada de folha; o robô volta a consultar sozinho' } : {}) });
    }
    if (a.tipo === 'cpf_invalido') return paraEquipe(op, 'Facta CLT: a Facta disse que o CPF não é válido');
    if (a.tipo === 'sem_vinculo') return semOpcao(op, TXT.semVinculo, 'sem vínculo CLT na Dataprev');
    if (a.tipo === 'falha') {
      const erros = Number(op.erros ?? 0) + 1;
      if (erros >= (doAgendamento ? 10 : 3)) return paraEquipe(op, `Facta CLT: consulta da margem sem resposta (${a.mensagem})`);
      await mudar(op, { etapa: 'clt_autorizacao', dados_cliente: d, erros, proxima_consulta: quando(MIN) });
      return doAgendamento ? res() : res([...antes, '⏳ A Facta está demorando para responder. Vou tentar de novo em instantes e te aviso aqui.'], est('clt_autorizacao'));
    }
    return paraEquipe(op, `Facta CLT: erro na consulta da margem (${a.mensagem.replace(/\d{6,}/g, '***').slice(0, 120)})`);
  }

  async function semOpcao(op, texto, motivo) {
    return encerrar(op, [texto + TXT.oferecerFgts], { estado: { facta_etapa: 'encerrada', ...ESTADO_FGTS }, aviso: `Facta CLT: ${motivo}; o robô ofereceu o FGTS` });
  }

  async function aposDados(ctx, op, d, vinculos, antes = []) {
    const { vinculo, motivo } = escolherVinculo(vinculos);
    if (!vinculo) {
      if (motivo === 'sem_margem') return semOpcao(op, TXT.semMargem, 'vínculo sem margem disponível');
      return semOpcao(op, TXT.semVinculo, motivo === 'inelegivel' ? 'vínculo não elegível' : 'sem vínculo CLT na Dataprev');
    }
    Object.assign(d, dadosDoVinculo(vinculo));
    if (!d.matricula || !d.data_admissao) return paraEquipe(op, 'Facta CLT: a Dataprev não devolveu matrícula ou data de admissão');
    await mudar(op, { erros: 0, proxima_consulta: null, autorizado_em: quando(0) });
    if (!d.nascimento) {
      await mudar(op, { etapa: 'clt_dados', campo_atual: 'nascimento', dados_cliente: d, tentativas: 0 });
      return res([...antes, TXT.nascimento], est('clt_dados'));
    }
    return mostrarOfertas(ctx, op, d, { opcao_valor: '2', valor_parcela: d.margem }, antes, { maximo: true });
  }

  // ----- ofertas -----
  async function buscarTabelas(d, modo, cfgResumo = cfg) {
    const p = { cpf: dig(d.cpf), data_nascimento: d.nascimento, valor_renda: dinheiroApi(d.renda), opcao_valor: modo.opcao_valor };
    if (modo.opcao_valor === '2') p.valor_parcela = dinheiroApi(modo.valor_parcela);
    else p.valor = dinheiroApi(modo.valor);
    const r = await api.operacoes(p);
    const lista = Array.isArray(r.dados?.tabelas) ? r.dados.tabelas : [];
    return { r, ofertas: resumirTabelas(lista, cfgResumo), mensagem: String(r.dados?.mensagem ?? '') };
  }

  async function mostrarOfertas(ctx, op, d, modo, antes = [], { prefixo = '', filtroPrazo = 0, maximo = false } = {}) {
    const { r, ofertas: todas, mensagem } = await buscarTabelas(d, modo);
    const ofertas = filtroPrazo > 0 ? todas.filter((o) => o.prazo <= filtroPrazo) : todas;
    if (!ofertas.length) {
      if (!r.dados) return paraEquipe(op, `Facta CLT: simulação sem resposta (${r.bloqueado ? 'bloqueado pela Cloudflare' : 'sem resposta'})`);
      return semOpcao(op, TXT.semOfertaFacta, `simulação sem tabelas (${mensagem.slice(0, 80) || 'vazia'})`);
    }
    if (maximo) d.max_liquido = Math.max(...ofertas.map((o) => o.liquido)); // limite máximo do cliente (simulação pela margem toda)
    await mudar(op, { etapa: 'clt_oferta', campo_atual: null, ofertas, oferta_idx: null, dados_cliente: d, tentativas: 0, proxima_consulta: null });
    return res([...antes, textoOfertas(ofertas, { nome: nomeCurto(ctx, d), prefixo })],
      { ...est('clt_oferta'), aviso: `Facta CLT: ${ofertas.length} opção(ões) apresentada(s) (maior valor líquido ${reais(Math.max(...ofertas.map((o) => o.liquido)))})` });
  }

  /** (v21.1) Retenção: 1 vez por operação, refaz a simulação pela margem toda SEM a ordem de comissão
   *  (tabela que paga mais ao cliente). Só mostra se alguma opção ficar melhor do que a já mostrada. */
  async function tentarRetencao(op, d) {
    if (d.retencao) return null;
    d.retencao = true;
    const { ofertas: melhores } = await buscarTabelas(d, { opcao_valor: '2', valor_parcela: d.margem }, { ...cfg, tabelas_preferidas: [] });
    const vistas = op.ofertas ?? [];
    const melhorou = melhores.some((m) => { const v = vistas.find((x) => x.prazo === m.prazo); return !v || m.liquido > v.liquido + 0.01; });
    if (!melhores.length || !melhorou) { await mudar(op, { dados_cliente: d }); return null; }
    d.max_liquido = Math.max(d.max_liquido || 0, ...melhores.map((o) => o.liquido));
    await mudar(op, { ofertas: melhores, oferta_idx: null, dados_cliente: d, tentativas: 0 });
    return res([textoOfertas(melhores, { prefixo: TXT.retencao })], { ...est('clt_oferta'), aviso: 'Facta CLT: cliente ia desistir; o robô ofereceu a tabela de maior valor ao cliente (comissão menor)' });
  }

  async function verificarPolitica(ctx, op, d, idx) {
    const o = (op.ofertas ?? [])[idx];
    const r = await api.politica({ cpf: dig(d.cpf), matricula: d.matricula, dataNascimento: d.nascimento, dataAdmissao: d.data_admissao, prazo: String(o.prazo), valorEmprestimo: dinheiroApi(o.contrato) });
    if (!r.dados) return paraEquipe(op, `Facta CLT: análise de crédito sem resposta (${r.bloqueado ? 'bloqueado pela Cloudflare' : 'sem resposta'})`);
    const aprovado = verdade(r.dados.aprovado);
    if (aprovado) {
      await mudar(op, { oferta_idx: idx, politica_em: quando(0) });
      return perguntarProximo(op, d, [TXT.inicioDados]);
    }
    const vmax = num(r.dados.valor_maximo_disponivel), pmax = num(r.dados.prazo_maximo_disponivel);
    if (vmax > 0 && !d.politica_reoferta) {
      d.politica_reoferta = true;
      return mostrarOfertas(ctx, op, d, { opcao_valor: '1', valor: vmax }, [], { prefixo: TXT.politicaReduzida(vmax), filtroPrazo: pmax > 0 ? pmax : 0, maximo: true });
    }
    return paraEquipe(op, `Facta CLT: análise de crédito não aprovou${r.dados.mensagem ? ` (${String(r.dados.mensagem).replace(/\d{6,}/g, '***').slice(0, 100)})` : ''}`);
  }

  // ----- coleta -----
  async function perguntarProximo(op, d, antes = []) {
    const campo = proximoCampo(d, cfg);
    if (!campo) {
      await mudar(op, { etapa: 'clt_confirmacao', campo_atual: null, dados_cliente: d, tentativas: 0 });
      return res([...antes, textoResumo(d, (op.ofertas ?? [])[op.oferta_idx ?? 0])], est('clt_confirmacao'));
    }
    await mudar(op, { etapa: 'clt_coleta', campo_atual: campo, dados_cliente: d, tentativas: 0 });
    return res([...antes, pergunta(campo)], est('clt_coleta'));
  }

  async function lerCampoComApoio(op, campo, texto, d) {
    if (campo === 'rg_orgao') {
      // aceita "SSP", "SSP/RJ", "ssp rj", "Detran-RJ": 1ª sigla = órgão; se vier a UF junto, já guarda
      const partes = String(texto ?? '').normalize('NFD').replace(/\p{M}/gu, '').toUpperCase().split(/[^A-Z]+/).filter(Boolean);
      const sigla = partes[0] ?? '';
      const lista = await cb.orgaos().catch(() => undefined);
      const validas = lista ? Object.keys(lista).map((k) => k.toUpperCase()) : ORGAOS_COMUNS;
      if (!sigla || sigla.length > 8 || !validas.includes(sigla)) return { ok: false, erro: ERROS.rg_orgao };
      const patch = { rg_orgao: sigla };
      if (partes[1] && UFS.includes(partes[1]) && !d.rg_uf) patch.rg_uf = partes[1];
      return { ok: true, patch };
    }
    if (campo === 'naturalidade') {
      const v = lerCidadeUf(texto);
      if (!v) return { ok: false, erro: ERROS.naturalidade };
      const id = await cb.cidade(v.cidade, v.uf);
      if (id === undefined) return { ok: false, erro: ERROS.naturalidade, sistema: true };
      if (!id) return { ok: false, erro: ERROS.naturalidade_nao_achada };
      return { ok: true, patch: { natural_cidade: v.cidade.toUpperCase(), natural_uf: v.uf, natural_cidade_id: String(id) } };
    }
    if (campo === 'banco') {
      const b = await cb.banco(texto);
      if (b === undefined) return { ok: false, erro: ERROS.banco, sistema: true };
      if (!b) return { ok: false, erro: ERROS.banco };
      return { ok: true, patch: { banco: b.nome, banco_id: String(b.id) } };
    }
    return lerCampo(campo, texto, d, agora());
  }

  /** Depois do endereço: id da cidade na Facta (combo). */
  async function completarCidade(d) {
    if (!d.cidade || !d.uf || d.cidade_id) return 'ok';
    const id = await cb.cidade(d.cidade, d.uf);
    if (id === undefined) return 'sistema';
    if (!id) { d.cidade = null; d.uf = null; return 'nao_achada'; }
    d.cidade_id = String(id);
    return 'ok';
  }

  // ----- proposta (etapas 1, 2 e 3) -----
  async function cadastrarProposta(ctx, op) {
    const d = op.dados_cliente ?? {};
    const o = (op.ofertas ?? [])[op.oferta_idx ?? 0];
    if (!o) return paraEquipe(op, 'Facta CLT: oferta escolhida não encontrada');
    if (!cfg.login_certificado) return paraEquipe(op, 'Facta CLT: falta login_certificado na configuração agent_config.facta');
    await mudar(op, { aceite_proposta_em: quando(0) });
    const e1 = await api.etapa1({
      cpf: dig(d.cpf), data_nascimento: d.nascimento, login_certificado: cfg.login_certificado, codigo_tabela: o.codigo_tabela,
      prazo: String(o.prazo), valor_operacao: dinheiroApi(o.contrato), valor_parcela: dinheiroApi(o.parcela), coeficiente: o.coeficiente,
      ...(cfg.vendedor ? { vendedor: cfg.vendedor } : {}), ...(cfg.codigo_master ? { codigo_master: cfg.codigo_master } : {}), ...(cfg.gerente_comercial ? { gerente_comercial: cfg.gerente_comercial } : {}),
    });
    const idSim = e1.dados?.id_simulador;
    if (!e1.dados || e1.dados.erro === true || !idSim) return paraEquipe(op, `Facta CLT: simulação (etapa 1) recusada — ${limparMsg(e1)}`);
    await mudar(op, { id_simulador: String(idSim) });
    const e2 = await api.etapa2(montarEtapa2(d, String(idSim)));
    const codCli = e2.dados?.codigo_cliente;
    if (!e2.dados || e2.dados.erro === true || !codCli) {
      const m = limparMsg(e2);
      const dica = /banco|agencia|agência|conta/i.test(m) && cfg.pedir_conta_bancaria !== true ? ' — a Facta pediu conta bancária: ligar facta.pedir_conta_bancaria = true' : '';
      return paraEquipe(op, `Facta CLT: dados pessoais (etapa 2) recusados — ${m}${dica}`);
    }
    await mudar(op, { codigo_cliente: String(codCli) });
    const e3 = await api.etapa3({ codigo_cliente: String(codCli), id_simulador: String(idSim), tipo_formalizacao: 'DIG' });
    const af = e3.dados?.codigo;
    if (!e3.dados || e3.dados.erro === true || !af) return paraEquipe(op, `Facta CLT: cadastro da proposta (etapa 3) recusado — ${limparMsg(e3)}`);
    let url = String(e3.dados.url_formalizacao ?? '').trim();
    if (!/^https?:\/\//i.test(url)) url = '';
    if (!url) await api.envioLink(String(af), 'whatsapp').catch(() => null);
    // Proposta cadastrada: os dados pessoais saem do nosso banco (ficam só com a Facta).
    await mudar(op, {
      etapa: 'clt_assinatura', campo_atual: null, dados_cliente: null, codigo_af: String(af), url_formalizacao: url || null,
      proposta_em: quando(0), link_enviado_em: quando(0), lembrete_em: null, proxima_consulta: quando(2 * MIN), tentativas: 0, erros: 0,
    });
    return res([url ? TXT.link(url) : TXT.linkPorWhatsappFacta], { ...est('clt_assinatura'), aviso: `Facta CLT: proposta CADASTRADA (AF ${String(af).slice(-4).padStart(4, '*')}; cliente recebe ${reais(o.liquido)})` });
  }
  const limparMsg = (r) => (r?.dados ? String(r.dados.mensagem ?? 'sem mensagem') : (r?.bloqueado ? 'bloqueado pela Cloudflare' : 'sem resposta'))
    .replace(/\d{6,}/g, '***').replace(/[\w.+-]+@[\w-]+(\.[\w-]+)+/g, '***').slice(0, 140);

  // ----- acompanhamento (agendamento facta-acompanhar) -----
  async function acompanhar({ ctx, op }) {
    if (ETAPAS_FINAIS.includes(op.etapa) || ESPERA_CLIENTE.includes(op.etapa)) return res();
    if (op.etapa === 'clt_autorizacao') {
      const d = { ...(op.dados_cliente ?? {}) };
      if (!d.cpf) return paraEquipe(op, 'Facta CLT: operação sem CPF para consultar');
      if (op.campo_atual === 'celular_outro') { await mudar(op, { proxima_consulta: null }); return res(); }
      // se a 1ª consulta falhou (rede/virada) antes de pedir o link, o agendamento pede o link agora
      const r = await consultarMargem(ctx, op, d, [], { jaPediu: Boolean(op.autorizacao_pedida_em), doAgendamento: true });
      if (r) return r;
      // ainda sem autorização: a cada 2 min por 30 min, lembrete único, depois a cada 30 min; 24 h → equipe
      const t = idade(op.autorizacao_pedida_em);
      if (t > DIA) return paraEquipe(op, 'Facta CLT: cliente não autorizou a consulta em 24 horas');
      if (t > 30 * MIN && !op.lembrete_em) {
        await mudar(op, { lembrete_em: quando(0), proxima_consulta: quando(30 * MIN) });
        return res([TXT.lembreteAutorizacao], est('clt_autorizacao'));
      }
      await mudar(op, { proxima_consulta: quando(t > 30 * MIN ? 30 * MIN : 2 * MIN) });
      return res();
    }
    if (op.etapa === 'clt_assinatura' || op.etapa === 'clt_assinado') {
      const r = await api.andamento(op.codigo_af);
      const lista = Array.isArray(r.dados?.propostas) ? r.dados.propostas : [];
      const p = lista.find((x) => String(x?.codigo_af ?? '') === String(op.codigo_af)) ?? (lista.length === 1 ? lista[0] : null);
      if (!p) {
        const erros = Number(op.erros ?? 0) + 1;
        await mudar(op, { erros, proxima_consulta: quando(erros >= 20 ? HORA : 5 * MIN) });
        return erros === 20 ? res([], { aviso: 'Facta CLT: não consigo ler o andamento da proposta há 20 tentativas (conferir no portal; o cliente não foi avisado)' }) : res();
      }
      const ev = lerStatusProposta(p);
      const base = { erros: 0, status_proposta: String(p.status_proposta ?? '').slice(0, 120) };
      const valor = (op.ofertas ?? [])[op.oferta_idx ?? 0]?.liquido;
      if (ev === 'cancelada') { await mudar(op, base); return paraEquipe(op, `Facta CLT: proposta CANCELADA na Facta (${base.status_proposta})`, TXT.cancelada); }
      if (ev === 'pago') {
        await mudar(op, { ...base, etapa: 'concluida', pago_em: quando(0), proxima_consulta: null, encerrada_em: quando(0) });
        return res([TXT.pago(valor ? reais(valor) : '')], { estado: { facta_etapa: 'concluida', operacao_concluida: true }, aviso: 'Facta CLT: pagamento ENVIADO ao cliente ✅' });
      }
      if (ev === 'assinado' && op.etapa === 'clt_assinatura') {
        await mudar(op, { ...base, etapa: 'clt_assinado', assinado_em: quando(0), proxima_consulta: quando(30 * MIN) });
        return res([TXT.assinado], { ...est('clt_assinado'), aviso: 'Facta CLT: cliente ASSINOU o contrato ✍️' });
      }
      if (op.etapa === 'clt_assinado') {
        if (idade(op.assinado_em) > 10 * DIA) { await mudar(op, { ...base, etapa: 'concluida', proxima_consulta: null, encerrada_em: quando(0) }); return res([], { aviso: 'Facta CLT: 10 dias após a assinatura sem pagamento registrado (conferir no portal)' }); }
        await mudar(op, { ...base, proxima_consulta: quando(30 * MIN) });
        return res();
      }
      // esperando assinatura: lembrete único em 3 h; equipe em 48 h
      if (idade(op.link_enviado_em) > 48 * HORA) { await mudar(op, base); return paraEquipe(op, 'Facta CLT: cliente não assinou o contrato em 48 horas'); }
      if (!op.lembrete_em && idade(op.link_enviado_em) > 3 * HORA) {
        await mudar(op, { ...base, lembrete_em: quando(0), proxima_consulta: quando(10 * MIN) });
        return res([TXT.lembreteLink(op.url_formalizacao)], est('clt_assinatura'));
      }
      await mudar(op, { ...base, proxima_consulta: quando(idade(op.link_enviado_em) > HORA ? 10 * MIN : 2 * MIN) });
      return res();
    }
    return res();
  }

  /** Mensagem do cliente com operação esperando algo dele. null = deixar o fluxo normal responder. */
  async function tratarMensagem({ ctx, op, texto }) {
    if (!esperaCliente(op)) return null;
    const d = { ...(op.dados_cliente ?? {}) };
    d._wa = ctx?.contato?.wa_id ?? d._wa;
    const antesDaProposta = !['clt_assinatura', 'clt_assinado'].includes(op.etapa);
    if (ehDesistir(texto) && antesDaProposta) {
      if (op.etapa === 'clt_oferta' && d.margem) { const ret = await tentarRetencao(op, d); if (ret) return ret; }
      return encerrar(op, [TXT.encerrado], { aviso: 'Facta CLT: cliente desistiu' });
    }

    if (op.etapa === 'clt_inicio') {
      if (ehSim(texto) || (/\b(tenho carteira|carteira assinada|registrad[oa]|clt|trabalho)\b/.test(normalizar(texto)) && !/\bnao\b/.test(normalizar(texto)))) {
        if (!d.cpf) { const c = extrairCpf(texto); if (c) d.cpf = c; }
        return proximoDado(op, d, [], ctx);
      }
      if (ehNao(texto) || /\b(autonom|mei|aposentad|servidor|desempregad|informal)\w*/.test(normalizar(texto))) {
        return encerrar(op, [TXT.semCarteira], { estado: { facta_etapa: 'encerrada', ...ESTADO_FGTS }, aviso: 'Facta CLT: cliente sem carteira assinada; o robô ofereceu o FGTS' });
      }
      return falhou(op, TXT.naoEntendiInicio);
    }

    if (op.etapa === 'clt_dados') {
      const campo = op.campo_atual || 'nome';
      if (campo === 'nome') {
        const v = lerNomeCompleto(texto);
        if (!v) return falhou(op, `${ERROS.nome}\n\n${TXT.nome}`);
        d.nome = v;
        return proximoDado(op, d, [], ctx);
      }
      if (campo === 'cpf') {
        const c = extrairCpf(texto);
        if (!c) return falhou(op, `${ERROS.cpf}\n\n${TXT.cpf}`, 'CPF inválido');
        d.cpf = c;
        return proximoDado(op, d, [], ctx);
      }
      if (campo === 'celular_conf') {
        const n = normalizar(texto);
        if (/^1$/.test(n) || ehSim(texto)) { d.celular = celularDoWa(d._wa); return proximoDado(op, d, [], ctx); }
        if (/^2$/.test(n) || ehNao(texto) || /outro/.test(n)) { d.celular_wa_ok = false; return proximoDado(op, d, [], ctx); }
        const cel = lerCelular(texto);
        if (cel) { d.celular = cel; return proximoDado(op, d, [], ctx); }
        return falhou(op, TXT.celularConf(String(celularDoWa(d._wa) ?? '').slice(-4)));
      }
      if (campo === 'celular') {
        const cel = lerCelular(texto);
        if (!cel) return falhou(op, `${ERROS.celular}\n\n${TXT.celular}`);
        d.celular = cel;
        return proximoDado(op, d, [], ctx);
      }
      if (campo === 'nascimento') {
        const v = lerData(texto, { minAnos: 18, maxAnos: 80, agora: agora() });
        if (!v) return falhou(op, `${ERROS.nascimento}\n\n${TXT.nascimento}`);
        d.nascimento = v;
        await mudar(op, { campo_atual: null, dados_cliente: d });
        return mostrarOfertas(ctx, op, d, { opcao_valor: '2', valor_parcela: d.margem }, [], { maximo: true });
      }
      return proximoDado(op, d, [], ctx);
    }

    if (op.etapa === 'clt_termo') {
      if (ehSim(texto) || /\b(autorizo|aceito|concordo)\b/.test(normalizar(texto))) {
        await mudar(op, { aceite_em: quando(0), aceite_versao: cfg.termo_versao || FACTA_TERMO_VERSAO });
        const r = await consultarMargem(ctx, op, d, [TXT.consultando]);
        return r ?? res([TXT.aindaNaoAutorizado], est('clt_autorizacao'));
      }
      if (ehNao(texto)) return encerrar(op, [TXT.semAceite], { aviso: 'Facta CLT: cliente não autorizou a consulta' });
      return falhou(op, TXT.naoEntendiTermo);
    }

    if (op.etapa === 'clt_autorizacao') {
      // (v21.2) Conversa para resolver problemas na autorização. Nunca repete a mesma resposta: reenvia,
      // troca de canal (WhatsApp/SMS) ou de número, confere sozinho, ou oferece a equipe.
      const n = normalizar(texto);
      const opcao = (n.match(/^([1-6])$/) ?? [])[1] ?? '';
      if (op.campo_atual === 'celular_outro') {
        if (ehNao(texto)) {
          if (d.forcado_outro) return paraEquipe(op, 'Facta CLT: celular ligado a outro CPF e o cliente não tem outro número');
          await mudar(op, { campo_atual: null });
          return res([TXT.problemas], est('clt_autorizacao'));
        }
        const cel = lerCelular(texto);
        if (!cel) return falhou(op, `${ERROS.celular}\n\n${TXT.pedirOutroNumero}`);
        d.celular = cel;
        return pedirLink(ctx, op, d, 'WHATSAPP', { reenvio: Boolean(op.autorizacao_pedida_em) });
      }
      const celNovo = lerCelular(texto);
      if (celNovo && celNovo !== d.celular && celNovo !== String(d.cpf ?? '').replace(/\D/g, '')) { d.celular = celNovo; return pedirLink(ctx, op, d, 'WHATSAPP', { reenvio: true }); }
      if (opcao === '6' || /\b(pessoa|atendente|humano|equipe|alguem|consultor|ligar|ligacao)\b/.test(n)) {
        return paraEquipe(op, 'Facta CLT: cliente pediu ajuda de uma pessoa na autorização');
      }
      if (opcao === '4' || /\boutro (numero|celular|telefone|whats\w*|zap)\b|\btrocar (o )?numero\b/.test(n)) {
        d.forcado_outro = false;
        await mudar(op, { campo_atual: 'celular_outro', dados_cliente: d });
        return res([TXT.pedirOutroNumero], est('clt_autorizacao'));
      }
      if (opcao === '3' || /\bsms\b|mensagem de texto|torpedo/.test(n)) return pedirLink(ctx, op, d, 'SMS', { reenvio: true });
      const erroLink = /\b(erro|errado|invalido|expirad\w*|falhou|falha|bug|travou|problema)\b|nao (abre|abriu|funciona|funcionou|carrega|carregou|deu certo|consegui|consigo)/.test(n);
      if (opcao === '1' || erroLink) {
        const errosLink = Number(d.erros_link ?? 0) + 1;
        d.erros_link = errosLink;
        if (errosLink > 2) {
          await mudar(op, { dados_cliente: d });
          return res([`${TXT.erroPersistente} Pode ser instabilidade no sistema da Facta.\n\n${TXT.problemas}`], est('clt_autorizacao'));
        }
        return pedirLink(ctx, op, d, d.canal || 'WHATSAPP', { reenvio: true });
      }
      const naoChegou = /nao (chegou|recebi|veio|apareceu|achei|encontrei|encontro|acho|vi)|\bcade\b|onde (esta|ta) (o )?link/.test(n);
      if (opcao === '2' || naoChegou) {
        const vezes = Number(d.nao_chegou ?? 0) + 1;
        d.nao_chegou = vezes;
        // WhatsApp não chegou de novo → tenta SMS; SMS não chegou → volta para WhatsApp
        const canal = vezes >= 2 ? (d.canal === 'SMS' ? 'WHATSAPP' : 'SMS') : (d.canal || 'WHATSAPP');
        return pedirLink(ctx, op, d, canal, { reenvio: true });
      }
      const pedeReenvio = /\b(whats|whatsapp|whatsap|zap|zapzap|wpp)\b/.test(n)
        || (/\b(manda|mande|envia|envie|reenvia|reenvie|reenviar|mandar|enviar)\b/.test(n) && /\b(de novo|novamente|outra vez|outro link|link novo|novo link|link)\b/.test(n))
        || /^(de novo|novamente|outro link|link novo|manda de novo)$/.test(n);
      if (pedeReenvio) {
        const canal = /\b(whats|whatsapp|whatsap|zap|zapzap|wpp)\b/.test(n) ? 'WHATSAPP' : (d.canal || 'WHATSAPP');
        return pedirLink(ctx, op, d, canal, { reenvio: true });
      }
      const feito = opcao === '5' || (/\b(ok|okay|pronto|feito|fiz|autorizei|autorizado|conclui|liberei|aceitei|confirmei|foi|ja)\b/.test(n) && !/\b(nao|nem)\b/.test(n));
      const r = await consultarMargem(ctx, op, d, [], { jaPediu: Boolean(op.autorizacao_pedida_em) });
      if (r) return r;
      if (feito) {
        const vezes = Number(d.ok_sem_autorizar ?? 0) + 1;
        d.ok_sem_autorizar = vezes;
        await mudar(op, { dados_cliente: d, proxima_consulta: quando(MIN) });
        return res([vezes === 1 ? TXT.aguardeConferindo : `${TXT.aindaNaoApareceu}\n\n${TXT.problemas}`], est('clt_autorizacao'));
      }
      if (String(texto).includes('?') || /\b(como|onde|ajuda|duvida|explica|nao entendi)\b/.test(n)) {
        await mudar(op, { proxima_consulta: quando(2 * MIN) });
        return res([`${TXT.comoAutorizar}\n\n${TXT.problemas}`], est('clt_autorizacao'));
      }
      const naoEntendi = Number(d.nao_entendi ?? 0) + 1;
      d.nao_entendi = naoEntendi;
      if (naoEntendi >= 4) return paraEquipe(op, 'Facta CLT: cliente não conseguiu seguir na autorização');
      await mudar(op, { dados_cliente: d, proxima_consulta: quando(2 * MIN) });
      return res([TXT.problemas], est('clt_autorizacao'));
    }

    if (op.etapa === 'clt_oferta') {
      const ofertas = op.ofertas ?? [];
      const resp = lerRespostaOferta(texto, ofertas.length);
      if (resp && typeof resp === 'object' && 'escolha' in resp) {
        const r = await verificarPolitica(ctx, op, d, resp.escolha);
        return { ...r, textos: [TXT.verificando, ...r.textos] };
      }
      if (resp && typeof resp === 'object' && 'parcela' in resp) {
        if (resp.parcela > d.margem + 0.001) return falhou(op, TXT.parcelaAcimaMargem(d.margem));
        const { ofertas: novas } = await buscarTabelas(d, { opcao_valor: '2', valor_parcela: resp.parcela });
        if (!novas.length) return res([TXT.semOpcaoPedido, textoOfertas(ofertas, { prefixo: ' ' }).trim()], est('clt_oferta'));
        await mudar(op, { ofertas: novas, oferta_idx: null, tentativas: 0 });
        return res([textoOfertas(novas, { prefixo: `Com parcela de até ${reais(resp.parcela)}:` })], est('clt_oferta'));
      }
      if (resp && typeof resp === 'object' && 'valor' in resp && d.max_liquido && resp.valor > d.max_liquido + 0.5) {
        return res([TXT.acimaDoMaximo(d.max_liquido), textoOfertas(ofertas, { prefixo: ' ' }).trim()], est('clt_oferta'));
      }
      if (resp && typeof resp === 'object' && 'valor' in resp) {
        const { ofertas: novas } = await buscarTabelas(d, { opcao_valor: '1', valor: resp.valor });
        const dentro = novas.filter((o) => o.parcela <= d.margem + 0.001);
        if (!dentro.length) return res([TXT.semOpcaoPedido, textoOfertas(ofertas, { prefixo: ' ' }).trim()], est('clt_oferta'));
        await mudar(op, { ofertas: dentro, oferta_idx: null, tentativas: 0 });
        return res([textoOfertas(dentro, { prefixo: `Para um empréstimo de ${reais(resp.valor)}:` })], est('clt_oferta'));
      }
      if (/\b(pouco|pouquinho|baixo|pequeno|mixaria|nao compensa|nao vale|so isso|queria mais|quero mais|mais dinheiro)\b/.test(normalizar(texto))) {
        const ret = await tentarRetencao(op, d);
        if (ret) return ret;
        return res([TXT.jaEhMaximo(d.max_liquido || Math.max(...ofertas.map((o) => o.liquido))), textoOfertas(ofertas, { prefixo: ' ' }).trim()], est('clt_oferta'));
      }
      return falhou(op, TXT.naoEntendiOferta(ofertas.length));
    }

    if (op.etapa === 'clt_coleta') {
      const campo = op.campo_atual || proximoCampo(d, cfg);
      if (AJUDA[campo] && ehNaoSei(texto)) return res([`${AJUDA[campo]}\n\n${pergunta(campo)}`], est('clt_coleta'));
      const lido = await lerCampoComApoio(op, campo, texto, d);
      if (!lido.ok) {
        if (lido.sistema) return paraEquipe(op, `Facta CLT: lista da Facta (${campo}) fora do ar`);
        return falhou(op, `${lido.erro}\n\n${pergunta(campo)}`);
      }
      Object.assign(d, lido.patch);
      if (campo === 'cep') {
        try {
          const e = await cep(d.cep);
          if (!e) { delete d.cep; await mudar(op, { dados_cliente: d }); return falhou(op, `${ERROS.cep_nao_achado}\n\n${PERGUNTAS.cep}`); }
          if (e.logradouro) d.logradouro = e.logradouro.toUpperCase();
          if (e.bairro) d.bairro = e.bairro.toUpperCase();
          if (e.cidade && e.uf) { d.cidade = e.cidade.toUpperCase(); d.uf = e.uf; d.cidade_id = null; }
        } catch { /* ViaCEP fora: pergunta rua, bairro e cidade */ }
      }
      if ((campo === 'cep' || campo === 'cidade_uf') && d.cidade && d.uf) {
        const c = await completarCidade(d);
        if (c === 'sistema') return paraEquipe(op, 'Facta CLT: lista de cidades da Facta fora do ar');
        if (c === 'nao_achada') { await mudar(op, { dados_cliente: d }); return falhou(op, `${ERROS.cidade_nao_achada}\n\n${PERGUNTAS.cidade_uf}`); }
      }
      return perguntarProximo(op, d);
    }

    if (op.etapa === 'clt_confirmacao') {
      if (/\bcorrig\w*\b/.test(normalizar(texto))) {
        const manter = ['cpf', 'nome', 'celular', 'nome_oficial', 'nascimento', 'sexo', 'nome_mae', 'matricula', 'cnpj_empregador', 'data_admissao', 'renda', 'margem', 'empregador', 'politica_reoferta'];
        const base = Object.fromEntries(Object.entries(d).filter(([k]) => manter.includes(k)));
        return perguntarProximo(op, base);
      }
      if (ehSim(texto) || /\b(confirmo|enviar|pode enviar|esta certo)\b/.test(normalizar(texto))) {
        op.dados_cliente = d;
        const r = await cadastrarProposta(ctx, op);
        return { ...r, textos: [TXT.enviando, ...r.textos] };
      }
      if (ehNao(texto)) return falhou(op, TXT.naoConfirmou);
      return falhou(op, TXT.confirmarResumo);
    }

    if (op.etapa === 'clt_assinatura') {
      const r = await acompanhar({ ctx, op });
      if (r.textos.length || r.equipe) return r;
      return res([TXT.aguardandoAssinatura(op.url_formalizacao)], est('clt_assinatura'));
    }
    if (op.etapa === 'clt_assinado') {
      const r = await acompanhar({ ctx, op });
      if (r.textos.length || r.equipe) return r;
      return res([TXT.emAnalise], est('clt_assinado'));
    }
    return null;
  }

  return { iniciar, acompanhar, tratarMensagem };
}
