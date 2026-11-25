# -*- coding: utf-8 -*-
"""
diagnostico_gemini_embedding.py — teste rápido do Gemini para a base de conhecimento.
Faz 2 chamadas pequenas (custo ~R$ 0,0001) e mostra quanto tempo cada uma levou e o erro, se houver.
Nenhuma chave aparece na tela.
"""
import sys
import time
import tomllib
from pathlib import Path

PASTA = Path(__file__).resolve().parent


def main() -> int:
    with open(PASTA / ".streamlit" / "secrets.toml", "rb") as f:
        seg = tomllib.load(f)
    chave = str(seg.get("GEMINI_API_KEY", ""))

    def limpo(t: str) -> str:
        return t.replace(chave, "***") if chave else t

    import google.genai as genai_mod
    from google import genai
    from google.genai import types
    print("Versão da biblioteca google-genai:", getattr(genai_mod, "__version__", "?"))
    print("Python:", sys.version.split()[0])

    try:
        cliente = genai.Client(api_key=chave, http_options=types.HttpOptions(
            timeout=30_000, retry_options=types.HttpRetryOptions(attempts=1)))
    except Exception:  # noqa: BLE001
        cliente = genai.Client(api_key=chave, http_options=types.HttpOptions(timeout=30_000))

    testes = [
        ("1 texto simples", "title: none | text: teste de conexão"),
        ("2 textos em lote", [types.Content(parts=[types.Part(text="title: none | text: um")]),
                              types.Content(parts=[types.Part(text="title: none | text: dois")])]),
    ]
    ok_geral = True
    for nome, conteudo in testes:
        t0 = time.time()
        try:
            r = cliente.models.embed_content(
                model="gemini-embedding-2", contents=conteudo,
                config=types.EmbedContentConfig(output_dimensionality=768))
            dims = [len(e.values) for e in (r.embeddings or [])]
            print(f"[OK]   {nome}: {time.time() - t0:.1f} s, vetores={len(dims)}, tamanhos={dims}")
        except Exception as e:  # noqa: BLE001
            ok_geral = False
            print(f"[ERRO] {nome}: {time.time() - t0:.1f} s -> {type(e).__name__}: {limpo(str(e))[:500]}")
    return 0 if ok_geral else 1


if __name__ == "__main__":
    rc = main()
    try:
        input("\nAperte Enter para fechar...")
    except EOFError:
        pass
    sys.exit(rc)
