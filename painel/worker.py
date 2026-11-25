# -*- coding: utf-8 -*-
"""
worker.py — robô da fila de publicação. Roda sozinho (PUBLICAR.bat), de minuto em minuto:
pega os posts agendados que já venceram e publica no Facebook/Instagram.
Lê as chaves de .streamlit/secrets.toml. Nenhum segredo vai para tela ou log.
"""
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path

PASTA = Path(__file__).resolve().parent
INTERVALO_S = 60
LOG = PASTA / "diagnostico.log"


def log(texto: str) -> None:
    linha = f"{datetime.now():%Y-%m-%d %H:%M:%S} [worker] {texto}"
    print(linha, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except Exception:
        pass


def carregar_segredos() -> dict:
    caminho = PASTA / ".streamlit" / "secrets.toml"
    with open(caminho, "rb") as f:
        return tomllib.load(f)


def main() -> int:
    from supabase import create_client
    from banco import Banco
    from meta import Meta
    import publicador

    seg = carregar_segredos()
    for nome in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "META_SYSTEM_TOKEN"):
        if not str(seg.get(nome, "")).strip():
            log(f"Falta {nome} no CONFIGURAR. Rode o CONFIGURAR.bat e preencha.")
            return 1
    segredos = [str(v) for k, v in seg.items() if isinstance(v, str) and len(v) >= 6]

    def limpo(t: str) -> str:
        for s in segredos:
            t = t.replace(s, "***")
        return t

    db = Banco(create_client(seg["SUPABASE_URL"], seg["SUPABASE_SERVICE_ROLE_KEY"]))
    meta = Meta(seg["META_SYSTEM_TOKEN"], versao=str(seg.get("META_GRAPH_VERSION") or "v23.0"))
    log("Robô de publicação ligado. Verifico a fila a cada 60 segundos. Pode minimizar esta janela.")
    while True:
        try:
            res = publicador.processar_fila(db, meta, limite=5, worker="robo-pc")
            for r in res:
                log(limpo(f"{r['platform']} {'OK' if r['ok'] else 'FALHOU'}: {r['mensagem']}"))
        except KeyboardInterrupt:
            log("Robô encerrado.")
            return 0
        except Exception as e:  # noqa: BLE001
            log(limpo(f"Erro ao processar a fila: {type(e).__name__}: {str(e)[:200]}"))
        time.sleep(INTERVALO_S)


if __name__ == "__main__":
    sys.exit(main())
