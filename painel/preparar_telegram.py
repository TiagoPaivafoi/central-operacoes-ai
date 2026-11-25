# -*- coding: utf-8 -*-
"""
preparar_telegram.py — Central de Operações AI, etapa (e): aviso para a equipe no Telegram.

O que faz (rode pelo PREPARAR_TELEGRAM.bat):
  1. Pede o TOKEN do robô do Telegram (criado no @BotFather). Você cola com o botão direito
     ou Ctrl+V; ele NÃO aparece na tela e NÃO é gravado em nenhum arquivo.
  2. Confere o token no Telegram e acha o grupo onde você mandou /start para o robô.
  3. Manda uma mensagem de teste no grupo.
  4. Copia para a área de transferência, um de cada vez, os 2 valores que você cola no
     Supabase (Edge Functions → Secrets): TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID.
     No fim, limpa a área de transferência.

Segurança: nada é salvo em disco; o token só aparece como "...final XXXX".
"""
from __future__ import annotations

import getpass
import subprocess
import sys

try:
    import requests
except ImportError:  # pragma: no cover
    print("Falta a biblioteca 'requests'. Rode o INICIAR.bat uma vez e tente de novo.")
    input("Enter para sair...")
    sys.exit(1)

API = "https://api.telegram.org/bot{token}/{metodo}"


def copiar(texto: str) -> bool:
    """Copia para a área de transferência do Windows (sem mostrar na tela)."""
    try:
        subprocess.run(["clip"], input=texto.encode("utf-16-le"), check=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def limpar_area() -> None:
    try:
        subprocess.run(["cmd", "/c", "echo off | clip"], check=False)
    except Exception:  # noqa: BLE001
        pass


def chamar(token: str, metodo: str, **dados) -> dict:
    try:
        r = requests.post(API.format(token=token, metodo=metodo), json=dados or None, timeout=20)
        return r.json() if r.content else {"ok": False, "description": f"HTTP {r.status_code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "description": type(e).__name__}


def main() -> None:
    print("=" * 66)
    print(" Aviso da equipe no Telegram — preparar")
    print("=" * 66)
    print("Antes: crie o robô no @BotFather, crie o grupo, adicione o robô ao grupo")
    print("e mande no grupo:  /start@usuario_do_seu_bot\n")

    token = getpass.getpass("Cole o TOKEN do robô (não aparece na tela) e tecle Enter: ").strip()
    if not token or ":" not in token:
        print("\nIsso não parece um token do Telegram (formato 123456:ABC...).")
        input("Enter para sair...")
        return
    final = token[-4:]

    eu = chamar(token, "getMe")
    if not eu.get("ok"):
        print(f"\nO Telegram recusou o token (...final {final}): {eu.get('description', 'erro')}")
        print("Confira se copiou o token inteiro no @BotFather.")
        input("Enter para sair...")
        return
    usuario_bot = eu["result"].get("username", "?")
    print(f"\nToken OK (...final {final}). Robô: @{usuario_bot}")

    atualizacoes = chamar(token, "getUpdates", allowed_updates=["message", "my_chat_member"])
    grupos: dict[str, str] = {}
    for u in atualizacoes.get("result", []) or []:
        for chave in ("message", "my_chat_member"):
            chat = (u.get(chave) or {}).get("chat") or {}
            if chat.get("type") in ("group", "supergroup"):
                grupos[str(chat["id"])] = chat.get("title") or "(grupo sem nome)"
    if not grupos:
        print("\nNão achei nenhum grupo. Confira:")
        print(f"  1) o robô @{usuario_bot} está no grupo;")
        print(f"  2) você mandou no grupo:  /start@{usuario_bot}")
        print("  3) espere 10 segundos e rode este arquivo de novo.")
        input("Enter para sair...")
        return

    lista = list(grupos.items())
    if len(lista) == 1:
        chat_id, titulo = lista[0]
    else:
        print("\nAchei estes grupos:")
        for i, (_, t) in enumerate(lista, 1):
            print(f"  {i}) {t}")
        escolha = input("Número do grupo da equipe: ").strip()
        try:
            chat_id, titulo = lista[int(escolha) - 1]
        except Exception:  # noqa: BLE001
            print("Número inválido.")
            input("Enter para sair...")
            return
    print(f"Grupo: {titulo}")

    teste = chamar(token, "sendMessage", chat_id=chat_id,
                   text="✅ Teste: os avisos de 'cliente aguardando atendimento' do WhatsApp da "
                        "Cred Finance vão chegar aqui.")
    if not teste.get("ok"):
        print(f"\nNão consegui mandar a mensagem de teste: {teste.get('description', 'erro')}")
        input("Enter para sair...")
        return
    print("Mensagem de teste enviada no grupo. Confira no celular.\n")

    print("-" * 66)
    print("Agora no Supabase (Chrome 'Trabalho'): Edge Functions → Secrets → Add new secret")
    print("-" * 66)
    if copiar(token):
        print("1) COPIEI O TOKEN. Name: TELEGRAM_BOT_TOKEN   Value: Ctrl+V")
    else:
        print("1) Não consegui copiar sozinho. Cole o token do @BotFather como TELEGRAM_BOT_TOKEN.")
    input("   Depois de colar, tecle Enter aqui...")
    if copiar(chat_id):
        print(f"2) COPIEI O NÚMERO DO GRUPO ({chat_id}). Name: TELEGRAM_CHAT_ID   Value: Ctrl+V")
    else:
        print(f"2) Name: TELEGRAM_CHAT_ID   Value: {chat_id}")
    input("   Clique em Save no Supabase e tecle Enter aqui...")
    limpar_area()
    print("\nPronto! Área de transferência limpa. Os avisos começam na próxima conversa")
    print("que for para 'aguardando humano' (não precisa publicar a função de novo).")
    input("Enter para sair...")


if __name__ == "__main__":
    main()
