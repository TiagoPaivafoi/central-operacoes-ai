# -*- coding: utf-8 -*-
"""
atendimento.py — aba "💬 Atendimento" do painel (agente de WhatsApp, etapa d).

O que a equipe faz aqui, para a empresa ativa:
  * liga/desliga o robô em cada número de WhatsApp;
  * vê as conversas (aguardando humano primeiro), com a etapa do funil e o que o robô já coletou;
  * ASSUME a conversa (o robô para de responder), RESPONDE como atendente,
    DEVOLVE ao robô ou ENCERRA;
  * muda a etapa do funil (Contato → Qualificação → Simulação → Documentos → Contratação → Cliente/Perdido).

Versão e1 (etapa e, 29/10/2026): ao clicar em Assumir / Devolver ao robô / Encerrar
(ou ao responder), o painel já muda sozinho para a lista certa e mantém a mesma conversa aberta.

Segurança:
  * Usa a mesma chave de servidor do painel (nunca vai para o navegador).
  * O token do WhatsApp vem do secrets.toml (bloco [whatsapp]) e nunca aparece na tela.
  * Respostas só dentro da janela de 24 h e nunca para quem pediu para sair (SAIR/PARAR).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests
import streamlit as st

from banco import parse_data

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

STATUS_CONVERSA = {
    "waiting_human": "🟠 Aguardando humano",
    "human": "🙋 Com atendente",
    "bot": "🤖 Com o robô",
    "closed": "✅ Encerrada",
}
ETAPAS = {
    "new": "Contato", "qualifying": "Qualificação", "qualified": "Qualificado",
    "simulation": "Simulação", "documents": "Documentos", "contracting": "Contratação",
    "proposal": "Proposta", "negotiation": "Negociação", "won": "Cliente (fechou)", "lost": "Perdido",
}
ETAPAS_MENU = ["new", "qualifying", "simulation", "documents", "contracting", "won", "lost"]
ENTREGA = {"queued": "⏳", "sent": "✓", "delivered": "✓✓", "read": "✓✓ lida", "failed": "❌ falhou", "received": ""}
FILTROS = {
    "🟠 Aguardando humano": ["waiting_human"],
    "🙋 Com atendente": ["human"],
    "🤖 Com o robô": ["bot"],
    "Abertas (todas)": ["waiting_human", "human", "bot"],
    "✅ Encerradas": ["closed"],
}


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def _fuso(empresa: dict):
    try:
        return ZoneInfo(empresa.get("timezone") or "America/Sao_Paulo")
    except Exception:  # noqa: BLE001
        return timezone(timedelta(hours=-3))


def _hora(valor, fuso) -> str:
    d = parse_data(valor) if isinstance(valor, str) else valor
    if not d:
        return "—"
    return d.astimezone(fuso).strftime("%d/%m %H:%M")


def _ha_quanto(valor) -> str:
    d = parse_data(valor) if isinstance(valor, str) else valor
    if not d:
        return ""
    s = (datetime.now(timezone.utc) - d).total_seconds()
    if s < 60:
        return "agora"
    if s < 3600:
        return f"há {int(s // 60)} min"
    if s < 86400:
        return f"há {int(s // 3600)} h"
    return f"há {int(s // 86400)} d"


def _segredos_whatsapp() -> dict:
    try:
        return dict(st.secrets.get("whatsapp", {}))
    except Exception:  # noqa: BLE001
        return {}


def _limpo(texto: str, segredos: list[str]) -> str:
    for s in segredos:
        if s and len(s) >= 6:
            texto = texto.replace(s, "***")
    return texto[:400]


def _ir_para(filtro: str, conversa_id: str | None, aviso: str | None = None) -> None:
    """Depois de um botão: abre a lista certa com a mesma conversa selecionada (na próxima recarga)."""
    st.session_state["_atend_filtro_pendente"] = filtro
    if conversa_id:
        st.session_state["_atend_conv_pendente"] = conversa_id
    if aviso:
        st.session_state["_atend_aviso"] = aviso
    st.rerun()


def _nome_contato(ct: dict) -> str:
    nome = (ct.get("name") or "").strip() or "Sem nome"
    if ct.get("wa_id"):
        return f"{nome} (+{ct['wa_id']})"
    return f"{nome} (sem telefone, ID {str(ct.get('wa_user_id') or '')[:14]}…)"


# --------------------------------------------------------------------------
# Envio pela API do WhatsApp (resposta do atendente)
# --------------------------------------------------------------------------
def _enviar(sb, conversa: dict, contato: dict, conta: dict, texto: str) -> tuple[bool, str]:
    wa = _segredos_whatsapp()
    token = str(wa.get("token") or "").strip()
    versao = str(wa.get("graph_version") or "v26.0").strip()
    if not token:
        return False, "Falta o token do WhatsApp no secrets.toml (bloco [whatsapp], linha token)."
    if str(wa.get("phone_number_id") or "").strip() != str(conta.get("phone_number_id") or ""):
        return False, ("O token do secrets.toml é de outro número. Para responder por este número, "
                       "a empresa precisa ter o token dela cadastrado (etapa da agência multiempresa).")
    destino = {"to": contato["wa_id"]} if contato.get("wa_id") else {"recipient": contato.get("wa_user_id")}
    msg_id = sb.rpc("wa_agente_registrar_envio", {
        "p_conversation_id": conversa["id"], "p_sender": "human", "p_body": texto,
        "p_payload": {"tipo": "atendente"},
    }).execute().data
    try:
        r = requests.post(
            f"https://graph.facebook.com/{versao}/{conta['phone_number_id']}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"messaging_product": "whatsapp", "recipient_type": "individual", **destino,
                  "type": "text", "text": {"preview_url": False, "body": texto}},
            timeout=20)
        dados = r.json() if r.content else {}
    except Exception as e:  # noqa: BLE001
        dados, r = {"error": {"message": f"{type(e).__name__}"}}, None
    wamid = ((dados.get("messages") or [{}])[0] or {}).get("id")
    if r is not None and r.ok and wamid:
        sb.rpc("wa_agente_confirmar_envio", {"p_message_id": msg_id, "p_wamid": wamid, "p_erro": None}).execute()
        return True, "Mensagem enviada."
    erro = dados.get("error") or {}
    detalhe = _limpo(f"Meta {erro.get('code', '')}: {erro.get('message', 'erro')}", [token])
    sb.rpc("wa_agente_confirmar_envio", {"p_message_id": msg_id, "p_wamid": None, "p_erro": detalhe}).execute()
    if str(erro.get("code")) == "131047":
        detalhe += " — passou da janela de 24 h: só dá para falar com o cliente usando um modelo aprovado."
    return False, detalhe


# --------------------------------------------------------------------------
# Página
# --------------------------------------------------------------------------
def pagina(sb, empresa: dict, registrar_evento=None, erro_seguro=str) -> None:
    fuso = _fuso(empresa)
    tid = empresa["id"]
    st.title(f"💬 Atendimento — {empresa['name']}")
    st.caption("Conversas do WhatsApp. O robô atende primeiro; aqui a equipe assume, responde, devolve ao robô "
               "e acompanha a etapa do funil.")

    try:
        contas = (sb.table("whatsapp_accounts")
                  .select("id,label,display_phone_number,phone_number_id,status,agent_enabled")
                  .eq("tenant_id", tid).order("created_at").execute().data or [])
    except Exception as e:  # noqa: BLE001
        st.error("Não consegui ler o WhatsApp no Supabase. Confira se o SQL 006 foi rodado.")
        st.caption(erro_seguro(e))
        return
    if not contas:
        st.info("Esta empresa ainda não tem número de WhatsApp cadastrado.")
        return

    # ---- robô ligado/desligado por número ----------------------------------
    with st.expander("🤖 Robô por número", expanded=False):
        for c in contas:
            col1, col2 = st.columns([3, 1])
            col1.write(f"**{c['label']}** — {c.get('display_phone_number') or c['phone_number_id']}")
            novo = col2.toggle("Robô ligado", value=bool(c.get("agent_enabled")), key=f"robo_{c['id']}")
            if novo != bool(c.get("agent_enabled")):
                sb.table("whatsapp_accounts").update({"agent_enabled": novo}).eq("id", c["id"]).eq("tenant_id", tid).execute()
                if registrar_evento:
                    registrar_evento(tid, "whatsapp.agent_toggled",
                                     f"Robô {'ligado' if novo else 'desligado'} no número {c['label']}")
                st.rerun()
        st.caption("Desligado: as mensagens continuam chegando e ficam aqui para a equipe responder.")

    contas_por_id = {c["id"]: c for c in contas}

    # ---- lista de conversas -------------------------------------------------
    try:
        abertas = (sb.table("whatsapp_conversations").select("status")
                   .eq("tenant_id", tid).neq("status", "closed").execute().data or [])
    except Exception as e:  # noqa: BLE001
        st.error(f"Erro ao ler conversas: {erro_seguro(e)}")
        return
    cont = {s: sum(1 for a in abertas if a["status"] == s) for s in ("waiting_human", "human", "bot")}
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("🟠 Aguardando humano", cont["waiting_human"])
    m2.metric("🙋 Com atendente", cont["human"])
    m3.metric("🤖 Com o robô", cont["bot"])
    if m4.button("🔄 Atualizar", use_container_width=True):
        st.rerun()

    # lista escolhida: a que um botão pediu, ou a que estava aberta, ou (na 1ª vez) a mais útil
    pendente = st.session_state.pop("_atend_filtro_pendente", None)
    if pendente in FILTROS:
        st.session_state["filtro_atend"] = pendente
    elif st.session_state.get("filtro_atend") not in FILTROS:
        st.session_state["filtro_atend"] = "🟠 Aguardando humano" if cont["waiting_human"] else "Abertas (todas)"
    aviso = st.session_state.pop("_atend_aviso", None)
    if aviso:
        st.success(aviso)
    filtro = st.radio("Mostrar", list(FILTROS.keys()), horizontal=True, key="filtro_atend")
    convs = (sb.table("whatsapp_conversations")
             .select("id,status,handoff_reason,last_inbound_at,last_outbound_at,updated_at,ai_state,contact_id,"
                     "whatsapp_account_id,closed_at")
             .eq("tenant_id", tid).in_("status", FILTROS[filtro])
             .order("updated_at", desc=True).limit(60).execute().data or [])
    if not convs:
        st.info("Nenhuma conversa neste filtro.")
        return
    ids_ct = list({c["contact_id"] for c in convs})
    contatos = {c["id"]: c for c in (sb.table("contacts")
                                      .select("id,name,wa_id,wa_user_id,funnel_stage,opt_out_at")
                                      .eq("tenant_id", tid).in_("id", ids_ct).execute().data or [])}

    def rotulo(cid: str) -> str:
        c = next(x for x in convs if x["id"] == cid)
        ct = contatos.get(c["contact_id"], {})
        etapa = ETAPAS.get(ct.get("funnel_stage"), ct.get("funnel_stage") or "")
        return (f"{STATUS_CONVERSA.get(c['status'], c['status'])} · {_nome_contato(ct)} · {etapa} · "
                f"{_ha_quanto(c.get('last_inbound_at') or c.get('updated_at'))}")

    ids_lista = [c["id"] for c in convs]
    conv_pendente = st.session_state.pop("_atend_conv_pendente", None)
    if conv_pendente in ids_lista:
        st.session_state["conv_atend"] = conv_pendente
    elif st.session_state.get("conv_atend") not in ids_lista:
        st.session_state.pop("conv_atend", None)
    escolhida = st.selectbox("Conversa", ids_lista, format_func=rotulo, key="conv_atend")
    conv = next(c for c in convs if c["id"] == escolhida)
    ct = contatos.get(conv["contact_id"], {})
    conta = contas_por_id.get(conv["whatsapp_account_id"], {})

    st.markdown("---")
    cab1, cab2 = st.columns([3, 2])
    with cab1:
        st.subheader(_nome_contato(ct))
        st.write(f"**Situação:** {STATUS_CONVERSA.get(conv['status'], conv['status'])} · "
                 f"**Número:** {conta.get('label', '?')}")
        if conv.get("handoff_reason") and conv["status"] in ("waiting_human", "human"):
            st.write(f"**Motivo da passagem:** {conv['handoff_reason']}")
        if ct.get("opt_out_at"):
            st.warning("Este cliente pediu para NÃO receber mensagens (SAIR/PARAR). Ele volta se mandar VOLTAR.")
    with cab2:
        etapa_atual = ct.get("funnel_stage") or "new"
        opcoes = ETAPAS_MENU if etapa_atual in ETAPAS_MENU else [etapa_atual] + ETAPAS_MENU
        nova_etapa = st.selectbox("Etapa do funil", opcoes, index=opcoes.index(etapa_atual),
                                  format_func=lambda k: ETAPAS.get(k, k), key=f"etapa_{conv['id']}")
        if nova_etapa != etapa_atual:
            sb.table("contacts").update({"funnel_stage": nova_etapa}).eq("id", ct["id"]).eq("tenant_id", tid).execute()
            if registrar_evento:
                registrar_evento(tid, "contact.stage_changed",
                                 f"Etapa do funil mudada pela equipe para {ETAPAS.get(nova_etapa, nova_etapa)}")
            st.rerun()

    dados = (conv.get("ai_state") or {}).get("dados") or {}
    produto = (conv.get("ai_state") or {}).get("produto_interesse")
    if dados or produto:
        with st.expander("📋 O que o robô já coletou", expanded=conv["status"] == "waiting_human"):
            if produto:
                st.write(f"**Interesse:** {produto}")
            for k, v in dados.items():
                st.write(f"**{k.replace('_', ' ').capitalize()}:** {v}")

    b1, b2, b3 = st.columns(3)
    if conv["status"] != "human" and conv["status"] != "closed":
        if b1.button("🙋 Assumir (eu respondo)", use_container_width=True, type="primary"):
            sb.table("whatsapp_conversations").update({"status": "human"}).eq("id", conv["id"]).eq("tenant_id", tid).execute()
            if registrar_evento:
                registrar_evento(tid, "whatsapp.human_took", "Equipe assumiu a conversa pelo painel",
                                 entidade_tipo="whatsapp_conversation", entidade_id=conv["id"])
            _ir_para("🙋 Com atendente", conv["id"], "Você assumiu a conversa: o robô parou de responder. Escreva abaixo.")
    if conv["status"] in ("human", "waiting_human"):
        if b2.button("🤖 Devolver ao robô", use_container_width=True):
            sb.table("whatsapp_conversations").update({"status": "bot", "handoff_reason": None}) \
                .eq("id", conv["id"]).eq("tenant_id", tid).execute()
            if registrar_evento:
                registrar_evento(tid, "whatsapp.back_to_bot", "Equipe devolveu a conversa ao robô",
                                 entidade_tipo="whatsapp_conversation", entidade_id=conv["id"])
            _ir_para("🤖 Com o robô", conv["id"], "Conversa devolvida: o robô volta a responder na próxima mensagem do cliente.")
    if conv["status"] != "closed":
        if b3.button("✅ Encerrar conversa", use_container_width=True):
            sb.table("whatsapp_conversations").update({"status": "closed", "closed_at": datetime.now(timezone.utc).isoformat()}) \
                .eq("id", conv["id"]).eq("tenant_id", tid).execute()
            _ir_para("✅ Encerradas", conv["id"], "Conversa encerrada.")

    if conv["status"] in ("human", "waiting_human"):
        with st.expander("Conclusão confirmada e outras soluções"):
            st.caption("Marque somente depois de confirmar a conclusão real com o parceiro. Isso não assina, contrata, encerra a conversa nem liga o robô.")
            confirmado = st.checkbox("Confirmei com o parceiro que a operação foi concluída", key=f"conclusao_real_{conv['id']}")
            if st.button("Registrar conclusão confirmada", disabled=not confirmado, key=f"registrar_conclusao_{conv['id']}"):
                estado_atual = dict(conv.get("ai_state") or {})
                estado_atual.update(operacao_concluida=True, conclusao_origem="equipe_painel", conclusao_confirmada_em=datetime.now(timezone.utc).isoformat())
                alterado = sb.table("whatsapp_conversations").update({"ai_state": estado_atual}).eq("id", conv["id"]).eq("tenant_id", tid).eq("status", conv["status"]).eq("updated_at", conv["updated_at"]).execute()
                if alterado.data:
                    if registrar_evento:
                        registrar_evento(tid, "whatsapp.operation_completion_confirmed", "Equipe registrou conclusão real para orientar encerramento e oferta complementar única", entidade_tipo="whatsapp_conversation", entidade_id=conv["id"])
                    st.success("Conclusão registrada. Quando o atendimento voltar ao robô, ele poderá perguntar uma vez sobre outras soluções na próxima resposta apropriada.")
                else:
                    st.warning("A conversa mudou. Atualize a tela antes de registrar a conclusão.")

    # ---- mensagens ----------------------------------------------------------
    msgs = (sb.table("whatsapp_messages")
            .select("id,direction,sender,message_type,body,delivery_status,error,created_at,ai_model")
            .eq("tenant_id", tid).eq("conversation_id", conv["id"])
            .order("created_at", desc=True).limit(60).execute().data or [])
    msgs.reverse()
    with st.container(height=420):
        for m in msgs:
            if m["direction"] == "inbound":
                with st.chat_message("user", avatar="👤"):
                    corpo = m.get("body") or ""
                    if m["message_type"] not in ("text", "button", "interactive"):
                        corpo = f"[{m['message_type']}] {corpo}".strip()
                    st.write(corpo or "—")
                    st.caption(_hora(m["created_at"], fuso))
            else:
                quem = {"ai": "🤖 Robô", "system": "🤖 Robô (frase fixa)", "human": "🙋 Atendente"}.get(m["sender"], m["sender"])
                with st.chat_message("assistant", avatar="🤖" if m["sender"] != "human" else "🙋"):
                    st.write(m.get("body") or "—")
                    status = ENTREGA.get(m.get("delivery_status"), m.get("delivery_status") or "")
                    st.caption(f"{quem} · {_hora(m['created_at'], fuso)} {status}"
                               + (f" · {m['error']}" if m.get("delivery_status") == "failed" and m.get("error") else ""))

    # ---- resposta do atendente ---------------------------------------------
    janela = parse_data(conv.get("last_inbound_at")) if conv.get("last_inbound_at") else None
    aberta = bool(janela and datetime.now(timezone.utc) - janela < timedelta(hours=24))
    if conv["status"] == "closed":
        st.caption("Conversa encerrada. Se o cliente escrever de novo, abre uma conversa nova.")
        return
    if ct.get("opt_out_at"):
        return
    if not aberta:
        st.info("A janela de 24 h desta conversa fechou: o WhatsApp só deixa falar com o cliente usando um "
                "modelo de mensagem aprovado (fica para uma próxima etapa).")
        return
    fecha = janela + timedelta(hours=24)
    st.caption(f"Janela de 24 h aberta até {_hora(fecha, fuso)}.")
    with st.form(key=f"resp_{conv['id']}", clear_on_submit=True):
        texto = st.text_area("Responder como atendente", max_chars=1500, height=100,
                             placeholder="Escreva a resposta para o cliente…")
        enviar = st.form_submit_button("📤 Enviar", type="primary")
    if enviar:
        texto = (texto or "").strip()
        if not texto:
            st.warning("Escreva a mensagem antes de enviar.")
            return
        if conv["status"] != "human":
            # quem responde assume a conversa: o robô para de responder
            sb.table("whatsapp_conversations").update({"status": "human"}).eq("id", conv["id"]).eq("tenant_id", tid).execute()
        ok, msg = _enviar(sb, conv, ct, conta, texto)
        if ok:
            if registrar_evento:
                registrar_evento(tid, "whatsapp.human_reply", "Atendente respondeu pelo painel",
                                 entidade_tipo="whatsapp_conversation", entidade_id=conv["id"])
            if conv["status"] != "human":
                _ir_para("🙋 Com atendente", conv["id"], "Mensagem enviada. Você assumiu a conversa.")
            st.success(msg)
            st.rerun()
        else:
            st.error(f"Não foi enviada. {msg}")
