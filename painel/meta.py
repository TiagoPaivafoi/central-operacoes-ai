# -*- coding: utf-8 -*-
"""
meta.py — conversa com a Meta (Facebook e Instagram) pela Graph API.

* O token do usuário do sistema (n8n-bot) vem de fora (secrets.toml) e NUNCA é escrito em log,
  em tela ou na URL: ele viaja só no cabeçalho Authorization.
* Publicação: foto/vídeo/carrossel/texto na Página do Facebook; foto/Reels/carrossel no Instagram.
* Leitura: seguidores, alcance e desempenho de posts (somente leitura).
"""
import re
import time

import requests

VERSAO_GRAPH = "v23.0"
BASE = "https://graph.facebook.com"
TIMEOUT = 60
LIMITE_LEGENDA_IG = 2200
LIMITE_HASHTAGS_IG = 30

# Códigos da Meta que valem a pena tentar de novo (limite de uso, instabilidade).
CODIGOS_TEMPORARIOS = {1, 2, 4, 17, 32, 341, 368, 613, 80001, 80002, 80003, 80004, 80005, 80006, 80008, 80009}
# Códigos que exigem ação humana (token, permissão, parâmetro inválido).
CODIGOS_DEFINITIVOS = {10, 100, 102, 190, 200, 210, 220, 240, 283, 299, 459, 467}


class MetaErro(Exception):
    """Erro da Meta já traduzido. `retentavel` diz se vale tentar de novo sozinho."""

    def __init__(self, mensagem: str, codigo: int | None = None, subcodigo: int | None = None,
                 retentavel: bool = False, http: int | None = None):
        super().__init__(mensagem)
        self.codigo, self.subcodigo, self.retentavel, self.http = codigo, subcodigo, retentavel, http


def _limpar(texto: str, token: str) -> str:
    texto = str(texto or "")
    if token and len(token) >= 6:
        texto = texto.replace(token, "***")
    return re.sub(r"EAA[A-Za-z0-9]{20,}", "***", texto)[:500]


class Meta:
    def __init__(self, token: str, versao: str = VERSAO_GRAPH, sessao=None):
        if not token or len(token) < 20:
            raise MetaErro("Token da Meta ausente. Preencha 'Token da Meta' no CONFIGURAR.bat.")
        self._token = token
        self.versao = versao
        self.http = sessao or requests.Session()
        self._cache_pagina: dict = {}

    # ------------------------------------------------------------------ base
    def _chamar(self, metodo: str, caminho: str, params: dict | None = None, dados: dict | None = None,
                token: str | None = None):
        usar = token or self._token
        url = f"{BASE}/{self.versao}/{caminho.lstrip('/')}"
        try:
            r = self.http.request(metodo, url, params=params, data=dados, timeout=TIMEOUT,
                                  headers={"Authorization": f"Bearer {usar}"})
        except requests.Timeout as e:
            raise MetaErro("A Meta demorou demais para responder.", retentavel=True) from e
        except requests.RequestException as e:
            raise MetaErro("Falha de conexão com a Meta: " + _limpar(e, usar), retentavel=True) from e
        try:
            corpo = r.json()
        except ValueError:
            corpo = {}
        if r.status_code >= 400 or (isinstance(corpo, dict) and "error" in corpo):
            err = (corpo or {}).get("error", {}) if isinstance(corpo, dict) else {}
            codigo, sub = err.get("code"), err.get("error_subcode")
            msg = err.get("error_user_msg") or err.get("message") or f"HTTP {r.status_code}"
            retent = (codigo in CODIGOS_TEMPORARIOS) or (codigo not in CODIGOS_DEFINITIVOS and r.status_code >= 500)
            raise MetaErro(_limpar(f"{msg} (código {codigo}" + (f"/{sub}" if sub else "") + ")", usar),
                           codigo, sub, retent, r.status_code)
        return corpo

    def get(self, caminho, params=None, token=None):
        return self._chamar("GET", caminho, params=params, token=token)

    def post(self, caminho, dados=None, token=None):
        return self._chamar("POST", caminho, dados=dados, token=token)

    # ------------------------------------------------------- descoberta/token
    def descobrir_ativos(self) -> list[dict]:
        """Páginas que o token enxerga, com o Instagram profissional ligado a cada uma (se houver)."""
        campos = "id,name,instagram_business_account{id,username,name,followers_count}"
        paginas, params = [], {"fields": campos, "limit": 100}
        for _ in range(5):
            r = self.get("me/accounts", params)
            paginas += r.get("data", [])
            prox = (r.get("paging") or {}).get("cursors", {}).get("after")
            if not (r.get("paging") or {}).get("next") or not prox:
                break
            params = dict(params, after=prox)
        saida = []
        for p in paginas:
            ig = p.get("instagram_business_account") or {}
            saida.append({"page_id": p["id"], "page_name": p.get("name", ""),
                          "ig_id": ig.get("id"), "ig_username": ig.get("username"),
                          "ig_followers": ig.get("followers_count")})
        return saida

    def token_da_pagina(self, page_id: str) -> str:
        """Token específico da Página (necessário para publicar). Guardado só na memória."""
        if page_id in self._cache_pagina:
            return self._cache_pagina[page_id]
        r = self.get(page_id, {"fields": "access_token"})
        tok = r.get("access_token")
        if not tok:
            raise MetaErro("A Meta não liberou o token desta Página. Confira se o n8n-bot tem 'Acesso total' à Página.",
                           codigo=200)
        self._cache_pagina[page_id] = tok
        return tok

    def validar_token(self) -> dict:
        """Confere se o token está vivo (usa a ferramenta debug_token do próprio app não é necessário)."""
        r = self.get("me", {"fields": "id,name"})
        return {"id": r.get("id"), "nome": r.get("name")}

    # ------------------------------------------------------------- Facebook
    def publicar_facebook(self, page_id: str, formato: str, legenda: str, midias: list[dict],
                          link: str | None = None) -> tuple[str, str | None]:
        tok = self.token_da_pagina(page_id)
        imagens = [m["public_url"] for m in midias if m.get("kind") == "image"]
        videos = [m["public_url"] for m in midias if m.get("kind") == "video"]
        if formato == "video" and videos:
            r = self.post(f"{page_id}/videos", {"file_url": videos[0], "description": legenda}, tok)
            ident = str(r.get("id"))
        elif formato in ("image", "carousel") and len(imagens) == 1:
            r = self.post(f"{page_id}/photos", {"url": imagens[0], "caption": legenda, "published": "true"}, tok)
            ident = str(r.get("post_id") or r.get("id"))
        elif formato in ("image", "carousel") and len(imagens) > 1:
            ids = []
            for u in imagens[:10]:
                f = self.post(f"{page_id}/photos", {"url": u, "published": "false"}, tok)
                ids.append(f["id"])
            dados = {"message": legenda}
            for i, fid in enumerate(ids):
                dados[f"attached_media[{i}]"] = '{"media_fbid":"%s"}' % fid
            r = self.post(f"{page_id}/feed", dados, tok)
            ident = str(r.get("id"))
        elif formato == "text":
            dados = {"message": legenda}
            if link:
                dados["link"] = link
            r = self.post(f"{page_id}/feed", dados, tok)
            ident = str(r.get("id"))
        else:
            raise MetaErro("Este post não tem a mídia necessária para o Facebook.", codigo=100)
        return ident, self._permalink(ident, tok)

    def _permalink(self, ident: str, token: str) -> str | None:
        try:
            r = self.get(ident, {"fields": "permalink_url"}, token)
            link = r.get("permalink_url")
            if link:
                return link if link.startswith("http") else "https://www.facebook.com" + link
        except MetaErro:
            pass
        return f"https://www.facebook.com/{ident}"

    # ------------------------------------------------------------ Instagram
    @staticmethod
    def validar_legenda_instagram(legenda: str) -> None:
        if len(legenda or "") > LIMITE_LEGENDA_IG:
            raise MetaErro(f"A legenda tem {len(legenda)} caracteres; o Instagram aceita até {LIMITE_LEGENDA_IG}.",
                           codigo=100)
        if len(re.findall(r"#\w+", legenda or "")) > LIMITE_HASHTAGS_IG:
            raise MetaErro(f"O Instagram aceita até {LIMITE_HASHTAGS_IG} hashtags por post.", codigo=100)

    def _esperar_container(self, container_id: str, tok: str, limite_s: int = 420) -> None:
        inicio, espera = time.time(), 3
        while True:
            r = self.get(container_id, {"fields": "status_code,status"}, tok)
            st = r.get("status_code")
            if st in ("FINISHED", "PUBLISHED"):
                return
            if st in ("ERROR", "EXPIRED"):
                raise MetaErro(f"O Instagram recusou a mídia ({r.get('status') or st}).", codigo=100)
            if time.time() - inicio > limite_s:
                raise MetaErro("O Instagram ainda está processando a mídia; tentaremos de novo.", retentavel=True)
            time.sleep(espera)
            espera = min(espera + 2, 15)

    def publicar_instagram(self, ig_id: str, page_id: str, formato: str, legenda: str,
                           midias: list[dict]) -> tuple[str, str | None]:
        self.validar_legenda_instagram(legenda)
        tok = self.token_da_pagina(page_id)
        imagens = [m["public_url"] for m in midias if m.get("kind") == "image"]
        videos = [m["public_url"] for m in midias if m.get("kind") == "video"]
        if formato == "video" and videos:
            c = self.post(f"{ig_id}/media", {"media_type": "REELS", "video_url": videos[0],
                                             "caption": legenda, "share_to_feed": "true"}, tok)["id"]
        elif formato in ("image", "carousel") and len(imagens) == 1:
            c = self.post(f"{ig_id}/media", {"image_url": imagens[0], "caption": legenda}, tok)["id"]
        elif formato in ("image", "carousel") and len(imagens) > 1:
            filhos = []
            for u in imagens[:10]:
                f = self.post(f"{ig_id}/media", {"image_url": u, "is_carousel_item": "true"}, tok)["id"]
                filhos.append(f)
            for f in filhos:
                self._esperar_container(f, tok)
            c = self.post(f"{ig_id}/media", {"media_type": "CAROUSEL", "children": ",".join(filhos),
                                             "caption": legenda}, tok)["id"]
        else:
            raise MetaErro("O Instagram precisa de imagem ou vídeo; este post não tem a mídia necessária.",
                           codigo=100)
        self._esperar_container(c, tok)
        pub = self.post(f"{ig_id}/media_publish", {"creation_id": c}, tok)
        media_id = str(pub.get("id"))
        link = None
        try:
            link = self.get(media_id, {"fields": "permalink"}, tok).get("permalink")
        except MetaErro:
            pass
        return media_id, link

    # -------------------------------------------------------------- métricas
    def _metrica_total(self, caminho: str, metrica: str, tok: str, extra: dict | None = None):
        params = {"metric": metrica, "metric_type": "total_value", "period": "day"}
        params.update(extra or {})
        r = self.get(f"{caminho}/insights", params, tok)
        for item in r.get("data", []):
            tv = item.get("total_value") or {}
            if "value" in tv:
                return tv["value"]
            vals = item.get("values") or []
            if vals:
                return vals[-1].get("value")
        return None

    def metricas_instagram(self, ig_id: str, page_id: str, dias: int = 7) -> dict:
        """Resumo do perfil + últimos posts. Cada métrica é pedida sozinha: se uma não existir, as outras seguem."""
        tok = self.token_da_pagina(page_id)
        fim = int(time.time())
        ini = fim - dias * 86400
        perfil = self.get(ig_id, {"fields": "username,name,followers_count,follows_count,media_count"}, tok)
        resumo, avisos = {}, []
        for m in ("reach", "views", "profile_views", "accounts_engaged", "total_interactions",
                  "likes", "comments", "shares", "saves"):
            try:
                resumo[m] = self._metrica_total(ig_id, m, tok, {"since": ini, "until": fim})
            except MetaErro as e:
                avisos.append(f"{m}: {e}")
        posts = []
        try:
            r = self.get(f"{ig_id}/media", {"fields": "id,caption,media_type,media_product_type,permalink,timestamp,"
                                                      "like_count,comments_count", "limit": 10}, tok)
            for p in r.get("data", []):
                ins = {}
                for m in ("reach", "views", "saved", "shares", "total_interactions"):
                    try:
                        d = self.get(f"{p['id']}/insights", {"metric": m}, tok).get("data", [])
                        if d and d[0].get("values"):
                            ins[m] = d[0]["values"][0].get("value")
                    except MetaErro:
                        continue
                posts.append({**p, "insights": ins})
        except MetaErro as e:
            avisos.append(f"posts: {e}")
        return {"perfil": perfil, "resumo": resumo, "posts": posts, "avisos": avisos, "dias": dias}

    def metricas_facebook(self, page_id: str, dias: int = 7) -> dict:
        tok = self.token_da_pagina(page_id)
        fim = int(time.time())
        ini = fim - dias * 86400
        pagina = self.get(page_id, {"fields": "name,fan_count,followers_count"}, tok)
        resumo, avisos = {}, []
        for m in ("page_media_view", "page_total_media_view_unique", "page_post_engagements", "page_daily_follows",
                  "page_views_total"):
            try:
                r = self.get(f"{page_id}/insights", {"metric": m, "period": "day", "since": ini, "until": fim}, tok)
                soma = 0
                achou = False
                for item in r.get("data", []):
                    for v in item.get("values", []):
                        if isinstance(v.get("value"), (int, float)):
                            soma += v["value"]
                            achou = True
                resumo[m] = soma if achou else None
            except MetaErro as e:
                avisos.append(f"{m}: {e}")
        posts = []
        try:
            r = self.get(f"{page_id}/posts", {"fields": "id,message,created_time,permalink_url,"
                                                        "reactions.summary(true).limit(0),"
                                                        "comments.summary(true).limit(0),shares", "limit": 10}, tok)
            for p in r.get("data", []):
                posts.append({"id": p["id"], "mensagem": (p.get("message") or "")[:140],
                              "data": p.get("created_time"), "link": p.get("permalink_url"),
                              "reacoes": ((p.get("reactions") or {}).get("summary") or {}).get("total_count"),
                              "comentarios": ((p.get("comments") or {}).get("summary") or {}).get("total_count"),
                              "compartilhamentos": (p.get("shares") or {}).get("count")})
        except MetaErro as e:
            avisos.append(f"posts: {e}")
        return {"pagina": pagina, "resumo": resumo, "posts": posts, "avisos": avisos, "dias": dias}
