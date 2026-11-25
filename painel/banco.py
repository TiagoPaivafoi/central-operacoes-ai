# -*- coding: utf-8 -*-
"""
banco.py — camada de acesso ao Supabase da Central de Operações AI.

Regras deste módulo:
  * Toda consulta filtra por tenant_id (uma empresa nunca enxerga os dados de outra).
  * Nenhuma chave é lida ou guardada aqui: o cliente Supabase já chega pronto do app.
  * Registrar evento (log) nunca derruba a operação principal.
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone

BUCKET = "media"

# Formatos aceitos pelo banco (CHECK da tabela posts_schedule)
FORMATOS_VALIDOS = ("image", "carousel", "video", "text")


# --------------------------------------------------------------------------
# Funções puras (sem banco)
# --------------------------------------------------------------------------
def slugify(nome: str) -> str:
    """'Imobiliária Prime' -> 'imobiliaria-prime' (formato exigido pela coluna slug)."""
    s = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or f"empresa-{uuid.uuid4().hex[:6]}"


def parse_data(valor: str | None) -> datetime | None:
    """Lê datas do Supabase em qualquer versão do Python (corrige frações de segundo)."""
    if not valor:
        return None
    t = str(valor).strip().replace("Z", "+00:00")
    t = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], t)
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def validar_agendamento(formato: str, tipos_midia: list[str], plataformas: list[str]) -> list[str]:
    """
    Devolve a lista de problemas que impedem AGENDAR a publicação (lista vazia = ok).
    Evita agendar algo que a rede social vai recusar.
    """
    problemas: list[str] = []
    imagens = sum(1 for t in tipos_midia if t == "image")
    videos = sum(1 for t in tipos_midia if t == "video")

    if not plataformas:
        problemas.append("Selecione pelo menos uma conta de rede social para publicar.")
    if formato == "image" and imagens < 1:
        problemas.append("Post de imagem precisa de 1 imagem.")
    if formato == "carousel" and imagens < 2:
        problemas.append("Carrossel precisa de pelo menos 2 imagens (slides). Envie ou gere as imagens na Mesa do carrossel.")
    if formato == "video" and videos < 1:
        problemas.append("Post de vídeo precisa de um arquivo de vídeo (hoje o sistema gera só o roteiro).")
    if "instagram" in plataformas and not tipos_midia:
        problemas.append("O Instagram não publica post só de texto: é preciso imagem ou vídeo.")
    if "tiktok" in plataformas and videos < 1:
        problemas.append("O TikTok só aceita vídeo.")
    return problemas


# --------------------------------------------------------------------------
# Acesso ao banco
# --------------------------------------------------------------------------
class Banco:
    def __init__(self, cliente):
        self.c = cliente

    # ---- utilidades internas -------------------------------------------
    def _in(self, tabela: str, colunas: str, campo: str, valores, tenant_id: str) -> list[dict]:
        valores = [v for v in set(valores) if v]
        if not valores:
            return []
        r = (self.c.table(tabela).select(colunas)
             .eq("tenant_id", tenant_id).in_(campo, valores).execute())
        return r.data or []

    def _url_publica(self, caminho: str) -> str:
        r = self.c.storage.from_(BUCKET).get_public_url(caminho)
        if isinstance(r, dict):
            r = r.get("publicURL") or r.get("publicUrl") or ""
        r = str(r).rstrip("?")
        if not r.startswith("https://"):
            raise RuntimeError("O armazenamento não devolveu uma URL pública HTTPS.")
        return r

    # ---- saúde ---------------------------------------------------------
    def ping(self) -> None:
        self.c.table("tenants").select("id").limit(1).execute()

    # ---- empresas ------------------------------------------------------
    def listar_empresas(self) -> list[dict]:
        r = (self.c.table("tenants").select("*")
             .neq("status", "archived").order("name").execute())
        return r.data or []

    def _empresa_por_slug(self, slug: str) -> dict | None:
        r = self.c.table("tenants").select("*").eq("slug", slug).limit(1).execute()
        return (r.data or [None])[0]

    def criar_empresa(self, nome: str, nicho: str = "", cores: str = "", briefing: str = "",
                      webhook: str = "", tom: str = "", regras: str = "", whatsapp: str = "") -> dict:
        nome = (nome or "").strip()
        if not nome:
            raise ValueError("Informe o nome da empresa.")
        slug = slugify(nome)
        if self._empresa_por_slug(slug):
            raise ValueError(f"Já existe uma empresa com o identificador '{slug}'.")
        linha = {
            "slug": slug,
            "name": nome,
            "status": "active",
            "tone_of_voice": tom or None,
            "business_rules": regras or None,
            "brand_colors": {"descricao": cores} if cores else {},
            "settings": {"nicho": nicho, "briefing": briefing, "n8n_webhook": webhook,
                         "whatsapp": (whatsapp or "").strip()},
        }
        return self.c.table("tenants").insert(linha).execute().data[0]

    def atualizar_empresa(self, empresa: dict, nome: str, nicho: str, cores: str, briefing: str,
                          webhook: str, tom: str, regras: str, status: str, whatsapp: str | None = None) -> dict:
        settings = dict(empresa.get("settings") or {})
        settings.update({"nicho": nicho, "briefing": briefing, "n8n_webhook": webhook})
        if whatsapp is not None:
            settings["whatsapp"] = whatsapp.strip()
        brand = dict(empresa.get("brand_colors") or {})
        brand["descricao"] = cores
        campos = {
            "name": (nome or empresa["name"]).strip(),
            "status": status,
            "tone_of_voice": tom or None,
            "business_rules": regras or None,
            "brand_colors": brand,
            "settings": settings,
        }
        r = self.c.table("tenants").update(campos).eq("id", empresa["id"]).execute()
        return (r.data or [empresa])[0]

    def importar_empresa_legada(self, nome: str, dados: dict) -> str:
        """Traz uma empresa do empresas.json antigo. Só preenche o que está vazio. Devolve 'criada' ou 'atualizada'."""
        slug = slugify(nome)
        atual = self._empresa_por_slug(slug)
        nicho, cores = dados.get("nicho", ""), dados.get("cores", "")
        briefing, webhook = dados.get("briefing", ""), dados.get("n8n_webhook", "")
        if not atual:
            self.criar_empresa(nome, nicho, cores, briefing, webhook)
            return "criada"
        s = atual.get("settings") or {}
        self.atualizar_empresa(
            atual, atual["name"],
            s.get("nicho") or nicho,
            (atual.get("brand_colors") or {}).get("descricao") or cores,
            s.get("briefing") or briefing,
            s.get("n8n_webhook") or webhook,
            atual.get("tone_of_voice") or "",
            atual.get("business_rules") or "",
            atual.get("status") or "active",
        )
        return "atualizada"

    def id_empresa_por_nome(self, nome: str) -> dict | None:
        return self._empresa_por_slug(slugify(nome))

    def enviar_logo(self, tenant_id: str, png_bytes: bytes) -> str:
        caminho = f"{tenant_id}/logo/{int(datetime.now(timezone.utc).timestamp())}.png"
        self.c.storage.from_(BUCKET).upload(
            path=caminho, file=png_bytes, file_options={"content-type": "image/png"})
        url = self._url_publica(caminho)
        self.c.table("tenants").update({"logo_url": url}).eq("id", tenant_id).execute()
        return url

    # ---- contas de redes sociais ---------------------------------------
    def listar_contas(self, tenant_id: str) -> list[dict]:
        r = (self.c.table("social_credentials")
             .select("id,platform,label,external_account_name,status")
             .eq("tenant_id", tenant_id).eq("status", "active").order("platform").execute())
        return r.data or []

    # ---- mídia ---------------------------------------------------------
    def enviar_midia(self, tenant_id: str, conteudo: bytes, mime: str, kind: str = "image",
                     largura: int | None = None, altura: int | None = None,
                     origem: str = "ai_generated", alt: str | None = None) -> dict:
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "video/mp4": "mp4",
               "video/quicktime": "mov"}.get(mime, "bin")
        agora = datetime.now(timezone.utc)
        caminho = f"{tenant_id}/{agora:%Y}/{agora:%m}/{uuid.uuid4().hex}.{ext}"
        self.c.storage.from_(BUCKET).upload(
            path=caminho, file=conteudo, file_options={"content-type": mime})
        linha = {
            "tenant_id": tenant_id, "kind": kind, "storage_provider": "supabase",
            "storage_bucket": BUCKET, "storage_path": caminho,
            "public_url": self._url_publica(caminho), "mime_type": mime,
            "size_bytes": len(conteudo), "width": largura, "height": altura,
            "origin": origem, "alt_text": alt,
        }
        return self.c.table("media_assets").insert(linha).execute().data[0]

    # ---- posts ---------------------------------------------------------
    def criar_post(self, tenant_id: str, titulo: str, formato: str, legenda: str,
                   agendar_em: datetime, status: str, ai_context: dict,
                   asset_ids: list[str], credential_ids: list[str]) -> dict:
        if formato not in FORMATOS_VALIDOS:
            raise ValueError(f"Formato inválido: {formato}")
        post = self.c.table("posts_schedule").insert({
            "tenant_id": tenant_id, "title": (titulo or "")[:200] or None,
            "post_format": formato, "caption": legenda or "",
            "scheduled_at": agendar_em.isoformat(), "status": status,
            "generated_by_ai": True, "ai_context": ai_context,
        }).execute().data[0]
        pid = post["id"]
        try:
            for ordem, aid in enumerate(asset_ids):
                self.c.table("post_media").insert(
                    {"tenant_id": tenant_id, "post_id": pid, "asset_id": aid, "sort_order": ordem}).execute()
            if credential_ids:
                self.c.table("post_targets").insert(
                    [{"tenant_id": tenant_id, "post_id": pid, "credential_id": cid}
                     for cid in credential_ids]).execute()
        except Exception:
            # Não deixa um post pela metade: remove (as mídias e destinos saem em cascata).
            try:
                self.c.table("posts_schedule").delete().eq("id", pid).eq("tenant_id", tenant_id).execute()
            except Exception:
                pass
            raise
        self.registrar_evento(tenant_id, "post.created", f"Post criado ({status})",
                              entidade_tipo="post", entidade_id=pid)
        return post

    def listar_posts(self, tenant_id: str, limite: int = 40) -> list[dict]:
        r = (self.c.table("posts_schedule").select("*").eq("tenant_id", tenant_id)
             .order("created_at", desc=True).limit(limite).execute())
        return self.detalhes_posts(tenant_id, r.data or [])

    def detalhes_posts(self, tenant_id: str, posts: list[dict]) -> list[dict]:
        """Anexa a cada post os destinos (_alvos) e as mídias (_midias)."""
        if not posts:
            return posts
        ids = [p["id"] for p in posts]
        alvos = self._in("post_targets",
                         "id,post_id,credential_id,status,attempt_count,max_attempts,last_error,external_url,retry_after",
                         "post_id", ids, tenant_id)
        midias = self._in("post_media", "post_id,asset_id,sort_order", "post_id", ids, tenant_id)
        creds = {c["id"]: c for c in self._in("social_credentials", "id,platform,label", "id",
                                              [a["credential_id"] for a in alvos], tenant_id)}
        assets = {a["id"]: a for a in self._in("media_assets", "id,kind,public_url", "id",
                                               [m["asset_id"] for m in midias], tenant_id)}
        for p in posts:
            p["_alvos"] = [
                {**a, "platform": creds.get(a["credential_id"], {}).get("platform", "?"),
                 "label": creds.get(a["credential_id"], {}).get("label", "")}
                for a in alvos if a["post_id"] == p["id"]]
            ordenadas = sorted((m for m in midias if m["post_id"] == p["id"]), key=lambda m: m["sort_order"])
            p["_midias"] = [assets[m["asset_id"]] for m in ordenadas if m["asset_id"] in assets]
        return posts

    def mudar_status_post(self, tenant_id: str, post: dict, novo: str) -> tuple[bool, str]:
        """
        Transições permitidas pelo operador:
          draft -> pending_approval | scheduled | cancelled
          pending_approval -> draft | scheduled | cancelled
          scheduled -> draft | cancelled
        Agendar só passa se o post estiver completo (mídia + contas certas).
        """
        permitido = {
            "draft": {"pending_approval", "scheduled", "cancelled"},
            "pending_approval": {"draft", "scheduled", "cancelled"},
            "scheduled": {"draft", "cancelled"},
        }
        atual = post["status"]
        if novo not in permitido.get(atual, set()):
            return False, f"Não é possível ir de '{atual}' para '{novo}'."
        if novo == "scheduled":
            plataformas = [a["platform"] for a in post.get("_alvos", []) if a["status"] != "cancelled"]
            problemas = validar_agendamento(post["post_format"], [m["kind"] for m in post.get("_midias", [])], plataformas)
            if problemas:
                return False, " ".join(problemas)
        r = (self.c.table("posts_schedule").update({"status": novo})
             .eq("id", post["id"]).eq("tenant_id", tenant_id).eq("status", atual).execute())
        if not r.data:
            return False, "Este post mudou ou não pertence a esta empresa. Recarregue a página."
        if novo == "cancelled":
            self.c.table("post_targets").update({"status": "cancelled"}) \
                .eq("post_id", post["id"]).eq("tenant_id", tenant_id).eq("status", "pending").execute()
        self.registrar_evento(tenant_id, f"post.{novo}", f"Status alterado: {atual} → {novo}",
                              entidade_tipo="post", entidade_id=post["id"])
        return True, "ok"

    # ---- contas Meta (Facebook/Instagram) e fila de publicação -----------
    def atualizar_contexto_post(self, tenant_id: str, post_id: str, contexto: dict) -> None:
        self.c.table("posts_schedule").update({"ai_context": contexto}) \
            .eq("id", post_id).eq("tenant_id", tenant_id).execute()

    def atualizar_legenda_post(self, tenant_id: str, post_id: str, legenda: str) -> bool:
        """Troca a legenda só enquanto o post ainda não foi publicado."""
        r = (self.c.table("posts_schedule").update({"caption": legenda}).eq("id", post_id)
             .eq("tenant_id", tenant_id).in_("status", ["draft", "pending_approval", "scheduled"]).execute())
        if r.data:
            self.registrar_evento(tenant_id, "post.caption_updated", "Legenda atualizada com a sugestão do editor",
                                  entidade_tipo="post", entidade_id=post_id)
        return bool(r.data)

    def agendar_para(self, tenant_id: str, post: dict, quando: datetime) -> tuple[bool, str]:
        """Põe o post como 'agendado' para a hora escolhida (a fila/robô publica quando chegar a hora)."""
        if post["status"] in ("draft", "pending_approval"):
            ok, msg = self.mudar_status_post(tenant_id, post, "scheduled")
            if not ok:
                return False, msg
        elif post["status"] != "scheduled":
            return False, f"Não dá para agendar um post com situação '{post['status']}'."
        r = (self.c.table("posts_schedule").update({"scheduled_at": quando.astimezone(timezone.utc).isoformat()})
             .eq("id", post["id"]).eq("tenant_id", tenant_id).eq("status", "scheduled").execute())
        if not r.data:
            return False, "Não consegui gravar o horário. Recarregue a página."
        self.registrar_evento(tenant_id, "post.rescheduled", f"Agendado para {quando:%d/%m/%Y %H:%M}",
                              entidade_tipo="post", entidade_id=post["id"])
        return True, "ok"

    def credenciais_da_empresa(self, tenant_id: str) -> list[dict]:
        r = (self.c.table("social_credentials")
             .select("id,platform,label,external_account_id,external_account_name,parent_external_id,token_kind,status")
             .eq("tenant_id", tenant_id).order("platform").execute())
        return r.data or []

    def conectar_conta(self, tenant_id: str, plataforma: str, rotulo: str, ext_id: str, nome: str | None,
                       pai_id: str | None = None) -> dict:
        """Cadastra (ou reativa) uma conta que usa o token do usuário do sistema. Não guarda token no banco."""
        agora = datetime.now(timezone.utc).isoformat()
        campos = {"label": rotulo, "external_account_name": nome, "parent_external_id": pai_id,
                  "token_kind": "system_user", "status": "active", "last_validated_at": agora, "last_error": None}
        ex = (self.c.table("social_credentials").select("id").eq("tenant_id", tenant_id)
              .eq("platform", plataforma).eq("external_account_id", ext_id).limit(1).execute().data or [])
        if ex:
            return self.c.table("social_credentials").update(campos).eq("id", ex[0]["id"]).execute().data[0]
        linha = dict(campos, tenant_id=tenant_id, platform=plataforma, external_account_id=ext_id)
        return self.c.table("social_credentials").insert(linha).execute().data[0]

    def adicionar_destinos(self, tenant_id: str, post_id: str, credential_ids: list[str]) -> int:
        ja = {a["credential_id"] for a in (self.c.table("post_targets").select("credential_id")
                                           .eq("tenant_id", tenant_id).eq("post_id", post_id).execute().data or [])}
        novos = [{"tenant_id": tenant_id, "post_id": post_id, "credential_id": c}
                 for c in credential_ids if c not in ja]
        if novos:
            self.c.table("post_targets").insert(novos).execute()
        return len(novos)

    def antecipar_post(self, tenant_id: str, post_id: str) -> bool:
        """Faz um post agendado ficar 'vencido agora' para a fila pegar na hora."""
        # 2 minutos no passado: protege contra diferença de relógio entre este computador e o banco.
        agora = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        r = (self.c.table("posts_schedule").update({"scheduled_at": agora})
             .eq("id", post_id).eq("tenant_id", tenant_id).eq("status", "scheduled").execute())
        return bool(r.data)

    def pegar_envios_vencidos(self, limite: int = 5, worker: str = "painel") -> list[dict]:
        return self.c.rpc("claim_due_post_targets", {"p_limit": limite, "p_worker": worker}).execute().data or []

    def concluir_envio(self, alvo_id: str, ident: str, link: str | None) -> bool:
        r = self.c.rpc("complete_post_target", {"p_target_id": alvo_id, "p_external_post_id": ident,
                                                "p_external_url": link}).execute()
        return bool(r.data)

    def falhar_envio(self, alvo_id: str, erro: str, codigo: str | None, retentavel: bool,
                     espera_s: int | None) -> str:
        r = self.c.rpc("fail_post_target", {"p_target_id": alvo_id, "p_error": (erro or "")[:500],
                                            "p_error_code": codigo or None, "p_retryable": bool(retentavel),
                                            "p_retry_after_seconds": espera_s}).execute()
        return str(r.data)

    def reenfileirar_post(self, tenant_id: str, post_id: str) -> bool:
        """Dá outra chance aos destinos que falharam (zera tentativas) e põe o post na fila agora."""
        agora = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        r = (self.c.table("post_targets")
             .update({"status": "pending", "attempt_count": 0, "retry_after": None, "last_error": None,
                      "last_error_code": None, "locked_at": None, "locked_by": None})
             .eq("post_id", post_id).eq("tenant_id", tenant_id).eq("status", "failed").execute())
        if not r.data:
            return False
        self.c.table("posts_schedule").update({"status": "scheduled", "scheduled_at": agora}) \
            .eq("id", post_id).eq("tenant_id", tenant_id).in_("status", ["failed", "partially_published"]).execute()
        self.registrar_evento(tenant_id, "post.requeued", "Destinos com falha voltaram para a fila",
                              entidade_tipo="post", entidade_id=post_id)
        return True

    # ---- eventos (logs) ------------------------------------------------
    def registrar_evento(self, tenant_id: str | None, tipo: str, mensagem: str, nivel: str = "info",
                         entidade_tipo: str | None = None, entidade_id: str | None = None,
                         contexto: dict | None = None) -> None:
        try:
            self.c.table("event_logs").insert({
                "tenant_id": tenant_id, "source": "streamlit", "level": nivel, "event_type": tipo,
                "entity_type": entidade_tipo, "entity_id": entidade_id,
                "message": (mensagem or "")[:500], "context": contexto or {},
            }).execute()
        except Exception:
            pass  # log nunca pode derrubar a operação

    def listar_eventos(self, tenant_id: str, limite: int = 100) -> list[dict]:
        r = (self.c.table("event_logs").select("created_at,level,source,event_type,message")
             .eq("tenant_id", tenant_id).order("created_at", desc=True).limit(limite).execute())
        return r.data or []
