// =====================================================================
//  novosaque.mjs — Novo Saque de ponta a ponta (v20.0, 17/11/2026)
//  v20.3d (18/11/2026): dúvida do cliente sobre a autorização no app FGTS → passo a passo (TXT.comoAutorizar).
//  CPF → simulação → ofertas → cliente escolhe → dados → aceite → formalização
//  → link de assinatura → acompanhamento até o pagamento.
//  Tudo aqui é CÓDIGO FIXO (sem IA): CPF e dados pessoais nunca vão para o Gemini,
//  para a memória do robô (ai_state), para o motivo da passagem nem para o Telegram.
//  Os dados pessoais ficam só na tabela novosaque_operacoes (dados_cliente) até a
//  formalização ser aceita; depois são apagados (só a Novo Saque fica com eles).
//  Funções puras e dependências injetadas: testável sem custo (testes_novosaque.mjs).
// =====================================================================

export const NS_BASE_SANDBOX = 'https://api.novosaque.dev.br';
export const NS_VERSAO_ACEITE = 'ns-aceite-v1';

// Etapas da operação (coluna etapa)
export const ETAPAS_FINAIS = ['concluida', 'encerrada', 'equipe'];
const ESPERA_CLIENTE = ['oferta_apresentada', 'coletando', 'aguardando_aceite', 'pagamento_devolvido'];
const ESPERA_SISTEMA_CURTA = ['aguardando_ofertas', 'aguardando_autorizacao', 'aguardando_link', 'link_enviado'];

const SEG = 1000, MIN = 60 * SEG, HORA = 60 * MIN, DIA = 24 * HORA;

// ---------------------------------------------------------------------
// Textos para o cliente (curtos, sem markdown; números só os que a Novo Saque devolve)
// ---------------------------------------------------------------------
export const TXT = {
  consultando: '✅ CPF recebido!\n\n🔎 Estou consultando o seu saldo do FGTS na Novo Saque, nossa parceira.\nEm 1 ou 2 minutos te mando a proposta por aqui.',
  aindaConsultando: '⏳ Ainda estou aguardando a resposta da Novo Saque sobre o seu FGTS.\nAssim que chegar, te mando a proposta aqui.',
  semOferta: 'Consultei o seu FGTS, mas no momento a Novo Saque não encontrou uma proposta disponível para você.\n\nIsso pode acontecer por saldo insuficiente ou porque o saque-aniversário não está ativo.\n\n👤 Vou pedir para a nossa equipe ver outras opções com você.',
  inicioDados: '👍 Ótimo!\n\nPara preencher a proposta, preciso de alguns dados rápidos. Eles são usados só para esta proposta.',
  naoEntendiOferta: 'Não entendi.\n\nResponda *SIM* para seguir com a proposta ou *NÃO* se não quiser esta.',
  todasMostradas: (n) => `Essas foram as opções disponíveis agora.\n\n👉 Para seguir com uma delas, responda o número (1 a ${n}).\nPara parar por aqui, responda *ENCERRAR*.`,
  encerrado: 'Tudo bem, encerrei a proposta por aqui.\n\nSe mudar de ideia, é só me chamar.',
  semMaisOpcoes: 'Entendi.\n\nNo momento não tenho outra opção da Novo Saque para você. Se mudar de ideia, é só me chamar por aqui.',
  enviada: '✅ Proposta enviada para a Novo Saque!\n\nEm instantes te mando o link para assinar o contrato pelo celular.',
  aguardandoLink: '⏳ A Novo Saque está preparando o seu contrato.\nAssim que o link de assinatura ficar pronto, te mando aqui.',
  link: (l) => `📝 Seu contrato está pronto para assinatura:\n${l}\n\nA assinatura é pelo celular e pode pedir uma selfie.\nDepois de assinar, te aviso aqui cada passo até o dinheiro cair no seu PIX.`,
  lembreteLink: (l) => `📝 Passando para lembrar: o seu contrato ainda está esperando a assinatura.\n\nÉ só abrir este link:\n${l}`,
  assinado: '✅ Recebemos a sua assinatura!\n\nAgora o banco faz o registro do contrato e, em seguida, envia o pagamento.\nTe aviso assim que o dinheiro for enviado.',
  pago: (v) => `🎉 Pagamento feito!\n\nO banco enviou *${v}* para a sua chave PIX.\n\nObrigado por escolher a Cred Finance!`,
  pagoSemValor: '🎉 Pagamento feito!\n\nO banco enviou o valor da proposta para a sua chave PIX.\n\nObrigado por escolher a Cred Finance!',
  pixDevolvido: '⚠️ O banco não conseguiu fazer o PIX para a chave informada.\n\nPode me enviar outra chave PIX no seu nome?\n(CPF, e-mail, celular ou chave aleatória)',
  pixReprocessado: '✅ Obrigado!\n\nPedi para o banco refazer o pagamento para essa chave. Te aviso aqui quando o dinheiro for enviado.',
  passarEquipe: '👤 Vou pedir para uma pessoa da nossa equipe continuar com você por aqui para concluir a proposta.',
  autorizar: (inst) => `Para a Novo Saque consultar o seu saldo, o FGTS pede uma autorização.\n\nNo app FGTS:\n1️⃣ Ative o saque-aniversário (se ainda não estiver ativo).\n2️⃣ Em "Autorizar bancos a consultarem seu FGTS", autorize *${inst || 'a instituição parceira da Novo Saque'}*.\n\nQuando terminar, me responda *OK*.\nSe tiver dúvida, é só perguntar que eu explico o passo a passo.`,
  // (v20.3d) Passo a passo detalhado, enviado quando o cliente tem dúvida sobre a autorização.
  comoAutorizar: (inst) => `📲 É rapidinho. No app *FGTS* (o oficial, da Caixa):\n\n1️⃣ Entre com o seu CPF e a senha do gov.br.\n2️⃣ Se ainda não tiver, ative o *saque-aniversário* (opção Saque-Aniversário no menu).\n3️⃣ Na tela inicial, toque em *Mais* e depois em *Autorização de consulta às informações do FGTS* (pode aparecer como "Autorizar bancos a consultarem seu FGTS").\n4️⃣ Escolha *Empréstimo Saque-Aniversário*, leia e aceite o termo.\n5️⃣ Toque para adicionar uma instituição, procure *${inst || 'a instituição parceira da Novo Saque'}* e confirme.\n\nPronto! Depois me responda *OK* que eu confiro de novo.\nSe não conseguir, me diga em qual passo parou.`,
  conferindo: '🔎 Obrigado! Vou conferir de novo com a Novo Saque e te aviso aqui.',
  emRegistro: '⏳ O seu contrato já foi assinado e está em registro no banco.\nAssim que o pagamento for enviado, te aviso aqui.',
};

export const PERGUNTAS = {
  nome_conf: (n) => `👤 O nome que veio na consulta do FGTS é *${n}*.\n\nEstá correto? Responda *SIM* ou me envie o nome completo certo.`,
  nome: '👤 Qual é o seu nome completo?',
  nascimento: '📅 Qual é a sua data de nascimento?\n(ex.: 10/05/1985)',
  email: '📧 Qual é o seu e-mail?',
  cep: '🏠 Qual é o CEP da sua casa?\n(só os 8 números)',
  logradouro: 'Qual é o nome da sua rua?',
  bairro: 'Qual é o seu bairro?',
  cidade_uf: 'Qual é a sua cidade e estado?\n(ex.: Rio de Janeiro/RJ)',
  numero: 'Qual é o número da casa ou do prédio?\n(pode incluir o apartamento, ex.: 369 apto 104; sem número, responda S/N)',
  genero: 'Para o contrato, o banco pede o sexo que consta no seu documento.\n\nResponda *M* (masculino) ou *F* (feminino).',
  ocupacao: '💼 Qual é a sua profissão?',
  pep: 'Você é pessoa politicamente exposta?\n(ocupa ou ocupou, nos últimos 5 anos, cargo público relevante, ou é parente próximo de alguém nessa situação)\n\nResponda *SIM* ou *NÃO*.',
  pix: '💰 Por último: qual chave PIX *no seu nome* vai receber o dinheiro?\n(CPF, e-mail, celular ou chave aleatória)',
};

const ERROS = {
  nome: 'Preciso do nome completo (nome e sobrenome), sem números.',
  nascimento: 'Não consegui ler a data. Envie no formato dia/mês/ano, por exemplo 10/05/1985.',
  email: 'Esse e-mail não parece válido. Pode conferir?',
  cep: 'O CEP tem 8 números. Pode conferir?',
  cep_nao_achado: 'Não encontrei esse CEP. Pode conferir os 8 números?',
  logradouro: 'Pode me enviar o nome da rua?',
  bairro: 'Pode me enviar o nome do bairro?',
  cidade_uf: 'Envie a cidade e a sigla do estado, por exemplo: Rio de Janeiro/RJ.',
  numero: 'Envie o número (e, se quiser, o apartamento), ou S/N se não tiver.',
  genero: 'Responda só M ou F.',
  ocupacao: 'Pode me dizer a sua profissão em poucas palavras?',
  pep: 'Responda só SIM ou NÃO.',
  pix: 'Não reconheci essa chave PIX. Envie o CPF, o e-mail, o celular com DDD ou a chave aleatória.',
  pix_cpf_outro: 'A chave PIX pelo CPF precisa ser do titular da proposta. Envie o seu CPF ou outra chave no seu nome.',
};

// ---------------------------------------------------------------------
// Utilidades
// ---------------------------------------------------------------------
export function normalizar(t) {
  return String(t ?? '').normalize('NFD').replace(/\p{M}/gu, '').toLowerCase()
    .replace(/[^a-z0-9@._+\- ]/g, ' ').replace(/\s+/g, ' ').trim();
}
const palavras = (t) => normalizar(t).replace(/[^a-z0-9 ]/g, ' ').split(' ').filter(Boolean);

export function reais(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '';
  const [int, dec] = Math.abs(n).toFixed(2).split('.');
  return `R$ ${int.replace(/\B(?=(\d{3})+(?!\d))/g, '.')},${dec}`;
}
export function pct(v) {
  const n = Number(v);
  return Number.isFinite(n) ? `${(n * 100).toFixed(2).replace('.', ',')}%` : '';
}
export function cpfValido(d) {
  const s = String(d ?? '').replace(/\D/g, '');
  if (s.length !== 11 || /^(\d)\1{10}$/.test(s)) return false;
  const dv = (n) => {
    let soma = 0;
    for (let i = 0; i < n; i++) soma += Number(s[i]) * (n + 1 - i);
    const r = (soma * 10) % 11;
    return r === 10 ? 0 : r;
  };
  return dv(9) === Number(s[9]) && dv(10) === Number(s[10]);
}
const SIM = new Set(['sim', 's', 'ss', 'quero', 'aceito', 'pode', 'claro', 'ok', 'isso', 'correto', 'certo', 'confirmo', 'positivo', 'bora', 'vamos', 'fechado', 'perfeito', 'esta', 'ta', 'tudo', 'beleza', 'blz', 'yes']);
const NAO = new Set(['nao', 'n', 'negativo', 'outra', 'outro', 'proxima', 'proximo']);
export function ehSim(texto) {
  const p = palavras(texto);
  if (!p.length || p.length > 8) return false;
  if (p.some((x) => NAO.has(x))) return false;
  return SIM.has(p[0]) || (p.length <= 3 && p.some((x) => SIM.has(x)));
}
export function ehNao(texto) {
  const p = palavras(texto);
  return p.length > 0 && p.length <= 8 && NAO.has(p[0]);
}
// (v20.3d) Enquanto espera a autorização no app FGTS: o cliente diz que já fez ou tem dúvida?
export function ehAutorizacaoFeita(texto) {
  const n = normalizar(texto);
  if (/\b(nao|nem)\b/.test(n)) return false;
  return /\b(ok|okay|pronto|feito|fiz|autorizei|autorizado|ja autorizei|concluido|conclui|liberei|liberado|ja fiz|foi)\b/.test(n);
}
export function ehDuvidaAutorizacao(texto) {
  if (ehAutorizacaoFeita(texto)) return false;
  const n = normalizar(texto);
  return String(texto ?? '').includes('?') ||
    /\b(como|onde|qual|quais|nao sei|nao consigo|nao consegui|nao achei|nao acho|nao encontro|nao encontrei|nao aparece|ajuda|ajude|duvida|explica\w*|ensina\w*|passo a passo|giro|autoriza\w*|aplicativo|app|gov|senha|nao entendi|o que (e|eh|fazer|faco))\b/.test(n);
}

export function ehDesistir(texto) {
  const n = normalizar(texto);
  return /\b(desist\w*|cancel\w*|encerra\w*|nao quero mais|deixa pra la|deixa para la|nao tenho interesse|nao tenho mais interesse)\b/.test(n);
}

// Primeiro nome com capitalização simples
export function primeiroNome(t) {
  const p = String(t ?? '').trim().split(/\s+/)[0] ?? '';
  return p ? p.charAt(0).toUpperCase() + p.slice(1).toLowerCase() : '';
}

/** Troca dados pessoais por marcadores antes de o histórico ir para a IA. */
export function mascararDadosPessoais(texto) {
  return String(texto ?? '')
    .replace(/[\w.+-]+@[\w-]+(\.[\w-]+)+/g, '[e-mail informado]')
    .replace(/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi, '[chave informada]')
    .replace(/https:\/\/assinatura[^\s]*/gi, '[link de assinatura]')
    .replace(/(?<!\d)\d{3}[.\s]?\d{3}[.\s]?\d{3}[-.\s/]?\d{2}(?!\d)/g, '[CPF informado]')
    .replace(/(?<!\d)(?:\+?55\s?)?\(?\d{2}\)?\s?9\d{4}[-\s]?\d{4}(?!\d)/g, '[telefone informado]')
    .replace(/(?<!\d)\d{5}-?\d{3}(?!\d)/g, '[CEP informado]')
    .replace(/(?<!\d)\d{1,2}[/.-]\d{1,2}[/.-]\d{4}(?!\d)/g, '[data informada]');
}

// ---------------------------------------------------------------------
// API da Novo Saque (fetch injetado; a chave nunca é registrada)
// ---------------------------------------------------------------------
export function criarApiNovoSaque({ chave, base = NS_BASE_SANDBOX, fetchImpl = fetch, timeoutMs = 20000, pausaMs = 1500 }) {
  const raiz = String(base || NS_BASE_SANDBOX).replace(/\/+$/, '');
  async function chamar(metodo, caminho, corpo) {
    if (!chave) return { status: 0, dados: { error: 'sem_chave' } };
    for (let t = 0; t < 4; t++) { // NS_TENTATIVAS (v20.2): até 4 tentativas quando a Novo Saque oscila
      let r;
      try {
        r = await fetchImpl(raiz + caminho, {
          method: metodo,
          headers: { 'X-Api-Key': chave, Accept: 'application/json', 'Content-Type': 'application/json' },
          body: corpo === undefined ? undefined : JSON.stringify(corpo),
          signal: AbortSignal.timeout(timeoutMs),
        });
      } catch {
        if (t < 3) { await new Promise((ok) => setTimeout(ok, pausaMs * (t + 1))); continue; }
        return { status: 0, dados: { error: 'sem_resposta' } };
      }
      const texto = await r.text().catch(() => '');
      let dados = {};
      try { dados = texto ? JSON.parse(texto) : {}; } catch { dados = { texto: texto.slice(0, 300) }; }
      if ([502, 503, 504].includes(r.status) && t < 3) {
        await new Promise((ok) => setTimeout(ok, pausaMs * 2 * (t + 1)));
        continue;
      }
      return { status: r.status, dados };
    }
    return { status: 0, dados: {} };
  }
  const prod = (p) => encodeURIComponent(String(p).toUpperCase());
  const tid = (t) => encodeURIComponent(String(t));
  return {
    simular: (produto, cpf) => chamar('GET', `/partners-api/contracts/${prod(produto)}/simulation?document=${encodeURIComponent(String(cpf).replace(/\D/g, ''))}`),
    contrato: (produto, t) => chamar('GET', `/partners-api/contracts/${prod(produto)}/contract/${tid(t)}`),
    formalizar: (produto, t, simulationId, cliente) => chamar('POST', `/partners-api/contracts/${prod(produto)}/contract/${tid(t)}/formalization`,
      { transaction_id: String(t), simulation_id: String(simulationId), customer_data: cliente }),
    reprocessarPagamento: (produto, t, bank) => chamar('PUT', `/partners-api/contracts/${prod(produto)}/contract/${tid(t)}/reprocess/payment`, { bank_data: bank }),
  };
}

export function transacaoDaSimulacao(dados) {
  const d = dados ?? {};
  const t = d.transaction_id ?? d.contract?.transaction_id ?? (typeof d.contract === 'string' ? d.contract : null);
  return t && /^[0-9a-f-]{36}$/i.test(String(t)) ? String(t) : null;
}

/** Busca o endereço pelo CEP (ViaCEP). Devolve null se não achou; lança se o serviço falhou. */
export async function buscarCep(cep, fetchImpl = fetch) {
  const r = await fetchImpl(`https://viacep.com.br/ws/${String(cep).replace(/\D/g, '')}/json/`, { signal: AbortSignal.timeout(5000) });
  if (!r.ok) throw new Error('viacep_' + r.status);
  const d = await r.json();
  if (d?.erro) return null;
  return { logradouro: String(d.logradouro ?? '').trim(), bairro: String(d.bairro ?? '').trim(), cidade: String(d.localidade ?? '').trim(), uf: String(d.uf ?? '').trim().toUpperCase() };
}

// ---------------------------------------------------------------------
// Status e ofertas
// ---------------------------------------------------------------------
/** Traduz stage/summary_status da Novo Saque num evento do robô. */
export function lerStatus(c) {
  const stage = String(c?.stage ?? ''), st = String(c?.summary_status ?? '');
  if (st === 'Collateral sended' || stage === 'collateral') return 'concluido';
  if (st === 'Offer available') return 'ofertas';
  if (st === 'Offer unavailable') return 'sem_oferta';
  if (st === 'Awaiting authorization' || stage === 'term') return 'aguardando_autorizacao';
  if (st === 'Contract signature error') return 'erro_assinatura';
  if (st === 'Payment returned') return 'pagamento_devolvido';
  if (st === 'Contract paid' || st === 'Commission paid' || stage === 'pay-commission') return 'pago';
  if (st === 'Registered - Pending payment') return 'registrado';
  if (st.startsWith('Signed contract') || stage === 'registry') return 'assinado';
  if (stage === 'sign-contract' && c?.contract_link) return 'link';
  return 'andamento';
}

function comissaoDe(o) {
  const c = o?.commission ?? {};
  const v = Number(c.commission_corban_value ?? c.commission_total ?? NaN);
  return Number.isFinite(v) ? v : null;
}

/**
 * Ordena e resume as ofertas.
 * Regra 1 do Prompt Mestre: primeiro a que melhor remunera a Cred Finance (comissão da oferta, quando a
 * Novo Saque devolve); sem comissão, a ordem de tabelas_preferidas; empate ou sem preferência: maior valor
 * liberado ao cliente. Mostra no máximo cfg.max_ofertas (padrão 3).
 */
export function resumirOfertas(lista, cfgNs = {}) {
  const validas = (Array.isArray(lista) ? lista : []).filter((o) => o && o.simulation_id && o.success !== false && Number(o.disbursed_amount) > 0);
  const temComissao = validas.some((o) => comissaoDe(o) !== null);
  const pref = (cfgNs.tabelas_preferidas ?? []).map((s) => String(s).toUpperCase());
  const pos = (o) => { const i = pref.indexOf(String(o.table_name ?? '').toUpperCase()); return i < 0 ? 999 : i; };
  const liberado = (o) => Number(o.disbursed_amount) || 0;
  validas.sort((a, b) => (temComissao ? (comissaoDe(b) ?? 0) - (comissaoDe(a) ?? 0) : pos(a) - pos(b)) || liberado(b) - liberado(a));
  const max = Math.max(1, Math.min(5, Number(cfgNs.max_ofertas) || 3));
  return validas.slice(0, max).map((o) => {
    const parcelas = (Array.isArray(o.installments) ? o.installments : [])
      .filter((p) => p && Number(p.birthday_withdrawal_amount) > 0)
      .map((p) => ({ data: String(p.installment_birth_date ?? ''), valor: Number(p.birthday_withdrawal_amount) }));
    return {
      simulation_id: String(o.simulation_id), table_id: o.table_id ? String(o.table_id) : null, tabela: String(o.table_name ?? ''),
      liberado: liberado(o), financiado: Number(o.total_loan_amount ?? o.financed_amount) || null,
      juros_mes: Number(o.interest_rate_monthly), cet_mes: Number(o.cet_monthly_rate), cet_ano: Number(o.cet_annual_rate),
      iof: Number(o.total_iof) || 0, seguro: Number(o.insurance_amount) || 0,
      parcelas, n_parcelas: Number.isFinite(Number(o.installments)) ? Number(o.installments) : parcelas.length,
      valor_parcela: Number(o.installment_value) || null, comissao: comissaoDe(o),
    };
  });
}

function anoDe(data) {
  const m = String(data).match(/(\d{4})/);
  return m ? m[1] : String(data);
}

/** Texto da oferta para o WhatsApp (nunca menciona comissão nem tabela).
 *  (v20.3b) Padrão: só o valor líquido; as condições completas ficam no contrato.
 *  Com detalhes = true (configuração novosaque.oferta_detalhes), mostra também juros e CET. */
export function textoOferta(o, { nome = '', posicao = 0, total = 1, detalhes = false } = {}) {
  const linhas = [];
  linhas.push(posicao === 0 ? `✅ ${nome ? nome + ', e' : 'E'}ncontrei uma proposta para você!` : `Tenho outra opção para você (${posicao + 1} de ${total}):`);
  linhas.push('');
  linhas.push(`💰 Você recebe: *${reais(o.liberado)}* no seu PIX`);
  linhas.push('');
  if (o.parcelas?.length) linhas.push('O valor é descontado direto do seu FGTS, sem parcela no seu bolso.');
  else if (o.valor_parcela && o.n_parcelas) linhas.push(`Pagamento: ${o.n_parcelas} parcelas de ${reais(o.valor_parcela)}.`);
  if (detalhes) linhas.push(`Juros: ${pct(o.juros_mes)} ao mês. Custo total (CET): ${pct(o.cet_mes)} ao mês (${pct(o.cet_ano)} ao ano).`);
  linhas.push('As condições completas estão no contrato que você vai assinar.');
  linhas.push('');
  linhas.push(posicao + 1 < total
    ? 'Quer seguir com esta proposta?\nResponda *SIM*. Se não quiser esta, responda *NÃO* que eu te mostro outra opção.'
    : 'Quer seguir com esta proposta?\nResponda *SIM* ou *NÃO*.');
  return linhas.join('\n');
}

/** Resposta do cliente à oferta: 'sim' | 'nao' | 'desistir' | { escolha: i } | null */
export function lerRespostaOferta(texto, total, mostrouTodas) {
  if (ehDesistir(texto)) return 'desistir';
  const n = normalizar(texto);
  if (mostrouTodas) {
    const m = n.match(/^(?:opcao |a )?([1-5])$/);
    if (m && Number(m[1]) >= 1 && Number(m[1]) <= total) return { escolha: Number(m[1]) - 1 };
  }
  if (ehSim(texto)) return 'sim';
  if (ehNao(texto) || /\b(tem outra|outra opcao|nao gostei|nao quero essa|nao quero esta)\b/.test(n)) return 'nao';
  return null;
}

// ---------------------------------------------------------------------
// Coleta dos dados da formalização
// ---------------------------------------------------------------------
const ORDEM_CAMPOS = ['nome', 'nascimento', 'email', 'cep', 'logradouro', 'bairro', 'cidade_uf', 'numero', 'genero', 'ocupacao', 'pep', 'pix'];

export function proximoCampo(d) {
  for (const c of ORDEM_CAMPOS) {
    if (c === 'cidade_uf' ? !(d.cidade && d.uf) : (d[c] === undefined || d[c] === null || d[c] === '')) return c;
  }
  return null;
}
export function pergunta(campo, d) {
  if (campo === 'nome' && d?.pre_nome) return PERGUNTAS.nome_conf(d.pre_nome);
  return PERGUNTAS[campo] ?? '';
}

function lerData(t) {
  const m = String(t).trim().match(/^(\d{1,2})\s*[/.\- ]\s*(\d{1,2})\s*[/.\- ]\s*(\d{4})$/);
  if (!m) return null;
  const [dia, mes, ano] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const dt = new Date(Date.UTC(ano, mes - 1, dia));
  if (dt.getUTCFullYear() !== ano || dt.getUTCMonth() !== mes - 1 || dt.getUTCDate() !== dia) return null;
  const idade = (Date.now() - dt.getTime()) / (365.25 * DIA);
  if (idade < 18 || idade > 100) return null;
  return `${ano}-${String(mes).padStart(2, '0')}-${String(dia).padStart(2, '0')}`;
}
function lerNome(t) {
  const s = String(t).replace(/\s+/g, ' ').trim();
  if (s.length < 5 || s.length > 120 || /\d/.test(s) || !/^[\p{L}' .-]+$/u.test(s) || s.split(' ').filter((x) => x.length > 1).length < 2) return null;
  return s.toUpperCase();
}

/** Classifica a chave PIX. CPF de 11 números só vale se for o CPF do titular (comparado pelo sistema). */
export function lerPix(texto, cpfTitular) {
  const s = String(texto ?? '').trim();
  const email = s.match(/^[\w.+-]+@[\w-]+(\.[\w-]+)+$/);
  if (email) return { tipo: 'email', valor: s.toLowerCase() };
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s)) return { tipo: 'aleatory_key', valor: s.toLowerCase() };
  if (!/^[\d\s().+\-/]+$/.test(s)) return null;
  let dig = s.replace(/\D/g, '');
  const pareceCpf = /^\d{3}\.\d{3}\.\d{3}-\d{2}$/.test(s);
  if (dig.length === 13 && dig.startsWith('55')) dig = dig.slice(2);
  if (dig.length !== 11) return null;
  const titular = String(cpfTitular ?? '').replace(/\D/g, '');
  if (titular && dig === titular) return { tipo: 'cpf', valor: 'CPF_DO_TITULAR' };
  if (pareceCpf || (cpfValido(dig) && dig[2] !== '9')) return { erro: 'pix_cpf_outro' };
  if (dig[2] === '9') return { tipo: 'phone_number', valor: `+55${dig}` };
  return null;
}

/** Lê a resposta de um campo. Devolve { ok:true, patch } ou { ok:false, erro }. */
export function lerCampo(campo, texto, d = {}) {
  const t = String(texto ?? '').trim();
  switch (campo) {
    case 'nome':
      if (d.pre_nome && ehSim(t)) return { ok: true, patch: { nome: d.pre_nome } };
      return lerNome(t) ? { ok: true, patch: { nome: lerNome(t) } } : { ok: false, erro: ERROS.nome };
    case 'nascimento': { const v = lerData(t); return v ? { ok: true, patch: { nascimento: v } } : { ok: false, erro: ERROS.nascimento }; }
    case 'email': return /^[\w.+-]+@[\w-]+(\.[\w-]+)+$/.test(t) ? { ok: true, patch: { email: t.toLowerCase() } } : { ok: false, erro: ERROS.email };
    case 'cep': { const v = t.replace(/\D/g, ''); return v.length === 8 ? { ok: true, patch: { cep: v } } : { ok: false, erro: ERROS.cep }; }
    case 'logradouro': return t.length >= 3 ? { ok: true, patch: { logradouro: t.slice(0, 120) } } : { ok: false, erro: ERROS.logradouro };
    case 'bairro': return t.length >= 2 ? { ok: true, patch: { bairro: t.slice(0, 80) } } : { ok: false, erro: ERROS.bairro };
    case 'cidade_uf': {
      const m = t.match(/^(.{2,80}?)\s*[/,\- ]\s*([A-Za-z]{2})$/);
      return m ? { ok: true, patch: { cidade: m[1].trim(), uf: m[2].toUpperCase() } } : { ok: false, erro: ERROS.cidade_uf };
    }
    case 'numero': {
      if (/^(s\s*\/?\s*n|sem numero|sem número)$/i.test(t)) return { ok: true, patch: { numero: 'S/N' } };
      if (/^\d{1,6}\s?[a-zA-Z]?$/.test(t)) return { ok: true, patch: { numero: t.replace(/\s/g, '').toUpperCase(), complemento: null } };
      // (v20.3b) número com complemento: "369 apto 104", "369, bloco 2 apt 301"
      const mc = t.match(/^(\d{1,6}[a-zA-Z]?)\s*[,\-/]?\s+(.{2,40})$/);
      if (mc && !/^\d+$/.test(mc[2].trim())) return { ok: true, patch: { numero: mc[1].toUpperCase(), complemento: mc[2].trim().toUpperCase() } };
      return { ok: false, erro: ERROS.numero };
    }
    case 'genero': {
      const n = normalizar(t);
      if (/^(m|masculino|homem)$/.test(n)) return { ok: true, patch: { genero: 'M' } };
      if (/^(f|feminino|mulher)$/.test(n)) return { ok: true, patch: { genero: 'F' } };
      return { ok: false, erro: ERROS.genero };
    }
    case 'ocupacao': return t.length >= 3 && t.length <= 60 && !/\d{4,}/.test(t) ? { ok: true, patch: { ocupacao: t.toUpperCase() } } : { ok: false, erro: ERROS.ocupacao };
    case 'pep':
      if (ehSim(t)) return { ok: true, patch: { pep: true } };
      if (ehNao(t)) return { ok: true, patch: { pep: false } };
      return { ok: false, erro: ERROS.pep };
    default: return { ok: false, erro: '' };
  }
}

export function textoResumo(d, oferta, termoUrl) {
  const num = d.complemento ? `${d.numero}, ${d.complemento}` : d.numero;
  const end = `${d.logradouro}, ${num} - ${d.bairro}, ${d.cidade}/${d.uf}, CEP ${String(d.cep).replace(/(\d{5})(\d{3})/, '$1-$2')}`;
  const nasc = String(d.nascimento).split('-').reverse().join('/');
  const pix = d.pix_tipo === 'cpf' ? 'o seu CPF' : d.pix;
  const termo = termoUrl ? ` e com os termos da Novo Saque: ${termoUrl}` : '. As condições completas estão no contrato que você vai assinar pelo link';
  return [
    '📋 Confira os seus dados:',
    '',
    `Nome: ${d.nome}`,
    `Nascimento: ${nasc}`,
    `E-mail: ${d.email}`,
    `Endereço: ${end}`,
    `Chave PIX: ${pix}`,
    ...(oferta ? ['', `💰 Você recebe: *${reais(oferta.liberado)}*`] : []),
    '',
    `Ao responder SIM, você autoriza a Cred Finance a enviar estes dados à Novo Saque para formalizar a proposta e concorda com a contratação${termo}.`,
    '',
    'Está tudo certo?\nResponda *SIM* para enviar ou *CORRIGIR* para refazer os dados.',
  ].join('\n');
}

/** Monta o customer_data da formalização. Telefone vem do próprio WhatsApp; CPF do titular vem da Novo Saque. */
export function montarCliente(d, contrato, waId) {
  const tel = String(waId ?? '').replace(/\D/g, '');
  const local = tel.startsWith('55') ? tel.slice(2) : tel;
  const cli = {
    name: d.nome, email: d.email, birth_date: d.nascimento, gender: d.genero, nationality: 'Brasileiro',
    occupation: d.ocupacao, politically_exposed_person: d.pep === true,
    street_name: d.complemento ? `${d.logradouro} - ${d.complemento}`.slice(0, 120) : d.logradouro, number: d.numero, district: d.bairro, city: d.cidade, state: d.uf, postal_code: d.cep,
    country_code: 'BRA', phone_country_code: '55', phone_area_code: local.slice(0, 2), phone_number: local.slice(2),
    pix_key_type: d.pix_tipo, pix_key: d.pix_tipo === 'cpf' ? String(contrato?.document ?? contrato?.customer_data?.document ?? '').replace(/\D/g, '') : d.pix,
  };
  return cli;
}

function chavePix(d, contrato) {
  return d.pix_tipo === 'cpf' ? String(contrato?.document ?? '').replace(/\D/g, '') : d.pix;
}

// ---------------------------------------------------------------------
// Fluxo (estado na tabela novosaque_operacoes; dependências injetadas)
// deps: { api, db:{criar(op), atualizar(id, patch)}, cep(cep) , agora(), cfg }
// Resultado: { textos:[], equipe?:motivo, aviso?:texto p/ Telegram, estado?:ai_state, encerrar? }
// ---------------------------------------------------------------------
export function produtoNovoSaque(produtoInteresse, cfgNs = {}) {
  const p = String(produtoInteresse ?? '');
  const produtos = (cfgNs.produtos ?? ['FGTS']).map((x) => String(x).toUpperCase());
  if (/fgts|anivers/i.test(p) && produtos.includes('FGTS')) return 'FGTS';
  return null;
}

export function esperaCliente(op) {
  return Boolean(op) && (ESPERA_CLIENTE.includes(op.etapa) || ESPERA_SISTEMA_CURTA.includes(op.etapa));
}

export function criarFluxoNovoSaque({ api, db, cep = buscarCep, agora = () => Date.now(), cfg = {} }) {
  const quando = (ms) => new Date(agora() + ms).toISOString();
  const idade = (iso) => agora() - (Date.parse(String(iso ?? '')) || agora());
  async function mudar(op, patch) {
    Object.assign(op, patch);
    await db.atualizar(op.id, patch);
  }
  const res = (textos = [], extra = {}) => ({ textos: textos.filter(Boolean), ...extra });
  const paraEquipe = async (op, motivo, texto = TXT.passarEquipe) => {
    await mudar(op, { etapa: 'equipe', dados_cliente: null, proxima_consulta: null, encerrada_em: quando(0) });
    return res([texto], { equipe: motivo });
  };

  async function iniciar({ ctx, cpf, produto }) {
    const r = await api.simular(produto, cpf);
    const tid = transacaoDaSimulacao(r.dados);
    if (![200, 201, 202].includes(r.status) || !tid) return { ok: false, motivo: `Novo Saque não iniciou a simulação (HTTP ${r.status || 'sem resposta'})` };
    const op = await db.criar({
      tenant_id: ctx.empresa.id, conversation_id: ctx.conversa.id, produto, transaction_id: tid,
      etapa: 'aguardando_ofertas', proxima_consulta: quando(20 * SEG), dados_cliente: null, erros: 0, tentativas: 0,
    });
    return { ok: true, op, ...res([TXT.consultando], { estado: { novosaque_etapa: 'aguardando_ofertas' } }) };
  }

  async function lerContrato(op) {
    const r = await api.contrato(op.produto, op.transaction_id);
    const c = r.status === 200 ? (r.dados?.contract ?? null) : null;
    return { r, c };
  }

  /** Consulta a Novo Saque e avança as etapas que dependem do sistema. */
  async function acompanhar({ ctx, op }) {
    if (ETAPAS_FINAIS.includes(op.etapa) || ESPERA_CLIENTE.includes(op.etapa)) return res();
    const { r, c } = await lerContrato(op);
    if (!c) {
      const erros = Number(op.erros ?? 0) + 1;
      if (erros >= 15) return paraEquipe(op, `Novo Saque sem resposta na consulta do contrato (HTTP ${r.status || 'sem resposta'})`);
      await mudar(op, { erros, proxima_consulta: quando(60 * SEG) });
      return res();
    }
    const ev = lerStatus(c);
    const base = { stage: String(c.stage ?? ''), summary_status: String(c.summary_status ?? ''), erros: 0 };
    const oferta = (op.ofertas ?? [])[op.oferta_idx ?? 0];
    const nome = primeiroNome(ctx?.conversa?.ai_state?.dados?.nome || ctx?.contato?.nome || '');

    if (op.etapa === 'aguardando_ofertas' || op.etapa === 'aguardando_autorizacao') {
      if (ev === 'ofertas') {
        const ofertas = resumirOfertas(c.simulation_results, cfg);
        if (!ofertas.length) { await mudar(op, base); return paraEquipe(op, 'Novo Saque: oferta disponível, mas sem valores válidos', TXT.semOferta); }
        const b = c.balance_check_result ?? {};
        const pre = {};
        if (b.client_name) {
          // (v20.3b) nome oficial da consulta do FGTS: usa direto (o cliente confere no resumo e pode responder CORRIGIR)
          pre.pre_nome = String(b.client_name).toUpperCase().slice(0, 120);
          if (lerNome(pre.pre_nome)) pre.nome = pre.pre_nome;
        }
        // (v20.3b) dados que a Novo Saque já devolve no contrato: aproveita e não pergunta de novo
        const cd = c.customer_data ?? {};
        if (/^[\w.+-]+@[\w-]+(\.[\w-]+)+$/.test(String(cd.email ?? ''))) pre.email = String(cd.email).toLowerCase();
        if (/^[MF]$/i.test(String(cd.gender ?? ''))) pre.genero = String(cd.gender).toUpperCase();
        console.log('Novo Saque: dados que vieram da consulta:', [...Object.keys(b).filter((k) => b[k] !== null && b[k] !== ''), ...Object.keys(cd).filter((k) => cd[k] !== null && cd[k] !== '').map((k) => 'customer_data.' + k)].join(',') || 'nenhum');
        if (b.birth_date) {
          const bd = String(b.birth_date).slice(0, 10);
          const br = bd.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
          if (br) pre.nascimento = `${br[3]}-${br[2]}-${br[1]}`;
          else if (/^\d{4}-\d{2}-\d{2}$/.test(bd)) pre.nascimento = bd;
        }
        if (b.job_description) pre.ocupacao = String(b.job_description).toUpperCase().slice(0, 60);
        await mudar(op, { ...base, etapa: 'oferta_apresentada', ofertas, oferta_idx: 0, mostrou_todas: false, dados_cliente: pre, proxima_consulta: null, tentativas: 0 });
        return res([textoOferta(ofertas[0], { nome, posicao: 0, total: ofertas.length, detalhes: cfg.oferta_detalhes === true })],
          { estado: { novosaque_etapa: 'oferta_apresentada' }, aviso: `Novo Saque: ${ofertas.length} oferta(s) apresentada(s) ao cliente (maior valor liberado ${reais(Math.max(...ofertas.map((o) => o.liberado)))})` });
      }
      if (ev === 'sem_oferta') { await mudar(op, base); return paraEquipe(op, 'Novo Saque: sem oferta disponível para o cliente (FGTS)', TXT.semOferta); }
      if (ev === 'aguardando_autorizacao') {
        // No sandbox esse status aparece por poucos segundos; só orienta o cliente se ficar parado.
        if (op.etapa === 'aguardando_ofertas' && idade(op.criado_em) > 2 * MIN) {
          await mudar(op, { ...base, etapa: 'aguardando_autorizacao', autorizacao_pedida_em: quando(0), proxima_consulta: quando(60 * SEG) });
          return res([TXT.autorizar(cfg.instituicao_fgts)], { estado: { novosaque_etapa: 'aguardando_autorizacao' } });
        }
        if (op.etapa === 'aguardando_autorizacao' && idade(op.autorizacao_pedida_em) > 3 * HORA) {
          await mudar(op, base);
          return paraEquipe(op, 'Novo Saque: cliente não autorizou a consulta do FGTS em 3 horas');
        }
        await mudar(op, { ...base, proxima_consulta: quando(op.etapa === 'aguardando_autorizacao' ? 60 * SEG : 20 * SEG) });
        return res();
      }
      if (idade(op.criado_em) > 20 * MIN && op.etapa === 'aguardando_ofertas') { await mudar(op, base); return paraEquipe(op, 'Novo Saque demorou mais de 20 minutos para devolver as ofertas'); }
      await mudar(op, { ...base, proxima_consulta: quando(20 * SEG) });
      return res();
    }

    if (op.etapa === 'aguardando_link' || op.etapa === 'link_enviado') {
      if (ev === 'erro_assinatura') { await mudar(op, base); return paraEquipe(op, 'Novo Saque: erro na assinatura do contrato'); }
      if (ev === 'pagamento_devolvido') {
        if (Number(op.reprocessamentos ?? 0) >= 2) { await mudar(op, base); return paraEquipe(op, 'Novo Saque: pagamento devolvido de novo depois de 2 chaves PIX', ''); }
        await mudar(op, { ...base, etapa: 'pagamento_devolvido', dados_cliente: {}, proxima_consulta: null, tentativas: 0 });
        return res([TXT.pixDevolvido], { aviso: 'Novo Saque: pagamento devolvido (chave PIX). O robô pediu outra chave ao cliente.' });
      }
      if (ev === 'pago' || ev === 'concluido') {
        await mudar(op, { ...base, etapa: ev === 'concluido' ? 'concluida' : 'pago', proxima_consulta: ev === 'concluido' ? null : quando(10 * MIN), pago_em: quando(0) });
        return res([oferta ? TXT.pago(reais(oferta.liberado)) : TXT.pagoSemValor], { estado: { operacao_concluida: true, novosaque_etapa: 'pago' }, aviso: 'Novo Saque: pagamento ENVIADO ao cliente ✅' });
      }
      if (ev === 'assinado' || ev === 'registrado') {
        await mudar(op, { ...base, etapa: 'em_registro', proxima_consulta: quando(2 * MIN), assinado_em: quando(0) });
        return res([TXT.assinado], { estado: { novosaque_etapa: 'em_registro' }, aviso: 'Novo Saque: cliente ASSINOU o contrato ✍️' });
      }
      if (op.etapa === 'aguardando_link') {
        if (c.contract_link) {
          await mudar(op, { ...base, etapa: 'link_enviado', contract_link: String(c.contract_link), link_enviado_em: quando(0), proxima_consulta: quando(60 * SEG) });
          return res([TXT.link(String(c.contract_link))], { estado: { novosaque_etapa: 'link_enviado' }, aviso: 'Novo Saque: link de assinatura enviado ao cliente' });
        }
        if (idade(op.formalizado_em) > 15 * MIN) { await mudar(op, base); return paraEquipe(op, 'Novo Saque: o link de assinatura não ficou pronto em 15 minutos'); }
        await mudar(op, { ...base, proxima_consulta: quando(15 * SEG) });
        return res();
      }
      // link_enviado: lembrete único depois de 3 horas; equipe depois de 48 horas
      if (idade(op.link_enviado_em) > 48 * HORA) { await mudar(op, base); return paraEquipe(op, 'Novo Saque: cliente não assinou o contrato em 48 horas'); }
      if (!op.lembrete_em && idade(op.link_enviado_em) > 3 * HORA && op.contract_link) {
        await mudar(op, { ...base, lembrete_em: quando(0), proxima_consulta: quando(2 * MIN) });
        return res([TXT.lembreteLink(op.contract_link)]);
      }
      await mudar(op, { ...base, proxima_consulta: quando(idade(op.link_enviado_em) > HORA ? 3 * MIN : 60 * SEG) });
      return res();
    }

    if (op.etapa === 'em_registro') {
      if (ev === 'pago' || ev === 'concluido') {
        await mudar(op, { ...base, etapa: ev === 'concluido' ? 'concluida' : 'pago', proxima_consulta: ev === 'concluido' ? null : quando(10 * MIN), pago_em: quando(0) });
        return res([oferta ? TXT.pago(reais(oferta.liberado)) : TXT.pagoSemValor], { estado: { operacao_concluida: true, novosaque_etapa: 'pago' }, aviso: 'Novo Saque: pagamento ENVIADO ao cliente ✅' });
      }
      if (ev === 'pagamento_devolvido') {
        if (Number(op.reprocessamentos ?? 0) >= 2) { await mudar(op, base); return paraEquipe(op, 'Novo Saque: pagamento devolvido de novo depois de 2 chaves PIX', ''); }
        await mudar(op, { ...base, etapa: 'pagamento_devolvido', dados_cliente: {}, proxima_consulta: null, tentativas: 0 });
        return res([TXT.pixDevolvido], { aviso: 'Novo Saque: pagamento devolvido (chave PIX). O robô pediu outra chave ao cliente.' });
      }
      if (idade(op.assinado_em) > 5 * DIA) { await mudar(op, base); return paraEquipe(op, 'Novo Saque: pagamento não saiu em 5 dias após a assinatura', ''); }
      await mudar(op, { ...base, proxima_consulta: quando(3 * MIN) });
      return res();
    }

    if (op.etapa === 'pago') {
      if (ev === 'concluido') {
        await mudar(op, { ...base, etapa: 'concluida', proxima_consulta: null, encerrada_em: quando(0) });
        return res([], { aviso: 'Novo Saque: operação CONCLUÍDA (Collateral sended) 🏁' });
      }
      if (idade(op.pago_em) > 10 * DIA) { await mudar(op, { ...base, etapa: 'concluida', proxima_consulta: null, encerrada_em: quando(0) }); return res(); }
      await mudar(op, { ...base, proxima_consulta: quando(15 * MIN) });
      return res();
    }
    return res();
  }

  async function perguntarProximo(op, d, antes = []) {
    const campo = proximoCampo(d);
    if (!campo) {
      await mudar(op, { etapa: 'aguardando_aceite', campo_atual: null, dados_cliente: d, tentativas: 0 });
      return res([...antes, textoResumo(d, (op.ofertas ?? [])[op.oferta_idx ?? 0], cfg.termo_url || op.termo_url)], { estado: { novosaque_etapa: 'aguardando_aceite' } });
    }
    await mudar(op, { etapa: 'coletando', campo_atual: campo, dados_cliente: d, tentativas: 0 });
    return res([...antes, pergunta(campo, d)], { estado: { novosaque_etapa: 'coletando' } });
  }

  async function formalizar(ctx, op) {
    const d = op.dados_cliente ?? {};
    const escolhida = (op.ofertas ?? [])[op.oferta_idx ?? 0];
    let ultimo = null;
    for (let t = 0; t < 2; t++) {
      // simulation_id muda a cada consulta: pega o atual da MESMA tabela antes de enviar
      const { c } = await lerContrato(op);
      if (!c) { ultimo = { status: 0 }; continue; }
      if (lerStatus(c) !== 'ofertas') return paraEquipe(op, `Novo Saque: as ofertas não estão mais disponíveis (${String(c.summary_status ?? '')})`);
      const atual = (c.simulation_results ?? []).find((o) => escolhida?.table_id && String(o.table_id) === escolhida.table_id) ??
        (c.simulation_results ?? []).find((o) => String(o.simulation_id) === escolhida?.simulation_id);
      if (!atual?.simulation_id) return paraEquipe(op, 'Novo Saque: a oferta escolhida não aparece mais na consulta');
      const r = await api.formalizar(op.produto, op.transaction_id, atual.simulation_id, montarCliente(d, c, ctx?.contato?.wa_id));
      if (r.status === 202 || r.status === 200) {
        await mudar(op, {
          etapa: 'aguardando_link', dados_cliente: null, simulation_id: String(atual.simulation_id), aceite_em: quando(0), aceite_versao: NS_VERSAO_ACEITE,
          formalizado_em: quando(0), proxima_consulta: quando(10 * SEG), tentativas: 0,
          termo_url: c.terms_link ? String(c.terms_link).slice(0, 500) : (op.termo_url ?? null),
        });
        return res([TXT.enviada], { estado: { novosaque_etapa: 'aguardando_link' }, aviso: `Novo Saque: proposta FORMALIZADA (cliente recebe ${reais(escolhida?.liberado)})` });
      }
      ultimo = r;
      const erro = String(r.dados?.error ?? '');
      if (!(r.status === 400 && /invalid/i.test(erro) && !r.dados?.invalid_fields)) break; // só repete quando o simulation_id venceu
    }
    const campos = Object.keys(ultimo?.dados?.invalid_fields ?? {}).slice(0, 8).join(', ');
    return paraEquipe(op, `Novo Saque recusou a formalização (HTTP ${ultimo?.status || 'sem resposta'}${campos ? `; campos: ${campos}` : ''})`);
  }

  async function lerPixDoCliente(op, texto) {
    const tem11 = String(texto).replace(/\D/g, '').length >= 11 && !/@/.test(texto);
    let cpfTitular = null;
    if (tem11) {
      const { c } = await lerContrato(op);
      cpfTitular = c?.document ?? c?.customer_data?.document ?? null;
    }
    return lerPix(texto, cpfTitular);
  }

  /** Trata a mensagem do cliente quando a operação espera algo dele. null = deixar o fluxo normal responder. */
  async function tratarMensagem({ ctx, op, texto }) {
    if (!esperaCliente(op)) return null;
    const antesDaFormalizacao = ['oferta_apresentada', 'coletando', 'aguardando_aceite', 'aguardando_ofertas', 'aguardando_autorizacao'].includes(op.etapa);
    if (ehDesistir(texto) && antesDaFormalizacao) {
      await mudar(op, { etapa: 'encerrada', dados_cliente: null, proxima_consulta: null, encerrada_em: quando(0) });
      return res([TXT.encerrado], { estado: { novosaque_etapa: 'encerrada' }, aviso: 'Novo Saque: cliente desistiu da proposta' });
    }
    const falhou = async (msg) => {
      const n = Number(op.tentativas ?? 0) + 1;
      if (n >= 3) return paraEquipe(op, `Novo Saque: cliente não conseguiu responder (${op.etapa}${op.campo_atual ? ' / ' + op.campo_atual : ''})`);
      await mudar(op, { tentativas: n });
      return res([msg]);
    };

    if (ESPERA_SISTEMA_CURTA.includes(op.etapa)) {
      // (v20.3d) Dúvida sobre a autorização no app FGTS: explica o passo a passo (sem IA e sem consultar a API).
      if (op.etapa === 'aguardando_autorizacao' && ehDuvidaAutorizacao(texto)) {
        return res([TXT.comoAutorizar(cfg.instituicao_fgts)], { estado: { novosaque_etapa: 'aguardando_autorizacao' } });
      }
      const r = await acompanhar({ ctx, op });
      if (r.textos.length || r.equipe) return r;
      if (op.etapa === 'aguardando_autorizacao') { await mudar(op, { proxima_consulta: quando(10 * SEG) }); return res([TXT.conferindo]); }
      if (op.etapa === 'aguardando_ofertas') return res([TXT.aindaConsultando]);
      if (op.etapa === 'aguardando_link') return res([TXT.aguardandoLink]);
      if (op.etapa === 'link_enviado' && op.contract_link) return res([TXT.lembreteLink(op.contract_link)]);
      return null;
    }

    if (op.etapa === 'oferta_apresentada') {
      const ofertas = op.ofertas ?? [];
      const resp = lerRespostaOferta(texto, ofertas.length, op.mostrou_todas === true);
      if (resp === 'sim' || (resp && typeof resp === 'object')) {
        const idx = typeof resp === 'object' ? resp.escolha : (op.oferta_idx ?? 0);
        await mudar(op, { oferta_idx: idx });
        const d = { ...(op.dados_cliente ?? {}) };
        return perguntarProximo(op, d, [TXT.inicioDados]);
      }
      if (resp === 'nao') {
        const prox = (op.oferta_idx ?? 0) + 1;
        if (prox < ofertas.length && !op.mostrou_todas) {
          await mudar(op, { oferta_idx: prox, tentativas: 0 });
          return res([textoOferta(ofertas[prox], { posicao: prox, total: ofertas.length, detalhes: cfg.oferta_detalhes === true })]);
        }
        if (ofertas.length > 1 && !op.mostrou_todas) { await mudar(op, { mostrou_todas: true, tentativas: 0 }); return res([TXT.todasMostradas(ofertas.length)]); }
        await mudar(op, { etapa: 'encerrada', dados_cliente: null, proxima_consulta: null, encerrada_em: quando(0) });
        return res([TXT.semMaisOpcoes], { estado: { novosaque_etapa: 'encerrada' }, aviso: 'Novo Saque: cliente recusou todas as ofertas' });
      }
      return falhou(op.mostrou_todas ? TXT.todasMostradas(ofertas.length) : TXT.naoEntendiOferta);
    }

    if (op.etapa === 'coletando') {
      const d = { ...(op.dados_cliente ?? {}) };
      const campo = op.campo_atual || proximoCampo(d);
      if (campo === 'pix') {
        const p = await lerPixDoCliente(op, texto);
        if (!p || p.erro) return falhou(`${ERROS[p?.erro ?? 'pix']}\n\n${PERGUNTAS.pix}`);
        return perguntarProximo(op, { ...d, pix_tipo: p.tipo, pix: p.valor });
      }
      const lido = lerCampo(campo, texto, d);
      if (!lido.ok) return falhou(`${lido.erro}\n\n${pergunta(campo, d)}`);
      Object.assign(d, lido.patch);
      if (campo === 'cep') {
        try {
          const e = await cep(d.cep);
          if (!e) { delete d.cep; await mudar(op, { dados_cliente: d }); return falhou(`${ERROS.cep_nao_achado}\n\n${PERGUNTAS.cep}`); }
          for (const [k, v] of Object.entries(e)) if (v) d[k] = v;
        } catch { /* ViaCEP fora: pergunta rua, bairro e cidade */ }
      }
      return perguntarProximo(op, d);
    }

    if (op.etapa === 'aguardando_aceite') {
      if (/\bcorrig\w*\b/.test(normalizar(texto))) {
        const d = op.dados_cliente ?? {};
        const pre = Object.fromEntries(Object.entries(d).filter(([k]) => ['pre_nome'].includes(k)));
        return perguntarProximo(op, pre);
      }
      if (ehSim(texto)) return formalizar(ctx, op);
      if (ehNao(texto)) return falhou('Sem problemas.\n\nPara refazer os dados, responda *CORRIGIR*.\nSe preferir parar por aqui, responda *ENCERRAR*.');
      return falhou('Para enviar a proposta, responda *SIM*.\nPara refazer os dados, responda *CORRIGIR*.');
    }

    if (op.etapa === 'pagamento_devolvido') {
      const p = await lerPixDoCliente(op, texto);
      if (!p || p.erro) return falhou(`${ERROS[p?.erro ?? 'pix']}\n\n${PERGUNTAS.pix}`);
      let chave = p.valor;
      if (p.tipo === 'cpf') { const { c } = await lerContrato(op); chave = chavePix({ pix_tipo: 'cpf' }, c); }
      const r = await api.reprocessarPagamento(op.produto, op.transaction_id, { pix_key_type: p.tipo, pix_key: chave });
      if (r.status === 200 || r.status === 202) {
        await mudar(op, { etapa: 'em_registro', dados_cliente: null, proxima_consulta: quando(2 * MIN), assinado_em: op.assinado_em ?? quando(0), tentativas: 0, reprocessamentos: Number(op.reprocessamentos ?? 0) + 1 });
        return res([TXT.pixReprocessado], { aviso: 'Novo Saque: nova chave PIX enviada (reprocessar pagamento)' });
      }
      return paraEquipe(op, `Novo Saque recusou o reprocessamento do pagamento (HTTP ${r.status || 'sem resposta'})`);
    }
    return null;
  }

  return { iniciar, acompanhar, tratarMensagem };
}

/** Primeiro CPF VÁLIDO do texto (só números). Usado apenas para chamar a API; nunca é gravado. */
export function extrairCpf(texto) {
  const achados = String(texto ?? '').match(/(?<!\d)\d{3}[.\s]?\d{3}[.\s]?\d{3}[-.\s/]?\d{2}(?!\d)/g) || [];
  for (const a of achados) {
    const d = a.replace(/\D/g, '');
    if (cpfValido(d)) return d;
  }
  return null;
}
