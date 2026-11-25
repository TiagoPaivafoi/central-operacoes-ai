// =====================================================================
//  novosaque-webhook — função HTTPS do Supabase (Edge Function)
//  Versão 1.0 (17/11/2026, robô v20.1). Recebe os avisos da Novo Saque ("uma etapa terminou").
//
//  Endereço a informar ao parceiro (Verify JWT DESLIGADO nesta função):
//    https://<SEU-PROJETO>.supabase.co/functions/v1/novosaque-webhook?chave=<NOVOSAQUE_WEBHOOK_KEY>
//
//  Como funciona: o aviso é só um "cutucão". A função confere a chave da URL, pega SÓ o
//  transaction_id e pede ao banco para antecipar a consulta daquela proposta (ns_cutucar).
//  O robô confirma o status na API da Novo Saque com a nossa chave: o conteúdo do aviso nunca
//  é usado, então um aviso falso só causa uma consulta a mais. A consulta a cada minuto
//  continua funcionando como reserva.
//  Privacidade: o corpo do aviso pode ter nome e data de nascimento do cliente. Nada dele é
//  gravado nem vai para os Logs (só a etapa e o resultado).
//  Segredos usados: NOVOSAQUE_WEBHOOK_KEY e os automáticos SUPABASE_URL e
//  SUPABASE_SERVICE_ROLE_KEY.
// =====================================================================

const SUPABASE_URL = (Deno.env.get("SUPABASE_URL") ?? "").trim().replace(/\/+$/, "");
const CHAVE_SERVIDOR = (Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "").trim();
const CHAVE_WEBHOOK = (Deno.env.get("NOVOSAQUE_WEBHOOK_KEY") ?? "").trim();
const TAMANHO_MAXIMO = 256 * 1024; // avisos maiores que 256 KB são recusados
const TEMPO_BANCO_MS = 8_000;

function json(status: number, corpo: Record<string, unknown>): Response {
  return new Response(JSON.stringify(corpo), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

/** Comparação em tempo constante (não dá pistas da chave pelo tempo de resposta). */
function iguais(a: string, b: string): boolean {
  const x = new TextEncoder().encode(a);
  const y = new TextEncoder().encode(b);
  let dif = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) dif |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return dif === 0;
}

/** Só letras, números e - _ . : (o transaction_id da Novo Saque); qualquer outra coisa é ignorada. */
export function lerTransacao(corpo: unknown): string | null {
  if (!corpo || typeof corpo !== "object") return null;
  const bruto = (corpo as Record<string, unknown>).transaction_id;
  if (typeof bruto !== "string" && typeof bruto !== "number") return null;
  const id = String(bruto).trim();
  return /^[A-Za-z0-9._:-]{4,100}$/.test(id) ? id : null;
}

/** Nome da etapa só para o log (texto curto e limpo). */
export function lerEtapa(corpo: unknown): string {
  const s = (corpo as Record<string, unknown> | null)?.stage;
  return typeof s === "string" ? s.replace(/[^\w -]/g, "").slice(0, 40) : "";
}

async function cutucar(transacao: string): Promise<string> {
  const r = await fetch(`${SUPABASE_URL}/rest/v1/rpc/ns_cutucar`, {
    method: "POST",
    headers: {
      apikey: CHAVE_SERVIDOR,
      ...(CHAVE_SERVIDOR.startsWith("eyJ") ? { Authorization: `Bearer ${CHAVE_SERVIDOR}` } : {}),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ p_transaction_id: transacao }),
    signal: AbortSignal.timeout(TEMPO_BANCO_MS),
  });
  const t = await r.text();
  if (!r.ok) throw new Error(`banco HTTP ${r.status} ${t.slice(0, 120)}`);
  try {
    return String(JSON.parse(t));
  } catch {
    return t.slice(0, 60);
  }
}

Deno.serve(async (req: Request): Promise<Response> => {
  if (!CHAVE_WEBHOOK || CHAVE_WEBHOOK.length < 24 || !SUPABASE_URL || !CHAVE_SERVIDOR) {
    console.error("Webhook Novo Saque: falta o segredo NOVOSAQUE_WEBHOOK_KEY (mínimo 24 caracteres).");
    return json(503, { ok: false, erro: "Webhook não configurado" });
  }
  const url = new URL(req.url);
  const chave = url.searchParams.get("chave") ?? req.headers.get("x-webhook-key") ?? "";
  if (!iguais(chave, CHAVE_WEBHOOK)) {
    console.warn("Webhook Novo Saque: chamada recusada (chave errada).");
    return json(401, { ok: false, erro: "Não autorizado" });
  }
  if (req.method === "GET" || req.method === "HEAD") return json(200, { ok: true, servico: "novosaque-webhook" });
  if (req.method !== "POST") return json(405, { ok: false, erro: "Use POST" });

  const tamanho = Number(req.headers.get("content-length") ?? "0");
  if (tamanho > TAMANHO_MAXIMO) return json(413, { ok: false, erro: "Aviso grande demais" });
  let corpo: unknown;
  try {
    const texto = await req.text();
    if (texto.length > TAMANHO_MAXIMO) return json(413, { ok: false, erro: "Aviso grande demais" });
    corpo = texto ? JSON.parse(texto) : null;
  } catch {
    return json(400, { ok: false, erro: "JSON inválido" });
  }
  const etapa = lerEtapa(corpo);
  const transacao = lerTransacao(corpo);
  if (!transacao) {
    console.warn(`Webhook Novo Saque: aviso sem transaction_id (etapa ${etapa || "?"}).`);
    return json(200, { ok: true, ignorado: "sem transaction_id" });
  }
  try {
    const resultado = await cutucar(transacao);
    console.log(`Webhook Novo Saque: etapa ${etapa || "?"} → ${resultado} (transação …${transacao.slice(-6)})`);
    return json(200, { ok: true });
  } catch (e) {
    // Responde 500 para a Novo Saque tentar de novo; a consulta a cada minuto segue como reserva.
    console.error("Webhook Novo Saque: falhou ao avisar o banco:", e instanceof Error ? e.message.slice(0, 160) : "erro");
    return json(500, { ok: false, erro: "Falha temporária" });
  }
});
