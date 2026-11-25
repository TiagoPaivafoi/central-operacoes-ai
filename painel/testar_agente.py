# -*- coding: utf-8 -*-
"""
testar_agente.py — testa o agente de WhatsApp (aberto pelo TESTAR_AGENTE.bat). Etapa (d).

PARTE 1 (automática, ~1 min, usa o Gemini: ~R$ 0,03)
  1. Supabase no ar, SQL 006 rodado e configuração da Cred Finance no banco.
  2. Função whatsapp-agente publicada e protegida (sem a chave: recusa).
  3. Segredos WHATSAPP_TOKEN e GEMINI_API_KEY cadastrados no Supabase.
  4. Liga o robô no número de teste (pergunta antes).
  5. Conversa simulada com um "cliente de teste" (5500000000…), assinada como a
     Meta assina, passando pelo webhook → agente → Gemini → banco:
       a) pergunta sobre um produto → a IA responde (mostra texto, modelo, tokens e tempo);
       b) "quero falar com um atendente" → frase fixa e conversa "aguardando humano";
       c) "SAIR" → cliente marcado como "não quer mensagens".
     Nada vai para a Meta (contato de teste) e tudo é apagado no fim.
PARTE 2 (teste real)
  6. Você manda uma mensagem do seu celular para o número de teste da Meta e
     o robô responde no seu WhatsApp.

Nenhuma chave aparece na tela nem no relatório. O telefone aparece só com os 4 últimos dígitos.
"""
import hashlib
import hmac
import json
import sys
import time
import tomllib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

PASTA = Path(__file__).resolve().parent
RELATORIO = PASTA / "agente_relatorio.txt"
ESPERA_RESPOSTA = 60
ESPERA_REAL_SEGUNDOS = 180
COTACAO = 6.0  # R$ por US$ (folgada, para a estimativa nunca ficar abaixo do real)
PRECOS = {  # US$ por milhão de tokens (entrada, saída) — out/2026
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.8-flash": (0.75, 3.75),
}


def custo_brl(modelo: str, ent: int, sai: int) -> float:
    pe, ps = PRECOS.get(modelo or "", (0.75, 3.75))
    return (ent * pe + sai * ps) / 1_000_000 * COTACAO


def main() -> int:
    resultados: list[tuple[bool, str]] = []
    segredos: list[str] = []

    def limpo(t: str) -> str:
        for s in segredos:
            if s:
                t = t.replace(s, "***")
        return t

    def marcar(ok: bool, texto: str) -> bool:
        resultados.append((ok, texto))
        print(f"  [{'OK' if ok else 'ERRO'}] {limpo(texto)}", flush=True)
        return ok

    def salvar(final: str) -> None:
        try:
            RELATORIO.write_text(
                f"Teste do agente de WhatsApp — {datetime.now():%d/%m/%Y %H:%M}\n"
                + "\n".join(f"[{'OK' if a else 'ERRO'}] {limpo(b)}" for a, b in resultados)
                + f"\nRESULTADO: {final}\n", encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    print("=" * 70)
    print("  TESTE DO AGENTE DE WHATSAPP (etapa d)")
    print("  Parte 1 usa o Gemini em 3 respostas (~R$ 0,03). Parte 2 é o teste real.")
    print("=" * 70)

    # ------------------------------------------------------------ segredos locais
    try:
        with open(PASTA / ".streamlit" / "secrets.toml", "rb") as f:
            seg = tomllib.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"  [ERRO] Não consegui ler o secrets.toml ({type(e).__name__}).")
        return 1
    wa = seg.get("whatsapp") or {}
    precisa = {"SUPABASE_URL": seg.get("SUPABASE_URL"), "SUPABASE_SERVICE_ROLE_KEY": seg.get("SUPABASE_SERVICE_ROLE_KEY"),
               "whatsapp.app_secret": wa.get("app_secret"), "whatsapp.phone_number_id": wa.get("phone_number_id"),
               "whatsapp.waba_id": wa.get("waba_id")}
    faltando = [k for k, v in precisa.items() if not str(v or "").strip()]
    if faltando:
        marcar(False, f"Falta no secrets.toml: {', '.join(faltando)}")
        salvar("REPROVADO")
        return 1
    sb_url = str(seg["SUPABASE_URL"]).strip().rstrip("/")
    sb_key = str(seg["SUPABASE_SERVICE_ROLE_KEY"]).strip()
    app_secret = str(wa["app_secret"]).strip()
    phone_id = str(wa["phone_number_id"]).strip()
    waba_id = str(wa["waba_id"]).strip()
    segredos.extend([sb_key, app_secret, str(wa.get("token") or "").strip(), str(seg.get("GEMINI_API_KEY") or "").strip()])
    url_webhook = f"{sb_url}/functions/v1/whatsapp-webhook"
    url_agente = f"{sb_url}/functions/v1/whatsapp-agente"

    from supabase import create_client
    sb = create_client(sb_url, sb_key)

    # ------------------------------------------------------------ 1. banco
    try:
        conta = (sb.table("whatsapp_accounts").select("id,tenant_id,label,agent_enabled")
                 .eq("phone_number_id", phone_id).execute().data or [None])[0]
    except Exception as e:  # noqa: BLE001
        marcar(False, "Supabase não respondeu. Ele pode estar PAUSADO: abra supabase.com/dashboard, "
                      f"clique no projeto e em 'Restore/Retomar projeto'. Detalhe: {limpo(str(e))[:200]}")
        salvar("REPROVADO")
        return 1
    if not marcar(bool(conta), "Supabase no ar e número de teste cadastrado" if conta
                  else "Número de teste não está no banco: rode o 005_recebimento_whatsapp.sql"):
        salvar("REPROVADO")
        return 1
    tenant_id = conta["tenant_id"]
    try:
        cfg = (sb.table("whatsapp_agent_settings").select("model,fallback_model,business_hours,out_of_hours_mode")
               .eq("tenant_id", tenant_id).execute().data or [None])[0]
    except Exception:  # noqa: BLE001
        cfg = None
    if not marcar(bool(cfg), f"SQL 006 rodado e configuração do agente no banco (modelo {cfg['model']})" if cfg
                  else "Falta rodar o 006_agente_whatsapp.sql no SQL Editor do Supabase"):
        salvar("REPROVADO")
        return 1

    # ------------------------------------------------------------ 2/3. função e segredos
    try:
        r = requests.post(url_agente, json={}, timeout=30)
    except Exception as e:  # noqa: BLE001
        marcar(False, f"A função whatsapp-agente não respondeu: {limpo(str(e))[:200]}")
        salvar("REPROVADO")
        return 1
    if r.status_code == 404:
        marcar(False, "Função 'whatsapp-agente' não encontrada (404). Publique a função com esse nome exato (Parte C).")
        salvar("REPROVADO")
        return 1
    marcar(r.status_code == 401, "Função whatsapp-agente publicada e protegida (sem a chave: recusada)"
           if r.status_code == 401 else f"ATENÇÃO: chamada sem chave não foi recusada (HTTP {r.status_code})")
    cab = {"x-agente-chave": sb_key, "apikey": sb_key}
    if sb_key.startswith("eyJ"):
        cab["Authorization"] = f"Bearer {sb_key}"
    try:
        r = requests.post(url_agente, headers=cab, timeout=60,
                          json={"aguardar": True, "conversation_id": str(uuid.uuid4())})
        corpo = r.json()
    except Exception as e:  # noqa: BLE001
        marcar(False, f"A função whatsapp-agente deu erro: {limpo(str(e))[:200]}")
        salvar("REPROVADO")
        return 1
    if r.status_code == 401:
        marcar(False, "O Supabase pediu login (401): desligue 'Verify JWT' na função whatsapp-agente.")
        salvar("REPROVADO")
        return 1
    falta = corpo.get("segredos_faltando") or []
    if not marcar(not falta, "Segredos WHATSAPP_TOKEN e GEMINI_API_KEY cadastrados no Supabase" if not falta
                  else f"Falta cadastrar no Supabase (Edge Functions → Secrets): {', '.join(falta)}"):
        salvar("REPROVADO")
        return 1

    # ------------------------------------------------------------ 4. ligar o robô
    if not conta.get("agent_enabled"):
        print()
        resp = input("  O robô está DESLIGADO no número de teste. Ligar agora? [S/n] ").strip().lower()
        if resp in ("", "s", "sim", "y"):
            sb.table("whatsapp_accounts").update({"agent_enabled": True}).eq("id", conta["id"]).execute()
            marcar(True, "Robô LIGADO no número de teste")
        else:
            marcar(False, "Robô continua desligado: o teste não tem como continuar")
            salvar("REPROVADO")
            return 1
    else:
        marcar(True, "Robô já está ligado no número de teste")

    # ------------------------------------------------------------ 5. conversa simulada
    marca = uuid.uuid4().hex[:8].upper()
    tel = "5500000000" + str(int(marca[:6], 16) % 1000).zfill(3)
    gasto = 0.0

    def enviar_webhook(texto: str, n: int) -> int:
        dados = {"object": "whatsapp_business_account", "entry": [{"id": waba_id, "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "15551628212", "phone_number_id": phone_id},
            "contacts": [{"profile": {"name": "TESTE DO AGENTE"}, "wa_id": tel}],
            "messages": [{"from": tel, "id": f"wamid.TESTE-AG-{marca}-{n}", "timestamp": str(int(time.time())),
                          "type": "text", "text": {"body": texto}}]}}]}]}
        corpo_b = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        assin = "sha256=" + hmac.new(app_secret.encode(), corpo_b, hashlib.sha256).hexdigest()
        return requests.post(url_webhook, data=corpo_b, timeout=30,
                             headers={"Content-Type": "application/json", "X-Hub-Signature-256": assin}).status_code

    def esperar_resposta(qtd: int) -> list[dict]:
        limite = time.time() + ESPERA_RESPOSTA
        while time.time() < limite:
            conv = (sb.table("contacts").select("id").eq("tenant_id", tenant_id).eq("wa_id", tel).execute().data or [])
            if conv:
                cv = (sb.table("whatsapp_conversations").select("id").eq("contact_id", conv[0]["id"])
                      .order("created_at", desc=True).limit(1).execute().data or [])
                if cv:
                    s = (sb.table("whatsapp_messages")
                         .select("body,sender,ai_model,ai_prompt_tokens,ai_completion_tokens,retrieved_chunk_ids,"
                                 "delivery_status,created_at,payload")
                         .eq("conversation_id", cv[0]["id"]).eq("direction", "outbound")
                         .order("created_at").execute().data or [])
                    if len(s) >= qtd and s[-1].get("delivery_status") != "queued":
                        return s
            time.sleep(2)
        return []

    def estado() -> dict:
        ct = (sb.table("contacts").select("id,funnel_stage,opt_out_at").eq("tenant_id", tenant_id)
              .eq("wa_id", tel).execute().data or [{}])[0]
        cv = (sb.table("whatsapp_conversations").select("status,handoff_reason,ai_state")
              .eq("contact_id", ct.get("id") or "00000000-0000-0000-0000-000000000000")
              .order("created_at", desc=True).limit(1).execute().data or [{}])[0]
        return {**ct, **cv}

    print()
    print("  Conversa simulada (cliente de teste; nada vai para a Meta):")
    try:
        t0 = time.time()
        st1 = enviar_webhook("Olá! Quero saber como funciona a antecipação do FGTS.", 1)
        if not marcar(st1 == 200, "Webhook aceitou a mensagem do cliente de teste" if st1 == 200
                      else f"Webhook respondeu HTTP {st1} (veja Edge Functions → whatsapp-webhook → Logs)"):
            raise RuntimeError("webhook")
        s = esperar_resposta(1)
        if not s:
            marcar(False, f"Nenhuma resposta em {ESPERA_RESPOSTA} s. Causa mais comum: a função whatsapp-webhook "
                          "ainda é a versão ANTIGA (publique a nova, Parte C) ou o agente está com 'Verify JWT' ligado. "
                          "Veja Edge Functions → whatsapp-agente → Logs.")
            raise RuntimeError("sem resposta")
        r1 = s[0]
        segs = time.time() - t0
        if r1["sender"] == "ai":
            ent, sai = int(r1.get("ai_prompt_tokens") or 0), int(r1.get("ai_completion_tokens") or 0)
            c = custo_brl(r1.get("ai_model"), ent, sai)
            gasto += c
            marcar(True, f"IA respondeu em {segs:.0f} s com {r1['ai_model']} | {ent} + {sai} tokens ≈ R$ {c:.4f} | "
                         f"{len(r1.get('retrieved_chunk_ids') or [])} trechos da base")
            print(f"        Resposta: \"{(r1.get('body') or '')[:300]}\"")
            e = estado()
            marcar(True, f"Etapa do funil: {e.get('funnel_stage')} | conversa: {e.get('status')}")
        else:
            e = estado()
            marcar(False, f"O robô mandou a frase fixa em vez da IA. Motivo: {e.get('handoff_reason') or '?'} "
                          "(se for 'fora do horário', rode o teste no horário de atendimento)")
            raise RuntimeError("frase fixa")

        # b) pede atendente
        sb.table("whatsapp_conversations").update({"status": "bot"}) \
            .eq("tenant_id", tenant_id).neq("status", "closed") \
            .in_("contact_id", [estado().get("id")]).execute()
        enviar_webhook("Quero falar com um atendente, por favor", 2)
        s = esperar_resposta(2)
        e = estado()
        marcar(bool(s) and len(s) >= 2 and s[-1]["sender"] == "system" and e.get("status") == "waiting_human",
               f"Pedido de atendente: frase fixa + conversa '{e.get('status')}' (motivo: {e.get('handoff_reason')})")

        # c) SAIR
        sb.table("whatsapp_conversations").update({"status": "bot"}) \
            .eq("tenant_id", tenant_id).neq("status", "closed").in_("contact_id", [e.get("id")]).execute()
        enviar_webhook("SAIR", 3)
        s = esperar_resposta(3)
        e = estado()
        marcar(bool(e.get("opt_out_at")), "SAIR: cliente marcado como 'não quer mais mensagens' e confirmação enviada"
               if e.get("opt_out_at") else "SAIR não foi registrado")
    except RuntimeError:
        pass
    except Exception as e:  # noqa: BLE001
        marcar(False, f"Erro na conversa simulada: {limpo(str(e))[:200]}")
    finally:
        try:
            apagado = sb.rpc("wa_apagar_testes").execute().data
            marcar(True, f"Dados do teste automático apagados ({apagado})")
        except Exception as e:  # noqa: BLE001
            marcar(False, f"Não consegui apagar os dados do teste: {limpo(str(e))[:200]}")

    parte1 = all(a for a, _ in resultados)
    print()
    print("PARTE 1: " + ("APROVADA" if parte1 else "HÁ PROBLEMAS (veja os [ERRO] acima)"))
    if not parte1:
        salvar("PARTE 1 REPROVADA")
        return 1

    # ------------------------------------------------------------ 6. teste real
    print()
    print("-" * 70)
    print("  PARTE 2 — TESTE REAL")
    print("  Pegue o seu celular e mande uma mensagem de WhatsApp para o número de")
    print("  teste da Meta: +1 555 162 8212. Sugestão: 'Oi, quero saber do consignado'.")
    print("  O robô vai responder no seu WhatsApp em alguns segundos.")
    print(f"  Vou esperar até {ESPERA_REAL_SEGUNDOS // 60} minutos. Para desistir: feche a janela.")
    print("-" * 70, flush=True)
    inicio = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
    achou = None
    limite = time.time() + ESPERA_REAL_SEGUNDOS
    while time.time() < limite:
        try:
            achou = (sb.table("whatsapp_messages").select("created_at,conversation_id,body")
                     .eq("tenant_id", tenant_id).eq("direction", "inbound").gte("created_at", inicio)
                     .not_.like("wa_message_id", "wamid.TESTE-%")
                     .order("created_at", desc=True).limit(1).execute().data or [None])[0]
        except Exception:  # noqa: BLE001
            achou = None
        if achou:
            break
        print("  … esperando a sua mensagem chegar", flush=True)
        time.sleep(5)
    if not achou:
        marcar(False, "Nenhuma mensagem real chegou em 3 minutos (confira o webhook com o TESTAR_WEBHOOK.bat).")
        salvar("PARTE 1 APROVADA; TESTE REAL SEM MENSAGEM")
        return 1
    conv_id = achou["conversation_id"]
    cv = (sb.table("whatsapp_conversations").select("status,contact_id").eq("id", conv_id).execute().data or [{}])[0]
    if cv.get("status") != "bot":
        print(f"  (A conversa do seu celular estava como '{cv.get('status')}'.)")
        r = input("  Devolver ao robô e mandar de novo? [S/n] ").strip().lower()
        if r in ("", "s", "sim", "y"):
            sb.table("whatsapp_conversations").update({"status": "bot", "handoff_reason": None}).eq("id", conv_id).execute()
            print("  Pronto. Mande outra mensagem do celular agora.")
            inicio = datetime.now(timezone.utc).isoformat()
    print("  Mensagem recebida. Esperando o robô responder…", flush=True)
    resp = None
    limite = time.time() + ESPERA_REAL_SEGUNDOS
    while time.time() < limite:
        resp = (sb.table("whatsapp_messages")
                .select("body,sender,ai_model,ai_prompt_tokens,ai_completion_tokens,delivery_status,error,created_at")
                .eq("conversation_id", conv_id).eq("direction", "outbound").gte("created_at", inicio)
                .order("created_at", desc=True).limit(1).execute().data or [None])[0]
        if resp and resp.get("delivery_status") not in ("queued",):
            break
        time.sleep(3)
    if not resp:
        cvx = (sb.table("whatsapp_conversations").select("status,handoff_reason").eq("id", conv_id).execute().data or [{}])[0]
        marcar(False, f"O robô não respondeu. Situação da conversa: {cvx.get('status')} "
                      f"({cvx.get('handoff_reason') or 'sem motivo'}). Veja Edge Functions → whatsapp-agente → Logs.")
        salvar("PARTE 1 APROVADA; TESTE REAL SEM RESPOSTA")
        return 1
    if resp.get("delivery_status") == "failed":
        marcar(False, f"O robô respondeu, mas a Meta recusou o envio: {resp.get('error')}")
        salvar("PARTE 1 APROVADA; TESTE REAL COM FALHA NO ENVIO")
        return 1
    ent, sai = int(resp.get("ai_prompt_tokens") or 0), int(resp.get("ai_completion_tokens") or 0)
    if resp.get("sender") == "ai":
        gasto += custo_brl(resp.get("ai_model"), ent, sai)
    ct = (sb.table("contacts").select("wa_id,funnel_stage").eq("id", cv.get("contact_id")).execute().data or [{}])[0]
    tel_real = str(ct.get("wa_id") or "")
    marcar(True, f"RESPOSTA REAL ENVIADA para ****{tel_real[-4:]} | {resp.get('sender')} | "
                 f"{resp.get('ai_model') or 'frase fixa'} | {ent} + {sai} tokens | status '{resp.get('delivery_status')}' | "
                 f"etapa {ct.get('funnel_stage')}")
    print(f"        Resposta: \"{(resp.get('body') or '')[:300]}\"")
    print()
    chegou = input("  A resposta chegou no seu WhatsApp? [S/n] ").strip().lower()
    ok_real = chegou in ("", "s", "sim", "y")
    marcar(ok_real, "Você confirmou: a resposta chegou no celular" if ok_real
           else "Você informou que a resposta NÃO chegou no celular")
    print()
    print(f"  Gasto estimado deste teste no Gemini: R$ {gasto:.3f}")
    final = "APROVADO (parte 1 e teste real)" if ok_real else "PARTE 1 APROVADA; RESPOSTA NÃO CHEGOU NO CELULAR"
    print(f"\nRESULTADO: {final}")
    print("Continue a conversa pelo celular à vontade. Para falar com a equipe, digite ATENDENTE;")
    print("a conversa aparece no painel, aba 💬 Atendimento.")
    salvar(final)
    return 0 if ok_real else 1


if __name__ == "__main__":
    try:
        rc = main()
    except Exception as erro:  # noqa: BLE001
        print(f"\n  [ERRO inesperado] {type(erro).__name__}. Tire um print e envie ao Claude.")
        rc = 1
    try:
        input("\nAperte Enter para fechar...")
    except EOFError:
        pass
    sys.exit(rc)
