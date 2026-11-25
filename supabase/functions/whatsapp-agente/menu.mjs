// =====================================================================
//  menu.mjs — menu de produtos no cumprimento (v21.3, 23/11/2026)
//  v21.4: consignado e FGTS sem perguntar valor: INSS e servidor pedem o CPF direto (fixo, sem IA);
//         CLT vai direto ao CPF na Facta (sem a pergunta da carteira: o cliente já escolheu CLT).
//  Mensagem FIXA (sem IA), um produto por linha, numerado. Funções puras, testáveis sem custo.
//  - "oi" / "olá" / "bom dia" fora de uma simulação → menu (zera a memória do produto anterior).
//  - Escolha (número ou palavra) → FGTS: Novo Saque (pede o CPF) · CLT: Facta (pergunta da carteira)
//    · outros produtos: atendente de IA já sabendo o produto · "outro assunto": IA livre.
//  - Contato que veio de ANÚNCIO: sem menu; o robô fala só do produto do anúncio. No fim do atendimento
//    pergunta se a pessoa quer mais alguma coisa; com SIM, mostra o menu.
//  - Desligar sem Deploy: agent_config.menu = {"enabled": false}. Só na conversa de teste: menu.conversas_teste = ["id"].
// =====================================================================
import { normalizarTexto } from './origem_campanha.mjs';

export const OPCOES_MENU = [
  { n: 1, id: 'fgts', rotulo: 'Antecipação do Saque-Aniversário FGTS', produto: 'Antecipação do saque-aniversário FGTS', rota: 'fgts',
    chaves: /\bfgts\b|saque aniversario|aniversario/ },
  { n: 2, id: 'clt', rotulo: 'Consignado CLT (carteira assinada)', produto: 'Consignado CLT', rota: 'clt',
    chaves: /\bclt\b|carteira|trabalhador/ },
  { n: 3, id: 'inss', rotulo: 'Consignado INSS (aposentados e pensionistas)', produto: 'Consignado INSS (aposentados e pensionistas)', rota: 'cpf',
    frase_cpf: '💼 Vamos simular o seu *consignado INSS*.\n\n📲 Me envie o seu *CPF* (só os números).',
    chaves: /\binss\b|aposentad|pensionista/ },
  { n: 4, id: 'servidor', rotulo: 'Consignado para servidor público', produto: 'Consignado para servidor público', rota: 'cpf',
    frase_cpf: '💼 Vamos simular o seu *consignado para servidor público*.\n\n📲 Me envie o seu *CPF* (só os números).',
    chaves: /servidor|funcionari[oa] public|prefeitura|militar|forcas armadas/ },
  { n: 5, id: 'cartao', rotulo: 'Empréstimo no limite do cartão de crédito', produto: 'Empréstimo no limite do cartão de crédito', rota: 'ia',
    chaves: /cartao/ },
  { n: 6, id: 'imovel', rotulo: 'Empréstimo com garantia de imóvel', produto: 'Empréstimo com garantia de imóvel (home equity)', rota: 'ia',
    chaves: /imovel|home equity|casa propria|apartamento/ },
  { n: 7, id: 'energia', rotulo: 'Desconto na conta de luz (energia)', produto: 'Desconto na conta de luz (energia)', rota: 'ia',
    chaves: /\bluz\b|energia|solar/ },
  { n: 8, id: 'outro', rotulo: 'Outro assunto ou tirar uma dúvida', produto: '', rota: 'livre',
    chaves: /outro assunto|outra coisa|\bduvida|\boutro\b/ },
];

const EMOJI_NUMERO = ['', '1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣'];

export const TEXTO_OUTRO_ASSUNTO = 'Claro! 😊\n\nMe conta em poucas palavras o que você precisa ou qual é a sua dúvida.';
export const TEXTO_MAIS_ALGO = 'Posso te ajudar com *mais alguma coisa*? 😊\n\nSe quiser ver os outros produtos da Cred Finance, responda *SIM*.';
export const TEXTO_ATE_LOGO = 'Combinado! 😊\n\nObrigado por falar com a *Cred Finance*.\nSe precisar, é só mandar um *oi* por aqui.';

/** Janela (minutos) em que a conversa ainda conta como "no meio de um produto" e em que a escolha do menu vale. */
export const JANELA_PADRAO_MIN = 30;
export const VALIDADE_MENU_MIN = 120;

export function menuLigado(cfg) {
  return cfg?.menu?.enabled !== false;
}

/** Trava opcional para testar antes: agent_config.menu.conversas_teste = ["id"] → menu só nessas conversas. */
export function menuNestaConversa(cfg, convId) {
  const lista = cfg?.menu?.conversas_teste;
  return !Array.isArray(lista) || lista.length === 0 || lista.includes(convId);
}

/** Texto do menu. cabecalho = 'cumprimento' (no "oi") ou 'retorno' (depois do SIM em "mais alguma coisa"). */
export function textoMenu(cfg, cabecalho = 'cumprimento') {
  const topo = cabecalho === 'retorno'
    ? 'Ótimo! 😊\nQual produto você quer simular?'
    : `Olá! 👋 Sou o assistente da *${String(cfg?.menu?.nome_empresa || 'Cred Finance')}*.\nQual produto você quer simular?`;
  const linhas = OPCOES_MENU.map((o) => `${EMOJI_NUMERO[o.n]} ${o.rotulo}`);
  return `${topo}\n\n${linhas.join('\n')}\n\nÉ só responder com o número. 😉`;
}

const CUMPRIMENTO = /^(?:(?:oi+e?|ola+|opa|ei|eai|e ai|salve|hey|hello|bom dia+|boa tarde|boa noite|tudo bem|tudo bom|tudo certo|td bem|como vai|menu|inicio|comecar)(?: |$))+$/;

/** As mensagens novas são só um cumprimento ("oi", "olá", "bom dia", "menu")? */
export function ehCumprimento(textos) {
  const lista = (Array.isArray(textos) ? textos : [textos]).map(normalizarTexto).filter(Boolean);
  return lista.length > 0 && lista.every((t) => CUMPRIMENTO.test(t));
}

/** Qual opção o cliente escolheu? Número (1 a 8, também 1️⃣) ou palavra (uma só opção). Senão, null. */
export function escolhaDoMenu(texto) {
  const n = normalizarTexto(texto);
  if (!n || n.length > 60) return null;
  const digitos = n.match(/\d+/g) || [];
  if (digitos.length === 1 && /^[1-8]$/.test(digitos[0])) return OPCOES_MENU[Number(digitos[0]) - 1];
  if (digitos.length) return null;
  const achadas = OPCOES_MENU.filter((o) => o.chaves.test(n));
  return achadas.length === 1 ? achadas[0] : null;
}

export function opcaoPorId(id) {
  return OPCOES_MENU.find((o) => o.id === id) ?? null;
}

/** SIM / NÃO para a pergunta "mais alguma coisa?". Senão, null. */
export function respostaSimNao(texto) {
  const n = normalizarTexto(texto);
  if (!n) return null;
  if (/^(nao|n|no)( |$)|^(so isso|e so isso|era so isso|nada|nada nao|tudo certo|ta bom obrigad[oa]|obrigad[oa]|valeu|agradeco)( |$)/.test(n)) return 'nao';
  if (/^(sim|s|ss|quero|claro|pode|pode ser|isso|ok|com certeza|bora|vamos|tenho interesse|tenho|gostaria)( |$)/.test(n)) return 'sim';
  return null;
}

function minutosDesde(iso, agora) {
  const t = Date.parse(String(iso ?? ''));
  return Number.isFinite(t) ? (agora - t) / 60_000 : Infinity;
}

/** O menu ainda está esperando a escolha (mostrado há pouco)? */
export function menuEsperandoEscolha(estado, agora = Date.now(), cfg = {}) {
  const validade = Number(cfg?.menu?.validade_escolha_min) || VALIDADE_MENU_MIN;
  return estado?.menu_aguardando === true && minutosDesde(estado?.menu_em, agora) <= validade;
}

/** Produto do anúncio (opção do menu) ou null. Ordem: campanha da Genial ligada → energia;
 *  agent_config.menu.anuncios {id_do_anúncio: "fgts"|"clt"|…}; palavras do título do anúncio; padrão configurado. */
export function opcaoDoAnuncio(origem, cfg, modoCampanha = false) {
  if (origem?.tipo !== 'campanha') return null;
  if (modoCampanha) return opcaoPorId('energia');
  const mapa = cfg?.menu?.anuncios;
  if (mapa && typeof mapa === 'object' && origem.anuncio && mapa[origem.anuncio]) {
    const op = opcaoPorId(String(mapa[origem.anuncio]));
    if (op && op.rota !== 'livre') return op;
  }
  const titulo = normalizarTexto(origem.titulo);
  if (titulo) {
    const achadas = OPCOES_MENU.filter((o) => o.rota !== 'livre' && o.chaves.test(titulo));
    if (achadas.length === 1) return achadas[0];
  }
  const padrao = cfg?.menu?.produto_anuncio_padrao ? opcaoPorId(String(cfg.menu.produto_anuncio_padrao)) : null;
  return padrao && padrao.rota !== 'livre' ? padrao : null;
}

/** O atendimento ainda está preso ao produto do anúncio? (até concluir, até perguntar "mais alguma coisa" ou até ver o menu) */
export function anuncioAtivo(estado, opcaoAnuncio) {
  return Boolean(opcaoAnuncio) && estado?.anuncio_liberado !== true &&
    estado?.operacao_concluida !== true && estado?.outras_solucoes_perguntado !== true;
}

/** Mostrar o menu agora? Só cumprimento, fora de simulação, fora do produto de anúncio e sem conversa recente de produto. */
export function deveMostrarMenu({ textos, estado = {}, operacaoEmAndamento = false, opcaoAnuncio = null, cfg = {}, agora = Date.now() }) {
  if (!menuLigado(cfg) || !ehCumprimento(textos)) return false;
  if (operacaoEmAndamento) return false;
  if (anuncioAtivo(estado, opcaoAnuncio)) return false;
  const janela = Number(cfg?.menu?.janela_minutos) || JANELA_PADRAO_MIN;
  const noMeio = Boolean(String(estado?.produto_interesse ?? '').trim()) || estado?.aguardando_cpf === true || estado?.aguardando_confirmacao === true;
  if (noMeio && minutosDesde(estado?.atualizado_em, agora) <= janela) return false;
  return true;
}

const DADOS_DA_PESSOA = new Set(['nome', 'nome_completo', 'email', 'e_mail', 'telefone', 'cidade', 'uf', 'estado']);

/** Memória "zerada" do produto anterior (a memória do banco soma; por isso grava os campos vazios por cima). */
export function estadoZerado(estadoAtual = {}) {
  const dados = {};
  for (const [k, v] of Object.entries(estadoAtual?.dados ?? {})) {
    if (!DADOS_DA_PESSOA.has(k) && v !== '' && v != null) dados[k] = '';
  }
  return {
    produto_interesse: '', produto_menu: '', aguardando_cpf: false, aguardando_confirmacao: false,
    operacao_concluida: false, outras_solucoes_perguntado: false, aguardando_mais_algo: false,
    ...(Object.keys(dados).length ? { dados } : {}),
  };
}

/** Memória gravada junto com o menu. */
export function estadoDoMenu(estadoAtual = {}, agora = Date.now()) {
  return { ...estadoZerado(estadoAtual), menu_aguardando: true, menu_em: new Date(agora).toISOString(), anuncio_liberado: true };
}

/** Memória gravada quando o cliente escolhe um produto (pelo menu ou pelo anúncio). */
export function estadoDaEscolha(estadoAtual, opcao, via = 'menu') {
  return {
    ...estadoZerado(estadoAtual),
    produto_interesse: opcao.produto, produto_menu: opcao.id, menu_aguardando: false,
    ...(via === 'anuncio' ? { anuncio_produto: opcao.id } : {}),
  };
}

/** Instruções extras para a IA (vão no fim das instruções do sistema). */
export function instrucoesProduto({ escolhaAgora = null, via = 'menu', estado = {}, opcaoAnuncio = null } = {}) {
  const partes = [];
  if (escolhaAgora) {
    if (escolhaAgora.rota === 'livre') {
      partes.push('O cliente escolheu "outro assunto" no menu: atenda a necessidade que ele contar, sem empurrar um produto.');
    } else {
      partes.push(`${via === 'anuncio' ? 'ANÚNCIO' : 'ESCOLHA NO MENU'}: o cliente ${via === 'anuncio' ? 'chegou pelo anúncio de' : 'acabou de escolher no menu'}: ${escolhaAgora.rotulo}. ` +
        'Não apresente a Cred Finance de novo e não pergunte o que ele procura: comece direto pela primeira pergunta do roteiro deste produto, uma pergunta por mensagem. ' +
        'A escolha do cliente vale: não troque por outro produto (por exemplo, não ofereça consignado a quem escolheu garantia de imóvel). ' +
        'Se este produto não tiver simulação automática, peça só os dados necessários; com eles a equipe continua.');
    }
  }
  if (anuncioAtivo(estado, opcaoAnuncio)) {
    partes.push(`ANÚNCIO (vale acima do MODO GERAL): este cliente veio do anúncio de ${opcaoAnuncio.rotulo}. ` +
      'Fale só deste produto até o atendimento dele terminar: não apresente a Cred Finance de forma genérica, não pergunte o que ele procura, não liste nem ofereça outros produtos. ' +
      'Se o cliente pedir claramente outro assunto, atenda o pedido dele.');
  } else if (!escolhaAgora) {
    const op = opcaoPorId(String(estado?.produto_menu ?? ''));
    if (op && op.rota !== 'livre' && String(estado?.produto_interesse ?? '').trim()) {
      partes.push(`PRODUTO ESCOLHIDO NO MENU: ${op.rotulo}. Continue neste produto, sem oferecer outros, até o próximo passo real (simulação, cadastro ou equipe).`);
    }
  }
  return partes.length ? `\n${partes.join('\n')}\n` : '';
}

/** Fim de uma operação automática (pagamento enviado): pergunta "mais alguma coisa?" uma vez. */
export function perguntaMaisAlgoAoConcluir(estadoAtual, res) {
  if (!res || res.equipe || res?.estado?.operacao_concluida !== true) return null;
  if (estadoAtual?.outras_solucoes_perguntado === true) return null;
  return { texto: TEXTO_MAIS_ALGO, estado: { outras_solucoes_perguntado: true, aguardando_mais_algo: true } };
}
