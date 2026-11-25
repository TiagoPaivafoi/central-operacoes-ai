# -*- coding: utf-8 -*-
"""
publicador.py — pega os envios vencidos da fila (post_targets) e publica no Facebook/Instagram.

Usado de dois jeitos:
  * pelo painel (botões "Publicar agora" e "Processar fila");
  * por worker.py, que roda sozinho de minuto em minuto (PUBLICAR.bat).

Segurança: nenhum token aparece em mensagem de erro, log ou tela.
"""
import time

from meta import Meta, MetaErro
from padrao_visual import adaptar_legenda

ESPERA_BASE_S = 120   # espera antes da 2ª tentativa; dobra a cada falha (2, 4, 8 min...)


def _espera(tentativa: int) -> int:
    return min(ESPERA_BASE_S * (2 ** max(tentativa - 1, 0)), 3600)


def publicar_item(meta: Meta, item: dict) -> tuple[str, str | None]:
    """Publica um item vindo de claim_due_post_targets. Devolve (id_na_rede, link)."""
    plataforma = item["platform"]
    midias = item.get("media") or []
    legenda = adaptar_legenda(item.get("caption") or "", plataforma)
    formato = item["post_format"]
    if plataforma == "facebook":
        return meta.publicar_facebook(item["external_account_id"], formato, legenda, midias, item.get("link_url"))
    if plataforma == "instagram":
        pagina = item.get("parent_external_id")
        if not pagina:
            raise MetaErro("A conta do Instagram não está ligada a uma Página cadastrada. "
                           "Refaça 'Conectar contas da Meta' em Empresas.", codigo=100)
        return meta.publicar_instagram(item["external_account_id"], pagina, formato, legenda, midias)
    raise MetaErro(f"A rede '{plataforma}' ainda não está disponível para publicação automática.", codigo=100)


def processar_fila(db, meta: Meta, limite: int = 5, worker: str = "painel", log=None) -> list[dict]:
    """Processa até `limite` envios vencidos. Devolve um resumo de cada um."""
    log = log or (lambda *_: None)
    itens = db.pegar_envios_vencidos(limite, worker)
    resultado = []
    for item in itens:
        alvo = item["target_id"]
        res = {"target_id": alvo, "platform": item["platform"], "post_id": item["post_id"], "ok": False,
               "mensagem": ""}
        try:
            ident, link = publicar_item(meta, item)
        except MetaErro as e:
            ver = db.falhar_envio(alvo, str(e), str(e.codigo or ""), e.retentavel,
                                  _espera(item.get("attempt_count", 1)) if e.retentavel else None)
            res["mensagem"] = f"{e} [{ver}]"
            db.registrar_evento(item["tenant_id"], "publish.failed", f"{item['platform']}: {e}", nivel="error",
                                entidade_tipo="post", entidade_id=item["post_id"])
        except Exception as e:  # noqa: BLE001
            texto = f"{type(e).__name__}: {str(e)[:200]}"
            ver = db.falhar_envio(alvo, texto, "interno", True, _espera(item.get("attempt_count", 1)))
            res["mensagem"] = f"{texto} [{ver}]"
            db.registrar_evento(item["tenant_id"], "publish.failed", texto, nivel="error",
                                entidade_tipo="post", entidade_id=item["post_id"])
        else:
            # Já está publicado na rede: o registro precisa gravar de qualquer jeito (evita postar em dobro).
            gravou = False
            for tentativa in range(4):
                try:
                    gravou = db.concluir_envio(alvo, ident, link)
                    break
                except Exception:  # noqa: BLE001
                    time.sleep(1 + tentativa)
            res.update(ok=True, mensagem=link or ident, external_id=ident, url=link)
            db.registrar_evento(item["tenant_id"], "publish.ok" if gravou else "publish.unrecorded",
                                f"{item['platform']} publicado: {link or ident}",
                                nivel="info" if gravou else "error",
                                entidade_tipo="post", entidade_id=item["post_id"])
        log(res)
        resultado.append(res)
    return resultado
