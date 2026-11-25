# -*- coding: utf-8 -*-
"""
testar_webhook.py — testa o recebimento de mensagens do WhatsApp (aberto pelo TESTAR_WEBHOOK.bat).

PARTE 1 (automática, custo zero, ~30 s)
  1. Supabase no ar e número de teste cadastrado (SQL 005 rodado).
  2. Verificação do webhook: token certo é aceito; token errado é recusado.
  3. Aviso com assinatura errada ou sem assinatura é RECUSADO.
  4. Mensagem simulada, assinada como a Meta assina, é gravada 1 vez;
     enviada de novo, NÃO duplica.
  5. Avisos de status (enviada/entregue/lida) chegando fora de ordem não "voltam" a etapa.
  6. Apaga tudo o que o teste criou.
  7. Confere na Meta se o webhook está cadastrado no app e se o app está ligado
     à conta do WhatsApp (se não estiver, liga — custo zero).
PARTE 2 (teste real)
  8. Você manda uma mensagem do seu celular para o número de teste da Meta e o
     programa mostra a mensagem gravada no banco.

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
RELATORIO = PASTA / "webhook_relatorio.txt"
FUNCAO = "whatsapp-webhook"
ESPERA_REAL_SEGUNDOS = 180


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

    def salvar_relatorio(final: str) -> None:
        try:
            RELATORIO.write_text(
                f"Teste do webhook do WhatsApp — {datetime.now():%d/%m/%Y %H:%M}\n"
                + "\n".join(f"[{'OK' if a else 'ERRO'}] {limpo(b)}" for a, b in resultados)
                + f"\nRESULTADO: {final}\n", encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    print("=" * 66)
    print("  TESTE DO RECEBIMENTO DE MENSAGENS DO WHATSAPP (custo zero)")
    print("=" * 66)

    # ---------------------------------------------------------------- segredos
    try:
        with open(PASTA / ".streamlit" / "secrets.toml", "rb") as f:
            seg = tomllib.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"  [ERRO] Não consegui ler o secrets.toml ({type(e).__name__}).")
        return 1
    wa = seg.get("whatsapp") or {}
    precisa = {"SUPABASE_URL": seg.get("SUPABASE_URL"), "SUPABASE_SERVICE_ROLE_KEY": seg.get("SUPABASE_SERVICE_ROLE_KEY"),
               "whatsapp.app_secret": wa.get("app_secret"), "whatsapp.webhook_verify_token": wa.get("webhook_verify_token"),
               "whatsapp.phone_number_id": wa.get("phone_number_id"), "whatsapp.waba_id": wa.get("waba_id"),
               "whatsapp.app_id": wa.get("app_id"), "whatsapp.token": wa.get("token")}
    faltando = [k for k, v in precisa.items() if not str(v or "").strip()]
    if faltando:
        marcar(False, f"Falta no secrets.toml: {', '.join(faltando)}. "
                      "(webhook_verify_token é criado pelo PREPARAR_WEBHOOK.bat)")
        salvar_relatorio("REPROVADO")
        return 1

    sb_url = str(seg["SUPABASE_URL"]).strip().rstrip("/")
    sb_key = str(seg["SUPABASE_SERVICE_ROLE_KEY"]).strip()
    app_secret = str(wa["app_secret"]).strip()
    verify = str(wa["webhook_verify_token"]).strip()
    phone_id = str(wa["phone_number_id"]).strip()
    waba_id = str(wa["waba_id"]).strip()
    app_id = str(wa["app_id"]).strip()
    token = str(wa["token"]).strip()
    versao = str(wa.get("graph_version") or "v26.0").strip()
    segredos.extend([sb_key, app_secret, verify, token])
    url_funcao = f"{sb_url}/functions/v1/{FUNCAO}"
    print(f"  Endereço do webhook: {url_funcao}\n")

    from supabase import create_client
    sb = create_client(sb_url, sb_key)

    # ---------------------------------------------------------------- 1. Supabase
    try:
        r = sb.table("whatsapp_accounts").select("id,tenant_id,label").eq("phone_number_id", phone_id).execute()
        conta = (r.data or [None])[0]
    except Exception as e:  # noqa: BLE001
        marcar(False, "Supabase não respondeu. Ele pode estar PAUSADO: abra supabase.com/dashboard, "
                      f"clique no projeto e em 'Restore/Retomar projeto'. Detalhe: {limpo(str(e))[:200]}")
        salvar_relatorio("REPROVADO")
        return 1
    if not marcar(bool(conta), "Supabase no ar e número de teste cadastrado" if conta
                  else "Número de teste não está no banco: rode o 005_recebimento_whatsapp.sql"):
        salvar_relatorio("REPROVADO")
        return 1
    tenant_id = conta["tenant_id"]

    # ---------------------------------------------------------------- 2. verificação
    desafio = str(uuid.uuid4().int)[:10]
    try:
        g = requests.get(url_funcao, params={"hub.mode": "subscribe", "hub.verify_token": verify,
                                             "hub.challenge": desafio}, timeout=30)
    except Exception as e:  # noqa: BLE001
        marcar(False, f"A função não respondeu: {limpo(str(e))[:200]}")
        salvar_relatorio("REPROVADO")
        return 1
    if g.status_code == 404:
        marcar(False, "Função 'whatsapp-webhook' não encontrada (404). Publique a função com esse nome exato.")
        salvar_relatorio("REPROVADO")
        return 1
    if g.status_code == 401:
        marcar(False, "O Supabase pediu login (401): desligue 'Verify JWT' na função whatsapp-webhook.")
        salvar_relatorio("REPROVADO")
        return 1
    if not marcar(g.status_code == 200 and g.text.strip() == desafio,
                  "Verificação da Meta: token certo aceito" if g.status_code == 200
                  else f"Verificação recusada (HTTP {g.status_code}). O segredo WHATSAPP_VERIFY_TOKEN no "
                       "Supabase não é igual ao do secrets.toml: rode o PREPARAR_WEBHOOK.bat de novo."):
        salvar_relatorio("REPROVADO")
        return 1
    g2 = requests.get(url_funcao, params={"hub.mode": "subscribe", "hub.verify_token": "token-errado",
                                          "hub.challenge": desafio}, timeout=30)
    marcar(g2.status_code == 403, "Verificação com token errado: recusada" if g2.status_code == 403
           else f"ATENÇÃO: token errado foi aceito (HTTP {g2.status_code})")

    # ---------------------------------------------------------------- 3/4/5. avisos simulados
    marca = uuid.uuid4().hex[:10].upper()
    tel_teste = "5500000000" + str(int(marca[:6], 16) % 1000).zfill(3)
    wamid_in = f"wamid.TESTE-IN-{marca}"
    wamid_out = f"wamid.TESTE-OUT-{marca}"
    agora = str(int(time.time()))

    def aviso(value: dict) -> dict:
        return {"object": "whatsapp_business_account",
                "entry": [{"id": waba_id, "changes": [{"field": "messages", "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "15551628212", "phone_number_id": phone_id},
                    **value}}]}]}

    def enviar(dados: dict, assinatura: str | None = "certa") -> requests.Response:
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        cab = {"Content-Type": "application/json"}
        if assinatura == "certa":
            cab["X-Hub-Signature-256"] = "sha256=" + hmac.new(app_secret.encode(), corpo, hashlib.sha256).hexdigest()
        elif assinatura == "errada":
            cab["X-Hub-Signature-256"] = "sha256=" + hmac.new(b"chave-errada", corpo, hashlib.sha256).hexdigest()
        return requests.post(url_funcao, data=corpo, headers=cab, timeout=30)

    msg = aviso({
        "contacts": [{"profile": {"name": "TESTE AUTOMÁTICO"}, "wa_id": tel_teste, "user_id": f"BR.TESTE{marca}"}],
        "messages": [{"from": tel_teste, "from_user_id": f"BR.TESTE{marca}", "id": wamid_in,
                      "timestamp": agora, "type": "text", "text": {"body": f"Olá, teste automático {marca}"}}],
    })

    try:
        p1 = enviar(msg, assinatura="errada")
        marcar(p1.status_code == 401, "Assinatura errada: recusada" if p1.status_code == 401
               else f"ATENÇÃO: assinatura errada não foi recusada (HTTP {p1.status_code})")
        p2 = enviar(msg, assinatura=None)
        marcar(p2.status_code == 401, "Sem assinatura: recusada" if p2.status_code == 401
               else f"ATENÇÃO: aviso sem assinatura não foi recusado (HTTP {p2.status_code})")

        p3 = enviar(msg)
        if p3.status_code == 401:
            marcar(False, "Assinatura certa foi recusada: o segredo WHATSAPP_APP_SECRET no Supabase não é a "
                          "chave secreta do app Atendimento Cred Finance. Rode o PREPARAR_WEBHOOK.bat de novo.")
        else:
            marcar(p3.status_code == 200, "Mensagem simulada aceita (resposta rápida à Meta)" if p3.status_code == 200
                   else f"Mensagem simulada: HTTP {p3.status_code} — veja Edge Functions → whatsapp-webhook → Logs")
        p4 = enviar(msg)  # mesma mensagem de novo
        gravadas = sb.table("whatsapp_messages").select("id,body,direction,conversation_id") \
            .eq("tenant_id", tenant_id).eq("wa_message_id", wamid_in).execute().data or []
        marcar(p4.status_code == 200 and len(gravadas) == 1,
               "Mensagem gravada no banco 1 vez (a repetida foi ignorada)" if len(gravadas) == 1
               else f"Mensagem gravada {len(gravadas)} vez(es) — esperado 1")

        if gravadas:
            conv_id = gravadas[0]["conversation_id"]
            contato = sb.table("contacts").select("id,name,wa_user_id").eq("tenant_id", tenant_id) \
                .eq("wa_id", tel_teste).execute().data or []
            marcar(bool(contato) and contato[0].get("wa_user_id") == f"BR.TESTE{marca}",
                   "Contato criado com telefone e identificador novo (BSUID)" if contato else "Contato não foi criado")
            # mensagem "enviada pela empresa" (simulada) para testar os avisos de status
            sb.table("whatsapp_messages").insert({
                "tenant_id": tenant_id, "conversation_id": conv_id, "direction": "outbound", "sender": "system",
                "wa_message_id": wamid_out, "message_type": "text", "body": "teste de status",
                "delivery_status": "queued"}).execute()

            def status(st: str) -> dict:
                return aviso({"statuses": [{"id": wamid_out, "status": st, "timestamp": agora,
                                            "recipient_id": tel_teste, "recipient_user_id": f"BR.TESTE{marca}"}]})
            for st in ("delivered", "sent", "read", "read"):   # fora de ordem e repetido
                enviar(status(st))
            final = sb.table("whatsapp_messages").select("delivery_status").eq("tenant_id", tenant_id) \
                .eq("wa_message_id", wamid_out).execute().data or [{}]
            hist = sb.table("whatsapp_message_statuses").select("status").eq("tenant_id", tenant_id) \
                .eq("wa_message_id", wamid_out).execute().data or []
            marcar(final[0].get("delivery_status") == "read" and len(hist) == 3,
                   f"Status registrados (enviada, entregue, lida = {len(hist)} avisos, sem repetir) e "
                   f"mensagem ficou como '{final[0].get('delivery_status')}'")
    finally:
        try:
            apagado = sb.rpc("wa_apagar_testes").execute().data
            marcar(True, f"Dados do teste automático apagados ({apagado})")
        except Exception as e:  # noqa: BLE001
            marcar(False, f"Não consegui apagar os dados do teste: {limpo(str(e))[:200]}")

    # ---------------------------------------------------------------- 7. Meta
    base = f"https://graph.facebook.com/{versao}"
    try:
        sub = requests.get(f"{base}/{app_id}/subscriptions",
                           params={"access_token": f"{app_id}|{app_secret}"}, timeout=30).json()
        if "error" in sub:
            raise RuntimeError(sub["error"].get("message", "erro"))
        wa_sub = next((s for s in sub.get("data", []) if s.get("object") == "whatsapp_business_account"), None)
        campos = {f.get("name") for f in (wa_sub or {}).get("fields", [])}
        ok_url = bool(wa_sub) and wa_sub.get("callback_url") == url_funcao and wa_sub.get("active", True)
        marcar(ok_url and "messages" in campos,
               "Webhook cadastrado no app da Meta com o campo 'messages'" if ok_url and "messages" in campos
               else ("Webhook ainda não cadastrado na Meta (faça a Parte D do checklist)" if not wa_sub
                     else "Webhook cadastrado, mas falta assinar o campo 'messages' ou o endereço está diferente"))
    except Exception as e:  # noqa: BLE001
        marcar(False, f"Não consegui conferir o webhook na Meta: {limpo(str(e))[:200]}")

    try:
        cab = {"Authorization": f"Bearer {token}"}
        apps = requests.get(f"{base}/{waba_id}/subscribed_apps", headers=cab, timeout=30).json()
        if "error" in apps:
            raise RuntimeError(apps["error"].get("message", "erro"))
        ligado = any(str((a.get("whatsapp_business_api_data") or {}).get("id")) == app_id for a in apps.get("data", []))
        if not ligado:
            liga = requests.post(f"{base}/{waba_id}/subscribed_apps", headers=cab, timeout=30).json()
            ligado = bool(liga.get("success"))
            marcar(ligado, "App ligado agora à conta do WhatsApp (para a Meta mandar os avisos)" if ligado
                   else f"Não consegui ligar o app à conta do WhatsApp: {limpo(str(liga))[:200]}")
        else:
            marcar(True, "App já está ligado à conta do WhatsApp (a Meta manda os avisos para ele)")
    except Exception as e:  # noqa: BLE001
        marcar(False, f"Não consegui conferir a ligação do app com a conta do WhatsApp: {limpo(str(e))[:200]}")

    parte1 = all(a for a, _ in resultados)
    print()
    print("PARTE 1: " + ("APROVADA" if parte1 else "HÁ PROBLEMAS (veja os [ERRO] acima)"))
    if not parte1:
        salvar_relatorio("PARTE 1 REPROVADA")
        return 1

    # ---------------------------------------------------------------- 8. teste real
    print()
    print("-" * 66)
    print("  PARTE 2 — TESTE REAL")
    print("  Pegue o seu celular e mande uma mensagem de WhatsApp para o número de")
    print("  teste da Meta: +1 555 162 8212  (ex.: 'Olá, teste do webhook').")
    print("  (É o número que te mandou o 'Hello World'. Pode responder na mesma conversa.)")
    print(f"  Vou esperar até {ESPERA_REAL_SEGUNDOS // 60} minutos. Para desistir: feche a janela.")
    print("-" * 66, flush=True)
    inicio = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    achou = None
    limite = time.time() + ESPERA_REAL_SEGUNDOS
    while time.time() < limite:
        try:
            achou = (sb.table("whatsapp_messages")
                     .select("created_at,message_type,body,conversation_id")
                     .eq("tenant_id", tenant_id).eq("direction", "inbound")
                     .gte("created_at", inicio).not_.like("wa_message_id", "wamid.TESTE-%")
                     .order("created_at", desc=True).limit(1).execute().data or [None])[0]
        except Exception as e:  # noqa: BLE001
            print(f"  (aguardando… {limpo(str(e))[:80]})", flush=True)
        if achou:
            break
        print("  … esperando a mensagem chegar", flush=True)
        time.sleep(6)

    if not achou:
        marcar(False, "Nenhuma mensagem real chegou em 3 minutos. Confira a Parte D do checklist "
                      "(webhook e campo 'messages') e veja Edge Functions → whatsapp-webhook → Logs.")
        salvar_relatorio("PARTE 1 APROVADA; TESTE REAL NÃO RECEBEU MENSAGEM")
        return 1

    conv, ct, rotulo = {}, {}, "?"
    try:
        conv = (sb.table("whatsapp_conversations").select("contact_id,whatsapp_account_id")
                .eq("id", achou["conversation_id"]).execute().data or [{}])[0]
        ct = (sb.table("contacts").select("wa_id").eq("id", conv.get("contact_id")).execute().data or [{}])[0]
        rotulo = (sb.table("whatsapp_accounts").select("label").eq("id", conv.get("whatsapp_account_id"))
                  .execute().data or [{}])[0].get("label", "?")
    except Exception:  # noqa: BLE001
        pass
    tel = str(ct.get("wa_id") or "")
    tel_mask = ("****" + tel[-4:]) if tel else "(sem telefone, só BSUID)"
    texto = (achou.get("body") or f"[{achou.get('message_type')}]")[:80]
    try:
        from zoneinfo import ZoneInfo
        from banco import parse_data
        hora = parse_data(achou.get("created_at")).astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m %H:%M:%S")
    except Exception:  # noqa: BLE001
        hora = str(achou.get("created_at", ""))[:19].replace("T", " ") + " UTC"
    marcar(True, f"MENSAGEM REAL GRAVADA NO BANCO: {hora} | tipo {achou.get('message_type')} | "
                 f"texto \"{texto}\" | contato {tel_mask} | número {rotulo}")
    print()
    print("RESULTADO: TESTE REAL APROVADO — a mensagem do seu celular está gravada no Supabase.")
    print("(Nenhuma resposta automática foi enviada: isso é a etapa d.)")
    salvar_relatorio("APROVADO (parte 1 e teste real)")
    return 0


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
