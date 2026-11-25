# -*- coding: utf-8 -*-
"""
preparar_webhook.py — prepara os 2 segredos do webhook do WhatsApp (aberto pelo PREPARAR_WEBHOOK.bat).

1. Cria (uma vez só) o TOKEN DE VERIFICAÇÃO do webhook e guarda no bloco [whatsapp]
   do .streamlit/secrets.toml, na linha webhook_verify_token (faz cópia de segurança antes).
2. Copia para a área de transferência, um de cada vez, o que você vai colar:
   - no Supabase: WHATSAPP_VERIFY_TOKEN e WHATSAPP_APP_SECRET;
   - na Meta: o token de verificação.
3. No fim, limpa a área de transferência.

Nenhuma chave aparece inteira na tela. Não acessa a internet.
"""
import re
import secrets
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQ = PASTA / ".streamlit" / "secrets.toml"
BACKUP = PASTA / ".streamlit" / "secrets.toml.antes_webhook.bak"


def copiar(texto: str) -> bool:
    """Copia para a área de transferência do Windows (sem mostrar na tela)."""
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Set-Clipboard -Value ([Console]::In.ReadToEnd().TrimEnd())"],
                       input=texto, text=True, check=True, timeout=20,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except Exception:  # noqa: BLE001
        try:
            subprocess.run("clip", input=texto.encode("utf-16-le"), shell=True, check=True, timeout=20)
            return True
        except Exception:  # noqa: BLE001
            return False


def limpar_area() -> None:
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ' '"],
                       check=False, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:  # noqa: BLE001
        pass


def esperar(msg: str) -> None:
    try:
        input(msg)
    except EOFError:
        pass


def gravar_token(texto: str, token: str) -> str:
    """Põe 'webhook_verify_token = "..."' logo abaixo da linha [whatsapp]."""
    linhas = texto.splitlines(keepends=True)
    for i, linha in enumerate(linhas):
        if re.match(r"^\s*\[whatsapp\]\s*(#.*)?$", linha):
            nl = "\r\n" if linha.endswith("\r\n") else "\n"
            if not linha.endswith(("\n", "\r\n")):
                linhas[i] = linha + nl
            linhas.insert(i + 1, f'webhook_verify_token = "{token}"{nl}')
            return "".join(linhas)
    raise RuntimeError("Não achei a linha [whatsapp] no secrets.toml.")


def main() -> int:
    print("=" * 64)
    print("  PREPARAR O WEBHOOK DO WHATSAPP (segredos para o Supabase e a Meta)")
    print("=" * 64)

    if not ARQ.exists():
        print("  [ERRO] Não achei o .streamlit/secrets.toml desta pasta.")
        return 1
    bruto = ARQ.read_text(encoding="utf-8")
    try:
        seg = tomllib.loads(bruto)
    except Exception as e:  # noqa: BLE001
        print(f"  [ERRO] O secrets.toml está com erro de formatação ({type(e).__name__}). Nada foi mudado.")
        return 1
    wa = seg.get("whatsapp")
    if not isinstance(wa, dict):
        print("  [ERRO] Não achei o bloco [whatsapp] no secrets.toml (faça antes a Parte E do checklist).")
        return 1
    app_secret = str(wa.get("app_secret", "")).strip()
    if not app_secret or "COLE-" in app_secret:
        print("  [ERRO] O app_secret do bloco [whatsapp] está vazio.")
        return 1

    token = str(wa.get("webhook_verify_token", "")).strip()
    if token:
        print("  [OK] Token de verificação já existia no secrets.toml (vou reutilizar o mesmo).")
    else:
        token = secrets.token_urlsafe(32)  # 43 letras/números aleatórios
        shutil.copy2(ARQ, BACKUP)
        novo = gravar_token(bruto, token)
        try:
            conferido = tomllib.loads(novo)
            assert conferido["whatsapp"]["webhook_verify_token"] == token
        except Exception:  # noqa: BLE001
            print("  [ERRO] Não consegui gravar o token com segurança. Nada foi mudado.")
            return 1
        ARQ.write_text(novo, encoding="utf-8")
        print("  [OK] Token de verificação NOVO criado e guardado no secrets.toml")
        print(f"       (cópia de segurança: {BACKUP.name}).")
    print(f"       Começa com: {token[:4]}...  (não precisa anotar)")
    print()

    passos = [
        (token, "PASSO 1 — Supabase → Edge Functions → Secrets → Add new secret\n"
                "          Name:  WHATSAPP_VERIFY_TOKEN\n"
                "          Value: aperte Ctrl+V  (o token de verificação já está copiado)"),
        (app_secret, "PASSO 2 — Supabase → Add another / Add new secret\n"
                     "          Name:  WHATSAPP_APP_SECRET\n"
                     "          Value: aperte Ctrl+V  (a chave secreta do app já está copiada)\n"
                     "          Depois clique em Save (ou Bulk save)."),
        (token, "PASSO 3 — Meta → app Atendimento Cred Finance → WhatsApp → Configuração → Webhook\n"
                "          Campo 'Verificar token' / 'Verify token': aperte Ctrl+V\n"
                "          (o token de verificação foi copiado de novo)"),
    ]
    for valor, texto in passos:
        if not copiar(valor):
            print("  [ERRO] Não consegui usar a área de transferência do Windows.")
            print("         Feche esta janela e me avise (não copie as chaves à mão).")
            return 1
        print(texto)
        esperar("          Quando terminar este passo, aperte Enter... ")
        print()

    limpar_area()
    print("  [OK] Área de transferência limpa. Pronto: pode seguir o checklist.")
    return 0


if __name__ == "__main__":
    rc = main()
    esperar("\nAperte Enter para fechar...")
    sys.exit(rc)
