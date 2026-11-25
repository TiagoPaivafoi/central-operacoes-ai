# -*- coding: utf-8 -*-
"""
configurar.py — janela simples para preencher as chaves do painel.

O que faz:
  * Mostra uma janela com os campos necessarios (voce so cola/digita).
  * Aproveita o que ja estiver no .streamlit/secrets.toml (mesmo se estiver bagunçado).
  * Grava um secrets.toml limpo, no formato certo, e guarda uma copia do antigo (.bak).

Seguranca: nada e enviado para a internet e nenhum valor e mostrado em tela ou impresso.
"""
import re
import sys
import time
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ARQUIVO = PASTA / ".streamlit" / "secrets.toml"

# (nome, rotulo, dica, obrigatorio)
CAMPOS = [
    ("GEMINI_API_KEY", "Chave do Gemini (Google AI Studio)",
     "A mesma que voce ja usava no projeto.", True),
    ("SUPABASE_URL", "Endereco do projeto no Supabase",
     "Comeca com https:// e termina com .supabase.co", True),
    ("SUPABASE_SERVICE_ROLE_KEY", "Chave secreta do Supabase (service_role)",
     "Supabase > Project Settings > API Keys. Nunca compartilhe.", True),
    ("APP_PASSWORD", "Senha para entrar no painel",
     "Voce inventa. Minimo de 12 caracteres, com letras, numeros e simbolos.", True),
    ("N8N_TOKEN", "Novo token do n8n",
     "Deixe em branco por enquanto, se nao tiver.", False),
    ("META_SYSTEM_TOKEN", "Token da Meta (usuario do sistema n8n-bot)",
     "Business Suite > Usuarios do sistema > Gerar token. Cole so aqui, nunca no chat.", False),
]
NOMES = [c[0] for c in CAMPOS]
EXEMPLOS = ("cole-", "SEU-PROJETO", "xxxxxxxx", "defina-uma-senha", "uma-senha-longa")


def ler_existente(arquivo: Path = ARQUIVO) -> dict:
    """Le NOME = "valor" linha a linha. Se um nome se repetir, vale o ultimo com valor real."""
    valores = {}
    if not arquivo.exists():
        return valores
    padrao = re.compile(r'^\s*([A-Z0-9_]+)\s*=\s*"(.*)"\s*(?:#.*)?$')
    for linha in arquivo.read_text(encoding="utf-8", errors="replace").splitlines():
        m = padrao.match(linha)
        if not m or m.group(1) not in NOMES:
            continue
        valor = m.group(2).replace('\\"', '"').replace("\\\\", "\\").strip()
        if not valor or any(e in valor for e in EXEMPLOS):
            continue  # valor de exemplo = nao conta
        valores[m.group(1)] = valor
    return valores


def validar(valores: dict) -> list:
    erros = []
    for nome, rotulo, _, obrigatorio in CAMPOS:
        v = (valores.get(nome) or "").strip()
        if obrigatorio and not v:
            erros.append(f"Falta preencher: {rotulo}.")
        if v and any(ord(ch) < 32 for ch in v):
            erros.append(f"{rotulo}: contem quebra de linha ou caractere invalido (copie de novo).")
    url = (valores.get("SUPABASE_URL") or "").strip()
    if url and not re.fullmatch(r"https://[A-Za-z0-9.-]+/?", url):
        erros.append("O endereco do Supabase deve comecar com https:// e nao pode ter espacos.")
    if url and "SEU-PROJETO" in url:
        erros.append("O endereco do Supabase ainda esta com o texto de exemplo.")
    senha = (valores.get("APP_PASSWORD") or "").strip()
    if senha and len(senha) < 12:
        erros.append("A senha do painel precisa ter pelo menos 12 caracteres.")
    return erros


def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"')


def gravar(valores: dict, arquivo: Path = ARQUIVO) -> None:
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    if arquivo.exists():  # guarda o antigo
        copia = arquivo.with_name(arquivo.name + ".bak")
        copia.write_bytes(arquivo.read_bytes())
    linhas = ["# Gerado por configurar.py. Nao envie este arquivo a ninguem.", ""]
    for nome, *_ in CAMPOS:
        v = (valores.get(nome) or "").strip()
        if nome == "SUPABASE_URL":
            v = v.rstrip("/")
        if v:
            linhas.append(f'{nome} = "{_esc(v)}"')
    arquivo.write_text("\n".join(linhas) + "\n", encoding="utf-8")


def abrir_janela() -> None:
    import tkinter as tk
    from tkinter import messagebox

    atuais = ler_existente()
    # Endereco do projeto da Cred Finance (nao e segredo): ja vem preenchido.
    atuais.setdefault("SUPABASE_URL", "https://SEU-PROJETO.supabase.co")
    raiz = tk.Tk()
    raiz.title("Configurar o painel — Central de Operacoes AI")
    raiz.resizable(True, True)

    # Barra de botoes FIXA embaixo: aparece sempre, mesmo em telas pequenas.
    barra = tk.Frame(raiz)
    barra.pack(side="bottom", fill="x", pady=10)

    topo = tk.Frame(raiz)
    topo.pack(side="top", fill="x")
    tk.Label(topo, text="Preencha os campos abaixo e clique em Salvar.",
             font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=18, pady=(14, 2))
    tk.Label(topo, text="Campos ja preenchidos aparecem com pontinhos: deixe como estao para manter.",
             font=("Segoe UI", 9), fg="#555").pack(anchor="w", padx=18)

    mostrar = tk.BooleanVar(value=False)
    rodape = tk.Frame(raiz)
    rodape.pack(side="bottom", fill="x")

    # Area dos campos com rolagem (barra lateral + roda do mouse)
    meio = tk.Frame(raiz)
    meio.pack(side="top", fill="both", expand=True)
    tela = tk.Canvas(meio, highlightthickness=0)
    rolagem = tk.Scrollbar(meio, orient="vertical", command=tela.yview)
    tela.configure(yscrollcommand=rolagem.set)
    rolagem.pack(side="right", fill="y")
    tela.pack(side="left", fill="both", expand=True)
    quadro = tk.Frame(tela)
    janela_interna = tela.create_window((0, 0), window=quadro, anchor="nw")
    quadro.bind("<Configure>", lambda _e: tela.configure(scrollregion=tela.bbox("all")))
    tela.bind("<Configure>", lambda e: tela.itemconfigure(janela_interna, width=e.width))
    tela.bind_all("<MouseWheel>", lambda e: tela.yview_scroll(int(-1 * (e.delta / 120)), "units"))

    entradas = {}
    for nome, rotulo, dica, obrigatorio in CAMPOS:
        tk.Label(quadro, text=rotulo + ("" if obrigatorio else "  (opcional)"),
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=18, pady=(8, 0))
        e = tk.Entry(quadro, width=80, show="•", font=("Consolas", 10))
        if nome in atuais:
            e.insert(0, atuais[nome])
        e.pack(anchor="w", padx=18)
        tk.Label(quadro, text=dica, font=("Segoe UI", 8), fg="#666").pack(anchor="w", padx=18)
        entradas[nome] = e

    def alternar():
        for e in entradas.values():
            e.config(show="" if mostrar.get() else "•")

    tk.Checkbutton(rodape, text="Mostrar o que digitei", variable=mostrar,
                   command=alternar).pack(anchor="w", padx=18)

    def salvar():
        valores = {n: e.get().strip() for n, e in entradas.items()}
        erros = validar(valores)
        if erros:
            messagebox.showwarning("Falta ajustar", "\n\n".join(erros))
            return
        try:
            gravar(valores)
        except Exception as ex:  # noqa: BLE001
            messagebox.showerror("Nao consegui salvar", f"{type(ex).__name__}: {ex}")
            return
        messagebox.showinfo("Pronto!", "Configuracao salva.\n\nAgora de dois cliques em INICIAR.bat.")
        raiz.destroy()

    tk.Button(barra, text="Salvar", width=16, bg="#1f6feb", fg="white",
              font=("Segoe UI", 10, "bold"), command=salvar).pack(side="left", padx=(18, 8))
    tk.Button(barra, text="Cancelar", width=12, command=raiz.destroy).pack(side="left", padx=8)

    # Tamanho: cabe o conteudo, mas nunca passa da altura da tela.
    raiz.update_idletasks()
    largura = max(760, quadro.winfo_reqwidth() + 40)
    altura_ideal = (topo.winfo_reqheight() + quadro.winfo_reqheight()
                    + rodape.winfo_reqheight() + barra.winfo_reqheight() + 30)
    altura = min(altura_ideal, raiz.winfo_screenheight() - 120)
    x = max(0, (raiz.winfo_screenwidth() - largura) // 2)
    raiz.geometry(f"{largura}x{altura}+{x}+20")
    raiz.mainloop()


if __name__ == "__main__":
    try:
        abrir_janela()
    except ImportError:
        print("Esta instalacao do Python nao tem o modulo de janelas (tkinter).")
        print("Envie um print desta tela ao Claude.")
        time.sleep(1)
        sys.exit(1)
