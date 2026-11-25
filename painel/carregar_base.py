# -*- coding: utf-8 -*-
"""
carregar_base.py — carrega a pasta "Base agente WhatsApp" no Supabase (aberto pelo BASE_CONHECIMENTO.bat).

Passos:
  1. Confere se o arquivo 004 já foi rodado no Supabase.
  2. Para cada empresa (subpasta), lê os arquivos e divide em trechos.
  3. Compara com o que já está no banco: arquivo igual não é reprocessado (não gasta nada).
  4. Mostra a ESTIMATIVA DE CUSTO do Gemini; acima de R$ 2,00 pede confirmação.
  5. Gera os vetores, grava cada documento de uma vez só e tira da base os arquivos apagados.
Lê as chaves de .streamlit/secrets.toml. Nenhum segredo vai para tela, log ou relatório.
"""
from __future__ import annotations

import sys
import tomllib
from datetime import datetime
from pathlib import Path

import base_conhecimento as bc

PASTA = Path(__file__).resolve().parent
LOG = PASTA / "diagnostico.log"
_SEGREDOS: list[str] = []


def limpo(t: str) -> str:
    for s in _SEGREDOS:
        t = t.replace(s, "***")
    return t


def log(texto: str, tela: bool = True) -> None:
    texto = limpo(texto)
    if tela:
        print(texto, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [base] {texto}\n")
    except Exception:  # noqa: BLE001
        pass


def carregar_segredos() -> dict:
    caminho = PASTA / ".streamlit" / "secrets.toml"
    if not caminho.exists():
        raise bc.ErroBase("Não achei o .streamlit/secrets.toml. Rode o CONFIGURAR.bat primeiro.")
    with open(caminho, "rb") as f:
        seg = tomllib.load(f)
    _SEGREDOS.extend(str(v) for v in seg.values() if isinstance(v, str) and len(v) >= 6)
    for nome in ("GEMINI_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        if not str(seg.get(nome, "")).strip():
            raise bc.ErroBase(f"Falta {nome} no secrets.toml. Rode o CONFIGURAR.bat e preencha.")
    return seg


def clientes(seg: dict):
    from google import genai
    from google.genai import types
    from supabase import create_client
    try:  # nunca fica parado esperando o Gemini: desiste da chamada após 60 segundos
        gemini = genai.Client(api_key=seg["GEMINI_API_KEY"], http_options=types.HttpOptions(timeout=60_000))
    except Exception:  # noqa: BLE001  (versões antigas da biblioteca)
        gemini = genai.Client(api_key=seg["GEMINI_API_KEY"])
    return create_client(seg["SUPABASE_URL"], seg["SUPABASE_SERVICE_ROLE_KEY"]), gemini


def planejar(sb) -> list[dict]:
    """Lê as pastas e monta o plano (o que muda em cada empresa), sem gastar nada."""
    if not bc.PASTA_BASE.exists():
        raise bc.ErroBase(f"Não achei a pasta {bc.PASTA_BASE}")
    planos = []
    for pasta in sorted(p for p in bc.PASTA_BASE.iterdir() if p.is_dir()):
        empresa = bc.empresa_por_slug(sb, pasta.name)
        if not empresa:
            log(f"AVISO: a pasta '{pasta.name}' não corresponde a nenhuma empresa do painel. Pulei.")
            continue
        docs, recusados = [], []
        for arq in bc.listar_arquivos(pasta):
            try:
                docs.append(bc.preparar_documento(arq, pasta))
            except bc.ErroBase as e:
                recusados.append(str(e))
        no_banco = bc.documentos_no_banco(sb, empresa["id"])
        mudou = [d for d in docs
                 if not (no_banco.get(d.chave, {}).get("content_hash") == d.hash
                         and no_banco[d.chave].get("status") == "ready")]
        presentes = {d.chave for d in docs}
        sairam = [k for k in no_banco if k.startswith("arquivo:") and k not in presentes]
        planos.append({"empresa": empresa, "docs": docs, "mudou": mudou, "recusados": recusados,
                       "sairam": sairam, "tokens": sum(d.tokens for d in mudou)})
    return planos


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass
    print("=" * 64)
    print("  BASE DE CONHECIMENTO DO AGENTE DE WHATSAPP — carga")
    print("=" * 64)
    try:
        seg = carregar_segredos()
        sb, gemini = clientes(seg)
        bc.conferir_instalacao_004(sb)
        planos = planejar(sb)
    except bc.ErroBase as e:
        log(f"PAROU: {e}")
        return 1
    except Exception as e:  # noqa: BLE001
        log(f"PAROU: {type(e).__name__}: {str(e)[:300]}")
        return 1

    if not planos:
        log("Nenhuma pasta de empresa encontrada em 'Base agente WhatsApp'.")
        return 1

    tokens = sum(p["tokens"] for p in planos)
    custo = bc.custo_brl(tokens)
    print()
    for p in planos:
        e = p["empresa"]
        print(f"Empresa: {e['name']} ({e['slug']})")
        for d in p["docs"]:
            marca = "NOVO/ALTERADO" if d in p["mudou"] else "igual (não gasta)"
            print(f"  - {d.chave.removeprefix('arquivo:')}: {len(d.trechos)} trechos, "
                  f"{d.visibilidade}, {marca}")
        for r in p["recusados"]:
            print(f"  ! {r}")
        for k in p["sairam"]:
            print(f"  - {k.removeprefix('arquivo:')}: saiu da pasta, será tirado da base")
    print()
    print(f"ESTIMATIVA DE CUSTO NO GEMINI: ~{tokens:,} tokens = cerca de R$ {custo:.4f}".replace(",", "."))
    print(f"(modelo {bc.MODELO_EMBEDDING}; estimativa folgada, o real costuma ser menor)")
    log(f"Plano: {sum(len(p['mudou']) for p in planos)} documento(s) a processar, "
        f"~{tokens} tokens, ~R$ {custo:.4f}", tela=False)

    if custo > bc.TETO_SEM_PERGUNTAR_BRL:
        resp = input(f"\nO custo passa de R$ {bc.TETO_SEM_PERGUNTAR_BRL:.2f}. "
                     "Digite S e Enter para continuar (qualquer outra tecla cancela): ")
        if resp.strip().lower() != "s":
            log("Cancelado pelo usuário. Nada foi gasto.")
            return 1

    erros = 0
    for p in planos:
        e = p["empresa"]
        for d in p["mudou"]:
            try:
                vetores = bc.vetores_do_documento(gemini, d)
                bc.gravar_documento(sb, e["id"], d, vetores)
                log(f"OK  {e['slug']}: {d.chave.removeprefix('arquivo:')} ({len(d.trechos)} trechos)")
            except bc.ErroBase as x:
                erros += 1
                log(f"ERRO {e['slug']}: {d.chave.removeprefix('arquivo:')}: {x}")
            except Exception as x:  # noqa: BLE001
                erros += 1
                log(f"ERRO {e['slug']}: {d.chave.removeprefix('arquivo:')}: {type(x).__name__}: {str(x)[:300]}")
        # Só remove arquivos apagados se todos os arquivos da pasta foram lidos sem recusa
        if p["sairam"] and p["docs"] and not p["recusados"]:
            try:
                n = bc.remover_ausentes(sb, e["id"], [d.chave for d in p["docs"]])
                log(f"OK  {e['slug']}: {n} documento(s) que saíram da pasta foram tirados da base")
            except Exception as x:  # noqa: BLE001
                erros += 1
                log(f"ERRO ao tirar documentos apagados: {type(x).__name__}: {str(x)[:200]}")
        erros += len(p["recusados"])

    print()
    if erros:
        log(f"Carga terminou com {erros} problema(s). Veja as linhas ERRO/! acima.")
        return 1
    log("Carga concluída sem erros.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
