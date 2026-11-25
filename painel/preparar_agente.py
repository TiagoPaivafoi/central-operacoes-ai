# -*- coding: utf-8 -*-
"""
preparar_agente.py — copia os 2 segredos novos do agente (aberto pelo PREPARAR_AGENTE.bat). Etapa (d).

Copia para a área de transferência, um de cada vez, o que você vai colar no
Supabase → Edge Functions → Secrets:
  - WHATSAPP_TOKEN  (linha "token" do bloco [whatsapp] do secrets.toml)
  - GEMINI_API_KEY  (linha GEMINI_API_KEY do secrets.toml, a chave de final J4jA)
No fim, limpa a área de transferência.

Não muda nada no secrets.toml. Nenhuma chave aparece inteira na tela. Não acessa a internet.
"""
import subprocess
import sys
import tomllib
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQ = PASTA / ".streamlit" / "secrets.toml"


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


def main() -> int:
    print("=" * 66)
    print("  PREPARAR O AGENTE DO WHATSAPP (2 segredos novos para o Supabase)")
    print("=" * 66)
    if not ARQ.exists():
        print("  [ERRO] Não achei o .streamlit/secrets.toml desta pasta.")
        return 1
    try:
        seg = tomllib.loads(ARQ.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"  [ERRO] O secrets.toml está com erro de formatação ({type(e).__name__}).")
        return 1
    wa = seg.get("whatsapp") or {}
    token = str(wa.get("token", "")).strip()
    gemini = str(seg.get("GEMINI_API_KEY", "")).strip()
    if not token or "COLE" in token.upper():
        print("  [ERRO] A linha token do bloco [whatsapp] está vazia.")
        return 1
    if not gemini or "COLE" in gemini.upper():
        print("  [ERRO] A linha GEMINI_API_KEY está vazia.")
        return 1
    print(f"  [OK] Token do WhatsApp encontrado (termina em ...{token[-4:]}).")
    print(f"  [OK] Chave do Gemini encontrada (termina em ...{gemini[-4:]}).")
    print()

    passos = [
        (token, "PASSO 1 — Supabase → Edge Functions → Secrets → Add new secret\n"
                "          Name:  WHATSAPP_TOKEN\n"
                "          Value: aperte Ctrl+V  (o token do WhatsApp já está copiado)"),
        (gemini, "PASSO 2 — Add another (ou Add new secret)\n"
                 "          Name:  GEMINI_API_KEY\n"
                 "          Value: aperte Ctrl+V  (a chave do Gemini já está copiada)\n"
                 "          Depois clique em Save (ou Bulk save)."),
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
