# -*- coding: utf-8 -*-
"""
testar_whatsapp_config.py — confere se o bloco [whatsapp] do secrets.toml está certo.

Só faz CONSULTAS na Meta (não envia mensagem, não muda nada, não gasta nada):
  1. o bloco [whatsapp] existe e tem todos os campos;
  2. o token é válido, é do app certo, não expira e tem só as 2 permissões do WhatsApp
     (esta conferência também prova que a chave secreta do app está certa);
  3. o número de teste responde com esse token;
  4. o número pertence à conta do WhatsApp (WABA) informada.
Nenhuma chave aparece na tela.
"""
import sys
import tomllib
from pathlib import Path

import requests

PASTA = Path(__file__).resolve().parent
CAMPOS = ("app_id", "app_secret", "waba_id", "phone_number_id", "token", "graph_version")
PERMISSOES = {"whatsapp_business_messaging", "whatsapp_business_management"}


def main() -> int:
    resultados: list[tuple[bool, str]] = []
    segredos: list[str] = []

    def limpo(t: str) -> str:
        for s in segredos:
            if s:
                t = t.replace(s, "***")
        return t

    def marcar(ok: bool, texto: str) -> None:
        resultados.append((ok, texto))
        print(f"  [{'OK' if ok else 'ERRO'}] {limpo(texto)}", flush=True)

    print("=" * 60)
    print("  TESTE DA CONFIGURAÇÃO DO WHATSAPP (só consulta, custo zero)")
    print("=" * 60)

    caminho = PASTA / ".streamlit" / "secrets.toml"
    try:
        with open(caminho, "rb") as f:
            seg = tomllib.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"  [ERRO] Não consegui ler o secrets.toml: {type(e).__name__}. "
              "Confira se as aspas do bloco [whatsapp] estão certas.")
        return 1

    wa = seg.get("whatsapp")
    if not isinstance(wa, dict):
        marcar(False, "Não achei o bloco [whatsapp] no secrets.toml.")
        return 1
    faltando = [c for c in CAMPOS if not str(wa.get(c, "")).strip() or "COLE-" in str(wa.get(c, ""))]
    marcar(not faltando, "Bloco [whatsapp] completo" if not faltando
           else f"Campos vazios ou ainda com o texto de exemplo: {', '.join(faltando)}")
    if faltando:
        return 1

    app_id, app_secret = str(wa["app_id"]).strip(), str(wa["app_secret"]).strip()
    waba_id, phone_id = str(wa["waba_id"]).strip(), str(wa["phone_number_id"]).strip()
    token, versao = str(wa["token"]).strip(), str(wa["graph_version"]).strip()
    segredos.extend([token, app_secret])
    base = f"https://graph.facebook.com/{versao}"

    def consultar(caminho_api: str, params: dict | None = None, com_token: bool = True) -> dict:
        cab = {"Authorization": f"Bearer {token}"} if com_token else {}
        r = requests.get(f"{base}/{caminho_api}", params=params or {}, headers=cab, timeout=30)
        try:
            dados = r.json()
        except ValueError:
            dados = {"error": {"message": f"resposta inesperada (HTTP {r.status_code})"}}
        if r.status_code >= 400 or "error" in dados:
            msg = (dados.get("error") or {}).get("message", f"HTTP {r.status_code}")
            raise RuntimeError(limpo(str(msg))[:300])
        return dados

    # 2. Token (usa a chave secreta do app para perguntar à Meta)
    try:
        info = consultar("debug_token", {"input_token": token, "access_token": f"{app_id}|{app_secret}"},
                         com_token=False).get("data", {})
        marcar(bool(info.get("is_valid")), "Token válido" if info.get("is_valid") else "Token inválido")
        marcar(str(info.get("app_id")) == app_id,
               "Token pertence ao app Atendimento Cred Finance" if str(info.get("app_id")) == app_id
               else "Token é de OUTRO app (gere de novo escolhendo Atendimento Cred Finance)")
        expira = info.get("expires_at", 0)
        marcar(expira in (0, None), "Token não expira" if expira in (0, None)
               else "Token com data de validade (gere de novo com Expiração: Nunca)")
        escopos = set(info.get("scopes") or [])
        extras, faltam = escopos - PERMISSOES - {"public_profile"}, PERMISSOES - escopos  # public_profile é padrão e inofensivo
        marcar(not extras and not faltam,
               "Token só com as 2 permissões do WhatsApp" if not extras and not faltam
               else f"Permissões a mais: {sorted(extras) or '-'}; faltando: {sorted(faltam) or '-'}")
        marcar(True, "Chave secreta do app aceita pela Meta")
    except Exception as e:  # noqa: BLE001
        marcar(False, f"Não consegui conferir o token. Pode ser a chave secreta do app errada ou o token "
                      f"errado: {limpo(str(e))}")

    # 3. Número de teste
    try:
        num = consultar(phone_id, {"fields": "display_phone_number,verified_name"})
        marcar(True, f"Número encontrado: {num.get('display_phone_number')} ({num.get('verified_name')})")
    except Exception as e:  # noqa: BLE001
        marcar(False, f"Número não respondeu com esse token: {limpo(str(e))}")

    # 4. Número pertence à WABA
    try:
        lista = consultar(f"{waba_id}/phone_numbers", {"fields": "id"}).get("data", [])
        dentro = any(str(n.get("id")) == phone_id for n in lista)
        marcar(dentro, "Número pertence à conta do WhatsApp informada" if dentro
               else "O phone_number_id não está nessa conta (waba_id). Confira os dois números.")
    except Exception as e:  # noqa: BLE001
        marcar(False, f"Não consegui ler a conta do WhatsApp (waba_id): {limpo(str(e))}")

    ok = all(r[0] for r in resultados)
    print()
    print("RESULTADO: " + ("CONFIGURAÇÃO APROVADA" if ok else "HÁ PROBLEMAS — tire um print desta tela e envie ao Claude"))
    try:
        (PASTA / "whatsapp_config_relatorio.txt").write_text(
            "\n".join(f"[{'OK' if a else 'ERRO'}] {limpo(b)}" for a, b in resultados)
            + "\nRESULTADO: " + ("APROVADA" if ok else "REPROVADA") + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    rc = main()
    try:
        input("\nAperte Enter para fechar...")
    except EOFError:
        pass
    sys.exit(rc)
