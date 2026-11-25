// =====================================================================
//  whatsapp-webhook — função HTTPS do Supabase (Edge Function)
//  Central de Operações AI — Agente de WhatsApp, etapas (c) RECEBIMENTO e (d) AGENTE
// ---------------------------------------------------------------------
//  O que faz:
//   • GET  → responde à verificação do webhook da Meta (token de verificação).
//   • POST → confere a assinatura X-Hub-Signature-256 com a chave secreta do
//            app; se não bater, recusa (401) e não grava nada.
//            Se bater, entrega o aviso ao banco (função wa_receber_webhook,
//            arquivo 005), que identifica a empresa pelo phone_number_id e
//            grava contato, conversa, mensagem e status, sem duplicar.
//   • Responde rápido à Meta ("EVENT_RECEIVED").
//   • Etapa (d): se chegou mensagem NOVA, aciona a função "whatsapp-agente"
//     EM SEGUNDO PLANO, depois de já ter respondido à Meta. A IA nunca deixa
//     este webhook lento; se o agente estiver fora, a mensagem fica guardada
//     e é atendida na próxima chamada.
//
//  Segredos (NUNCA escreva valores aqui; ficam em Edge Functions → Secrets):
//   WHATSAPP_APP_SECRET    → chave secreta do app "Atendimento Cred Finance"
//   WHATSAPP_VERIFY_TOKEN  → token de verificação (gerado pelo PREPARAR_WEBHOOK.bat)
//   SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY já vêm prontos do próprio Supabase.
//
//  IMPORTANTE: nesta função, "Verify JWT" precisa ficar DESLIGADO
//  (a Meta não manda login do Supabase; a proteção é a assinatura).
// =====================================================================

import { createClient } from "npm:@supabase/supabase-js@2.49.1";

const APP_SECRET = (Deno.env.get("WHATSAPP_APP_SECRET") ?? "").trim();
const VERIFY_TOKEN = (Deno.env.get("WHATSAPP_VERIFY_TOKEN") ?? "").trim();
const SUPABASE_URL = (Deno.env.get("SUPABASE_URL") ?? "").trim();
const TAMANHO_MAXIMO = 3_000_000; // a Meta manda avisos de até ~3 MB
const TEMPO_LIMITE_BANCO_MS = 8_000;

function chaveServidor(): string {
  const legada = (Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "").trim();
  if (legada) return legada;
  // projetos com as chaves novas do Supabase (sb_secret_...)
  try {
    const lista = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") ?? "{}");
    const valor = lista.default ?? Object.values(lista)[0];
    return typeof valor === "string" ? valor.trim() : "";
  } catch {
    return "";
  }
}

const CHAVE_SERVIDOR = chaveServidor();
const URL_AGENTE = SUPABASE_URL ? `${SUPABASE_URL.replace(/\/+$/, "")}/functions/v1/whatsapp-agente` : "";

// Roda depois da resposta à Meta (o Supabase mantém a função viva até terminar)
function emSegundoPlano(tarefa: Promise<unknown>): void {
  // deno-lint-ignore no-explicit-any
  const rt = (globalThis as any).EdgeRuntime;
  if (rt && typeof rt.waitUntil === "function") rt.waitUntil(tarefa);
  else tarefa.catch(() => {});
}

function acionarAgente(): void {
  if (!URL_AGENTE || !CHAVE_SERVIDOR) return;
  const cab: Record<string, string> = {
    "Content-Type": "application/json",
    "x-agente-chave": CHAVE_SERVIDOR,
    apikey: CHAVE_SERVIDOR,
  };
  if (CHAVE_SERVIDOR.startsWith("eyJ")) cab.Authorization = `Bearer ${CHAVE_SERVIDOR}`;
  emSegundoPlano(
    fetch(URL_AGENTE, {
      method: "POST",
      headers: cab,
      body: JSON.stringify({ origem: "webhook" }),
      signal: AbortSignal.timeout(10_000),
    })
      .then(async (r) => {
        await r.body?.cancel();
        console.log("Agente acionado:", r.status);
      })
      .catch((e) => console.warn("Não consegui acionar o agente:", e instanceof Error ? e.name : "erro")),
  );
}
const banco = SUPABASE_URL && CHAVE_SERVIDOR
  ? createClient(SUPABASE_URL, CHAVE_SERVIDOR, {
    auth: { persistSession: false, autoRefreshToken: false },
  })
  : null;

const codificador = new TextEncoder();
let chaveHmac: Promise<CryptoKey> | null = null;

function resposta(status: number, texto: string): Response {
  return new Response(texto, {
    status,
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

// Comparação em tempo constante (não deixa "adivinhar" a assinatura aos poucos)
function iguais(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diferenca = 0;
  for (let i = 0; i < a.length; i++) diferenca |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diferenca === 0;
}

function emHex(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += b.toString(16).padStart(2, "0");
  return s;
}

async function assinaturaValida(corpo: Uint8Array, cabecalho: string): Promise<boolean> {
  const texto = cabecalho.trim().toLowerCase();
  if (!texto.startsWith("sha256=")) return false;
  const recebida = texto.slice(7).trim();
  if (!/^[0-9a-f]{64}$/.test(recebida)) return false;
  chaveHmac ??= crypto.subtle.importKey(
    "raw",
    codificador.encode(APP_SECRET),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const calculada = new Uint8Array(await crypto.subtle.sign("HMAC", await chaveHmac, corpo));
  return iguais(emHex(calculada), recebida);
}

// GET: a Meta confere se o endereço é nosso
function verificarWebhook(url: URL): Response {
  const modo = url.searchParams.get("hub.mode") ?? "";
  const token = url.searchParams.get("hub.verify_token") ?? "";
  const desafio = url.searchParams.get("hub.challenge") ?? "";
  if (
    VERIFY_TOKEN &&
    modo === "subscribe" &&
    iguais(token, VERIFY_TOKEN) &&
    /^[A-Za-z0-9_-]{1,200}$/.test(desafio)
  ) {
    console.log("Webhook verificado pela Meta.");
    return resposta(200, desafio);
  }
  console.warn("Verificação recusada (token de verificação diferente ou faltando).");
  return resposta(403, "Proibido");
}

Deno.serve(async (req: Request): Promise<Response> => {
  const url = new URL(req.url);

  if (req.method === "GET") return verificarWebhook(url);
  if (req.method !== "POST") return resposta(405, "Método não permitido");

  if (!APP_SECRET || !banco) {
    console.error("Configuração incompleta: falta WHATSAPP_APP_SECRET ou a chave do Supabase.");
    return resposta(500, "Configuração incompleta");
  }

  const tamanhoInformado = Number(req.headers.get("content-length") ?? "0");
  if (tamanhoInformado > TAMANHO_MAXIMO) return resposta(413, "Aviso grande demais");

  const corpo = new Uint8Array(await req.arrayBuffer());
  if (corpo.length === 0) return resposta(400, "Aviso vazio");
  if (corpo.length > TAMANHO_MAXIMO) return resposta(413, "Aviso grande demais");

  // 1) Assinatura: só a Meta (que conhece a chave secreta do app) passa daqui
  const assinatura = req.headers.get("x-hub-signature-256") ?? "";
  if (!(await assinaturaValida(corpo, assinatura))) {
    console.warn("Aviso recusado: assinatura X-Hub-Signature-256 ausente ou inválida.");
    return resposta(401, "Assinatura inválida");
  }

  // 2) Lê o JSON
  let aviso: unknown;
  try {
    aviso = JSON.parse(new TextDecoder().decode(corpo));
  } catch {
    console.warn("Aviso assinado, mas o conteúdo não é JSON válido.");
    return resposta(400, "Conteúdo inválido");
  }
  if (!aviso || typeof aviso !== "object" || Array.isArray(aviso)) {
    return resposta(400, "Conteúdo inválido");
  }

  // 3) Grava no banco (tudo de uma vez, sem duplicar)
  try {
    const { data, error } = await banco
      .rpc("wa_receber_webhook", { p_payload: aviso })
      .abortSignal(AbortSignal.timeout(TEMPO_LIMITE_BANCO_MS));
    if (error) {
      // Erro de conexão/banco: responde 500 para a Meta tentar de novo mais tarde
      // (a gravação não duplica, então reenviar é seguro).
      console.error("Não consegui gravar no banco:", error.message);
      return resposta(500, "Erro temporário");
    }
    // Só números no log (nenhum texto de cliente, telefone ou chave)
    console.log("Aviso gravado:", JSON.stringify(data));
    if (Number((data as { mensagens_novas?: number } | null)?.mensagens_novas ?? 0) > 0) acionarAgente();
    return resposta(200, "EVENT_RECEIVED");
  } catch (e) {
    console.error("Banco não respondeu a tempo:", e instanceof Error ? e.name : "erro");
    return resposta(500, "Erro temporário");
  }
});
