# -*- coding: utf-8 -*-
"""
Central de Operações AI — painel interno da agência (multi-empresa).

Modelo de operação: as empresas NÃO acessam este sistema. Elas enviam os dados e a
agência produz e agenda tudo por aqui.

Segurança:
  * Todas as chaves ficam em .streamlit/secrets.toml (nunca neste arquivo).
  * Acesso protegido por senha única (APP_PASSWORD) com bloqueio após tentativas erradas.
  * A chave do Supabase (service_role) só existe no servidor; nunca vai para o navegador.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import requests
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image
from google import genai
from google.genai import types

import importlib
import banco as _banco_mod
importlib.reload(_banco_mod)  # pega mudanças do banco.py sem reiniciar o painel
import midia
import atendimento
import padrao_visual as pv
import publicador
importlib.reload(midia)          # pegam mudanças dos arquivos sem reiniciar o painel
importlib.reload(pv)
importlib.reload(publicador)
importlib.reload(atendimento)
from meta import Meta, MetaErro
from banco import Banco, parse_data, validar_agendamento

try:
    import pypdf
    PDF_DISPONIVEL = True
except ImportError:
    PDF_DISPONIVEL = False

try:
    import docx
    DOCX_DISPONIVEL = True
except ImportError:
    DOCX_DISPONIVEL = False

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

st.set_page_config(page_title="Central de Operações AI", page_icon="🚀", layout="wide")

# ==========================================================================
# Constantes
# ==========================================================================
MODELO_TEXTO_PADRAO = "gemini-3.6-flash"
MODELOS_TEXTO_RESERVA = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.8-flash"]   # usados se o principal estiver sobrecarregado
MODELO_IMAGEM_PADRAO = "gemini-nano-banana-2.1"
PASTA_APP = Path(__file__).resolve().parent
ARQUIVO_LEGADO = PASTA_APP / "empresas.json"

SENHA_MINIMA = 12
MAX_FALHAS_LOGIN = 5
JANELA_FALHAS_SEG = 600
SESSAO_HORAS = 8
COOKIE_LOGIN = "cco_sessao"
DIAS_LEMBRAR = 7
MAX_CHARS_DOCUMENTOS = 60_000
MAX_LOGO_BYTES = 5 * 1024 * 1024

NOMES_SEGREDOS = ["GEMINI_API_KEY", "SUPABASE_SERVICE_ROLE_KEY", "APP_PASSWORD", "N8N_TOKEN", "META_SYSTEM_TOKEN"]

OBJ_MARCA = "Marca e engajamento"
OBJ_WHATSAPP = "Gerar conversas no WhatsApp (clique para conversar)"
OBJETIVOS = [OBJ_MARCA, OBJ_WHATSAPP]
CANAIS = ["Instagram", "Facebook", "LinkedIn", "TikTok", "X (Twitter)", "WhatsApp", "E-mail Marketing"]
FORMATOS = ["Arte Estática", "Carrossel Estratégico", "Roteiro de Vídeo Curto", "Texto Direto"]
FORMATO_CARROSSEL = "Carrossel Estratégico"
FORMATO_DB = {"Arte Estática": "image", "Carrossel Estratégico": "carousel",
              "Roteiro de Vídeo Curto": "video", "Texto Direto": "text"}
TONS = ["Vendedor & Agressivo", "Educativo & Consultivo", "Informativo & Noticioso", "Premium & Exclusivo"]
POSICOES = {"Canto Inferior Direito (Recomendado)": "bottom_right", "Canto Superior Direito": "top_right",
            "Canto Inferior Esquerdo": "bottom_left", "Canto Superior Esquerdo": "top_left"}
STATUS_EMPRESA = {"active": "Ativa", "onboarding": "Em implantação", "paused": "Pausada", "archived": "Arquivada"}
STATUS_POST = {
    "draft": "📝 Rascunho", "pending_approval": "⏳ Aguardando aprovação do cliente",
    "scheduled": "📅 Agendado", "publishing": "🚀 Publicando", "published": "✅ Publicado",
    "partially_published": "⚠️ Publicado em parte", "failed": "❌ Falhou", "cancelled": "🚫 Cancelado",
}
STATUS_ALVO = {"pending": "⏳ na fila", "processing": "🔄 enviando", "published": "✅ publicado",
               "failed": "❌ falhou", "cancelled": "🚫 cancelado"}


# ==========================================================================
# Segredos e mensagens seguras
# ==========================================================================
def segredo(nome: str, padrao: str = "") -> str:
    """Lê um segredo de .streamlit/secrets.toml (ou variável de ambiente). Nunca imprime o valor."""
    try:
        if nome in st.secrets:
            return str(st.secrets[nome]).strip()
    except Exception:
        pass
    return os.environ.get(nome, padrao).strip()


def erro_seguro(e: Exception) -> str:
    """Texto de erro para a tela, com qualquer segredo trocado por ***."""
    texto = str(e)
    for nome in NOMES_SEGREDOS:
        valor = segredo(nome)
        if len(valor) >= 6:
            texto = texto.replace(valor, "***")
    return texto[:600]


def _dbg(msg: str) -> None:
    """Anota o andamento em diagnostico.log (sem segredos) para facilitar o suporte."""
    try:
        with open(PASTA_APP / "diagnostico.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except Exception:  # noqa: BLE001
        pass


# ==========================================================================
# Login (senha única)
# ==========================================================================
@st.cache_resource
def _controle_tentativas() -> dict:
    return {"falhas": []}


def _chave_cookie(senha: str) -> bytes:
    return hashlib.sha256(("cco-sessao-v1|" + senha).encode("utf-8")).digest()


def _gerar_token(senha: str, dias: int = DIAS_LEMBRAR, agora: float | None = None) -> str:
    """Token assinado (validade + assinatura). Trocar a senha invalida todos os tokens."""
    validade = int((agora if agora is not None else time.time()) + dias * 86400)
    assinatura = hmac.new(_chave_cookie(senha), str(validade).encode(), hashlib.sha256).hexdigest()
    return f"{validade}.{assinatura}"


def _token_valido(token: str, senha: str, agora: float | None = None) -> bool:
    try:
        validade_txt, assinatura = (token or "").split(".", 1)
        validade = int(validade_txt)
    except (ValueError, AttributeError):
        return False
    esperado = hmac.new(_chave_cookie(senha), str(validade).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(assinatura, esperado) and validade > (agora if agora is not None else time.time())


def _lembrado(senha: str) -> bool:
    """True se o navegador trouxe um cookie de login valido."""
    try:
        return _token_valido(st.context.cookies.get(COOKIE_LOGIN, ""), senha)
    except Exception:  # versao antiga do Streamlit ou sem cookies
        return False


def _gravar_cookie(valor: str, dias: int) -> None:
    """Grava (ou apaga, com valor vazio) o cookie de login no navegador."""
    segundos = dias * 86400 if valor else 0
    components.html(
        "<script>try{window.parent.document.cookie="
        f"'{COOKIE_LOGIN}={valor}; path=/; max-age={segundos}; SameSite=Strict';"
        "}catch(e){}</script>", height=0)


def exigir_login() -> None:
    senha = segredo("APP_PASSWORD")
    if len(senha) < SENHA_MINIMA:
        st.error(f"🔒 Painel bloqueado: defina `APP_PASSWORD` com pelo menos {SENHA_MINIMA} caracteres "
                 "em `.streamlit/secrets.toml` e reinicie o app.")
        st.stop()
    if st.session_state.get("auth_expira", 0) > time.time():
        token = st.session_state.pop("_gravar_cookie", None)
        if token:
            _gravar_cookie(token, DIAS_LEMBRAR)
        return
    if st.session_state.pop("_apagar_cookie", False):
        _gravar_cookie("", 0)          # acabou de clicar em Sair: nao entrar sozinho
    elif _lembrado(senha):
        st.session_state["auth_expira"] = time.time() + SESSAO_HORAS * 3600
        return

    st.title("🔒 Central de Operações AI")
    st.caption("Acesso restrito à equipe da agência.")
    with st.form("form_login"):
        digitada = st.text_input("Senha de acesso", type="password")
        lembrar = st.checkbox(f"Manter conectado neste computador por {DIAS_LEMBRAR} dias", value=True)
        entrar = st.form_submit_button("Entrar")
    if entrar:
        agora = time.time()
        ctrl = _controle_tentativas()
        ctrl["falhas"] = [t for t in ctrl["falhas"] if agora - t < JANELA_FALHAS_SEG]
        if len(ctrl["falhas"]) >= MAX_FALHAS_LOGIN:
            st.error("Muitas tentativas incorretas. Aguarde alguns minutos e tente de novo.")
        elif hmac.compare_digest(digitada.encode("utf-8"), senha.encode("utf-8")):
            ctrl["falhas"].clear()
            st.session_state["auth_expira"] = agora + SESSAO_HORAS * 3600
            if lembrar:
                st.session_state["_gravar_cookie"] = _gerar_token(senha)
            st.rerun()
        else:
            ctrl["falhas"].append(agora)
            time.sleep(1)
            st.error("Senha incorreta.")
    st.stop()


exigir_login()

# ==========================================================================
# Conexões (Supabase e Gemini)
# ==========================================================================
faltando = [n for n in ("GEMINI_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY") if not segredo(n)]
if faltando:
    st.error("Faltam estes itens em `.streamlit/secrets.toml`: " + ", ".join(f"`{n}`" for n in faltando))
    st.code('GEMINI_API_KEY = "..."\nSUPABASE_URL = "https://SEU-PROJETO.supabase.co"\n'
            'SUPABASE_SERVICE_ROLE_KEY = "..."\nAPP_PASSWORD = "..."\nN8N_TOKEN = "..."  # opcional', language="toml")
    st.stop()


@st.cache_resource(show_spinner=False)
def _cliente_supabase(url: str, chave: str):
    from supabase import create_client
    return create_client(url, chave)


@st.cache_resource(show_spinner=False)
def _cliente_gemini(chave: str):
    return genai.Client(api_key=chave, http_options=types.HttpOptions(timeout=90_000))


@st.cache_resource(show_spinner=False)
def _cliente_meta(token: str):
    return Meta(token, versao=segredo("META_GRAPH_VERSION") or "v23.0")


def cliente_meta():
    """Cliente da Meta com o token do usuário do sistema (ou None se ainda não foi configurado)."""
    token = segredo("META_SYSTEM_TOKEN")
    if not token:
        return None
    try:
        return _cliente_meta(token)
    except MetaErro:
        return None


try:
    db = Banco(_cliente_supabase(segredo("SUPABASE_URL"), segredo("SUPABASE_SERVICE_ROLE_KEY")))
    db.ping()
except Exception as e:
    st.error("Não consegui falar com o Supabase. Confira `SUPABASE_URL` e `SUPABASE_SERVICE_ROLE_KEY` e se os "
             "scripts SQL 001 e 002 foram executados.")
    st.caption(erro_seguro(e))
    st.stop()

gemini = _cliente_gemini(segredo("GEMINI_API_KEY"))
MODELO_TEXTO = segredo("GEMINI_MODEL") or MODELO_TEXTO_PADRAO
MODELO_IMAGEM = segredo("GEMINI_IMAGE_MODEL") or MODELO_IMAGEM_PADRAO


# ==========================================================================
# Utilidades
# ==========================================================================
def com_retentativa(funcao, tentativas: int = 3, espera: float = 2.0):
    """Repete chamadas que podem falhar por instabilidade (usar só em operações seguras de repetir)."""
    ultimo = None
    for i in range(tentativas):
        try:
            return funcao()
        except Exception as e:  # noqa: BLE001
            ultimo = e
            if i < tentativas - 1:
                time.sleep(espera * (i + 1))
    raise ultimo


def gerar_texto(prompt: str, config):
    """Gera o texto no modelo principal; se estiver sobrecarregado (503/429/504), passa para os de reserva.
    Faz ate 2 rodadas pela lista, com uma pausa curta entre elas."""
    ultimo = None
    modelos = [MODELO_TEXTO] + [m for m in MODELOS_TEXTO_RESERVA if m != MODELO_TEXTO]
    for rodada in range(2):
        for modelo in modelos:
            try:
                _dbg(f"texto: tentando {modelo} (rodada {rodada + 1})")
                resposta = gemini.models.generate_content(model=modelo, contents=prompt, config=config)
                _dbg(f"texto: OK com {modelo}")
                return resposta
            except Exception as e:  # noqa: BLE001
                ultimo = e
                _dbg(f"texto: {modelo} falhou: {erro_seguro(e)[:200]}")
        if rodada == 0:
            time.sleep(5)
    raise ultimo


def fuso(empresa: dict):
    nome = empresa.get("timezone") or "America/Sao_Paulo"
    try:
        return ZoneInfo(nome)
    except Exception:
        return timezone(timedelta(hours=-3))


def cfg(empresa: dict, chave: str, padrao: str = "") -> str:
    return (empresa.get("settings") or {}).get(chave) or padrao


def cores_da_empresa(empresa: dict) -> str:
    return (empresa.get("brand_colors") or {}).get("descricao") or "cores institucionais da marca"


@st.cache_data(ttl=3600, show_spinner=False, max_entries=64)
def baixar_logo(url: str):
    if not url:
        return None
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200 and len(r.content) < 6 * 1024 * 1024:
            return r.content
    except Exception:
        pass
    return None


def preparar_logo(arquivo) -> bytes:
    """Valida a logo enviada e devolve um PNG limpo (sem metadados)."""
    dados = arquivo.getvalue()
    if len(dados) > MAX_LOGO_BYTES:
        raise ValueError("A logo deve ter até 5 MB.")
    img = Image.open(BytesIO(dados))
    img.load()
    img = img.convert("RGBA")
    img.thumbnail((2000, 2000))
    saida = BytesIO()
    img.save(saida, format="PNG")
    return saida.getvalue()


@st.cache_data(show_spinner=False, max_entries=64)
def extrair_texto_documento(file_bytes: bytes, filename: str):
    """Extrai texto de PDF, Word, TXT, CSV e MD. Devolve (texto, erro)."""
    ext = filename.rsplit(".", 1)[-1].lower()
    try:
        if ext in ("txt", "csv", "md"):
            try:
                return file_bytes.decode("utf-8"), ""
            except UnicodeDecodeError:
                return file_bytes.decode("latin-1"), ""
        if ext == "pdf":
            if not PDF_DISPONIVEL:
                return "", "Instale 'pypdf' (pip install pypdf) para ler PDFs."
            leitor = pypdf.PdfReader(BytesIO(file_bytes))
            return "\n".join(p.extract_text() or "" for p in leitor.pages), ""
        if ext in ("docx", "doc"):
            if not DOCX_DISPONIVEL:
                return "", "Instale 'python-docx' (pip install python-docx) para ler arquivos Word."
            documento = docx.Document(BytesIO(file_bytes))
            return "\n".join(p.text for p in documento.paragraphs if p.text.strip()), ""
    except Exception as e:  # noqa: BLE001
        return "", f"Erro ao processar {filename}: {e}"
    return "", ""


def aplicar_logo(imagem: Image.Image, logo_bytes: bytes | None, posicao: str, escala: float = 0.18) -> Image.Image:
    """Estampa a logo da empresa sobre a arte."""
    if not logo_bytes:
        return imagem
    try:
        base = imagem.convert("RGBA")
        logo = Image.open(BytesIO(logo_bytes)).convert("RGBA")
        largura = int(base.size[0] * escala)
        altura = int(logo.size[1] * (largura / float(logo.size[0])))
        logo = logo.resize((largura, altura), Image.Resampling.LANCZOS)
        margem = int(base.size[0] * 0.04)
        pos = {
            "bottom_right": (base.size[0] - largura - margem, base.size[1] - altura - margem),
            "top_right": (base.size[0] - largura - margem, margem),
            "bottom_left": (margem, base.size[1] - altura - margem),
        }.get(posicao, (margem, margem))
        camada = Image.new("RGBA", base.size, (255, 255, 255, 0))
        camada.paste(logo, pos, mask=logo)
        return Image.alpha_composite(base, camada).convert("RGB")
    except Exception:
        return imagem


def para_jpeg(imagem: Image.Image, qualidade: int = 92) -> bytes:
    saida = BytesIO()
    imagem.convert("RGB").save(saida, format="JPEG", quality=qualidade)
    return saida.getvalue()


# ==========================================================================
# Geração de conteúdo (Gemini)
# ==========================================================================
def montar_prompt(empresa: dict, briefing: str, texto_docs: str, canal: str, formato: str,
                  tom_campanha: str, tema: str, publico: str, objetivo: str = "") -> str:
    extras = ""
    if empresa.get("tone_of_voice"):
        extras += f"\n**TOM DE VOZ DA MARCA:**\n{empresa['tone_of_voice']}\n"
    if empresa.get("business_rules"):
        extras += f"\n**REGRAS E RESTRIÇÕES (OBRIGATÓRIO RESPEITAR):**\n{empresa['business_rules']}\n"
    bloco_wa, campo_wa = "", ""
    if objetivo == OBJ_WHATSAPP:
        bloco_wa = ("- OBJETIVO: levar o público a CLICAR e iniciar uma conversa no WhatsApp da empresa para "
                    "conversar com um atendente e fechar negócio. Todas as legendas devem ter UMA chamada para ação "
                    "clara para chamar no WhatsApp (sem pressão e respeitando as regras da marca). Essa é a ÚNICA chamada: "
                    "NÃO cite 'link da bio', site, telefone nem outros canais.\n")
        campo_wa = (',\n  "whatsapp": {\n'
                    '    "mensagem_inicial": "texto curto e natural que o cliente enviará ao clicar '
                    '(ex.: Olá! Vi o anúncio e quero fazer uma simulação)",\n'
                    '    "boas_vindas": "primeira resposta do atendimento da empresa, acolhedora, em até 3 frases",\n'
                    '    "perguntas_qualificacao": ["3 perguntas curtas para entender o cliente e avançar para a proposta"],\n'
                    '    "fechamento": "mensagem final convidando para o próximo passo (simulação/envio de '
                    'documentos), sem prometer aprovação"\n  }')
    bloco_slides, campo_slides = "", ""
    if formato == FORMATO_CARROSSEL:
        bloco_slides = pv.bloco_redator_slides(pv.SLIDES_PADRAO) + "\n"
        campo_slides = (',\n  "slides": [\n'
                        '    {"papel": "capa", "texto_na_arte": "gancho curto, até 60 caracteres", '
                        '"cena": "descrição da cena em português", "prompt_imagem": "prompt EM INGLÊS deste slide"},\n'
                        '    {"papel": "conteudo", "texto_na_arte": "...", "cena": "...", "prompt_imagem": "..."},\n'
                        '    {"papel": "cta", "texto_na_arte": "...", "cena": "...", "prompt_imagem": "..."}\n'
                        f'  ]  (exatamente {pv.SLIDES_PADRAO} itens: 1 capa, {pv.SLIDES_PADRAO - 2} de conteudo e 1 cta)')
    return f"""
Você é o Diretor Executivo de Marketing da marca **{empresa['name']}**.
Crie o conteúdo completo para a campanha.

**DIRETRIZES E BRIEFING DA EMPRESA:**
{briefing or 'Sem briefing cadastrado.'}
{extras}
**PALETA DE CORES DA MARCA:**
{cores_da_empresa(empresa)}

**CONTEÚDO DOS DOCUMENTOS ANEXADOS:**
{texto_docs if texto_docs else 'Nenhum documento anexado.'}

**PARÂMETROS DA CAMPANHA:**
- Canal: {canal}
- Formato: {formato}
- Tom de voz da campanha: {tom_campanha}
- Tema: {tema}
- Público: {publico or 'Público principal da marca'}
{bloco_wa}
- RESSALVAS OBRIGATÓRIAS: se as regras da marca exigirem uma ressalva (ex.: "sujeito a análise de crédito") sempre que \
houver menção a crédito, simulação ou valores, inclua-a em TODAS as legendas (e no texto do último slide do carrossel, \
se couber) e não prometa aprovação, taxa ou prazo.
{pv.bloco_redator_prompts()}
{pv.REGRAS_LEGENDA}
{bloco_slides}Responda SOMENTE com um JSON válido, neste formato exato:
{{
  "conceito_visual": "descrição curta do conceito visual, em português",
  "prompt_imagem": "prompt detalhado EM INGLÊS para gerar a imagem, usando as cores da marca, seguindo o PADRÃO DE ARTE, sem texto escrito na imagem e sem logotipos",
  "prompt_video": "roteiro do vídeo em português seguindo o PADRÃO DE VÍDEO: cenário, pessoa, luz, câmera, fala exata entre aspas, direção do olhar e para onde aponta",
  "legendas": [
    {{"titulo": "Conversão e Urgência", "texto": "legenda pronta para publicar"}},
    {{"titulo": "Educativa e Proposta de Valor", "texto": "legenda pronta para publicar"}},
    {{"titulo": "Curta e Focada em Engajamento", "texto": "legenda pronta para publicar"}}
  ],
  "hashtags": ["#exemplo1", "#exemplo2"]{campo_wa}{campo_slides}
}}
""".strip()


def interpretar_conteudo(texto: str):
    """Transforma a resposta do Gemini em dicionário. Devolve None se não for um JSON utilizável."""
    bruto = re.sub(r"^```(?:json)?\s*|\s*```$", "", (texto or "").strip(), flags=re.I)
    try:
        dados = json.loads(bruto)
    except Exception:
        return None
    if not isinstance(dados, dict):
        return None
    legendas = []
    for item in dados.get("legendas") or []:
        if isinstance(item, dict):
            t, titulo = str(item.get("texto", "")).strip(), str(item.get("titulo", "")).strip()
        else:
            t, titulo = str(item).strip(), ""
        if t:
            legendas.append({"titulo": titulo or f"Opção {len(legendas) + 1}", "texto": t})
    if not legendas:
        return None
    tags = dados.get("hashtags") or []
    if isinstance(tags, str):
        tags = tags.split()
    tags = ["#" + str(h).strip().lstrip("#") for h in tags if str(h).strip()]
    wa = None
    bruto_wa = dados.get("whatsapp")
    if isinstance(bruto_wa, dict):
        perguntas = [str(q).strip() for q in (bruto_wa.get("perguntas_qualificacao") or []) if str(q).strip()]
        wa = {"mensagem_inicial": str(bruto_wa.get("mensagem_inicial", "")).strip(),
              "boas_vindas": str(bruto_wa.get("boas_vindas", "")).strip(),
              "perguntas_qualificacao": perguntas[:5],
              "fechamento": str(bruto_wa.get("fechamento", "")).strip()}
    slides = []
    for it in (dados.get("slides") or [])[:pv.MAX_SLIDES]:
        if isinstance(it, dict):
            papel = str(it.get("papel", "")).strip().lower()
            papel = papel if papel in pv.PAPEIS_SLIDE else "conteudo"
            slides.append({"papel": papel, "texto": " ".join(str(it.get("texto_na_arte") or it.get("texto") or "").split())[:90],
                           "cena": str(it.get("cena", "")).strip(), "prompt_imagem": str(it.get("prompt_imagem", "")).strip()})
    return {"conceito_visual": str(dados.get("conceito_visual", "")).strip(),
            "prompt_imagem": str(dados.get("prompt_imagem", "")).strip(),
            "prompt_video": str(dados.get("prompt_video", "")).strip(),
            "legendas": legendas, "hashtags": tags, "whatsapp": wa, "slides": slides}


def _imagem_pelo_gemini(prompt: str, vertical: bool) -> Image.Image:
    """Gera a arte com o modelo de imagem do Gemini. Lanca erro se nao vier imagem."""
    proporcao = "9:16" if vertical else "1:1"
    erros = []
    inicio = time.time()
    # 1) Caminho padrao (generate_content), pedindo so imagem e depois texto+imagem
    for modalidades in (["IMAGE"], ["TEXT", "IMAGE"]):
        if time.time() - inicio > 100:
            break
        try:
            _dbg(f"imagem: tentando {MODELO_IMAGEM} modalidades={modalidades}")
            args = {"response_modalities": modalidades}
            if hasattr(types, "ImageConfig"):
                args["image_config"] = types.ImageConfig(aspect_ratio=proporcao)
            resposta = gemini.models.generate_content(
                model=MODELO_IMAGEM,
                contents=prompt if hasattr(types, "ImageConfig")
                else f"{prompt}\nAspect ratio {proporcao}.",
                config=types.GenerateContentConfig(**args))
            for cand in (resposta.candidates or []):
                for parte in ((cand.content.parts or []) if cand.content else []):
                    dados = getattr(getattr(parte, "inline_data", None), "data", None)
                    if dados:
                        _dbg(f"imagem: OK em {time.time() - inicio:.1f}s")
                        return Image.open(BytesIO(dados))
            erros.append("o Gemini respondeu sem imagem")
            _dbg("imagem: resposta sem imagem")
        except Exception as e:  # noqa: BLE001
            erros.append(erro_seguro(e))
            _dbg(f"imagem: erro em {time.time() - inicio:.1f}s: {erro_seguro(e)[:300]}")
            if any(c in str(e) for c in ("429", "RESOURCE_EXHAUSTED", "403", "PERMISSION_DENIED", "404", "NOT_FOUND")):
                break   # cota, permissao ou modelo inexistente: repetir nao adianta
    # 2) Caminho alternativo (Interactions API), se a biblioteca tiver
    if hasattr(gemini, "interactions") and time.time() - inicio <= 100 and not any(
            c in " ".join(erros) for c in ("429", "RESOURCE_EXHAUSTED", "403", "PERMISSION_DENIED")):
        try:
            import base64
            it = gemini.interactions.create(model=MODELO_IMAGEM, input=f"{prompt}\nAspect ratio {proporcao}.")
            return Image.open(BytesIO(base64.b64decode(it.output_image.data)))
        except Exception as e:  # noqa: BLE001
            erros.append(erro_seguro(e))
    raise RuntimeError(" | ".join(dict.fromkeys(erros))[:500])


def gerar_imagem(prompt: str, vertical: bool):
    """Gera a arte no Gemini; se falhar, usa um servico reserva. Devolve (imagem, aviso)."""
    motivo = ""
    _dbg("imagem: inicio")
    try:
        return _imagem_pelo_gemini(prompt, vertical), ""
    except Exception as e:  # noqa: BLE001
        motivo = erro_seguro(e)
        _dbg(f"imagem: Gemini falhou ({motivo[:300]}); indo para o servico reserva")
    try:
        largura, altura = (768, 1344) if vertical else (1024, 1024)
        url = (f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt[:900])}"
               f"?width={largura}&height={altura}&nologo=true&seed={int(time.time())}")
        r = requests.get(url, timeout=(5, 20), stream=True)
        if r.status_code == 200:
            prazo, dados = time.time() + 45, b""
            for pedaco in r.iter_content(65536):
                dados += pedaco
                if time.time() > prazo or len(dados) > 8 * 1024 * 1024:
                    raise TimeoutError("o servico reserva demorou demais")
            return Image.open(BytesIO(dados)), (
                "⚠️ ARTE PROVISÓRIA (serviço reserva, com marca d'água) — não publicar. "
                f"O Gemini não gerou a imagem. Motivo: {motivo}")
    except Exception as e:  # noqa: BLE001
        return None, f"Não foi possível gerar a arte. Gemini: {motivo}. Reserva: {erro_seguro(e)}"
    return None, f"Não foi possível gerar a arte agora. Motivo: {motivo}"


# ==========================================================================
# Barra lateral
# ==========================================================================
st.sidebar.title("🚀 Central de Operações AI")
st.sidebar.caption("Painel interno da agência")

PAGINAS = ["☀️ Campanha de energia", "📊 Criar campanha", "🗂️ Fila de posts", "💬 Atendimento", "📈 Métricas", "🏢 Empresas", "🧾 Logs"]
pagina = st.sidebar.radio("Menu", PAGINAS)

empresas = db.listar_empresas()
empresa = None
if empresas:
    por_id = {e["id"]: e for e in empresas}
    escolhida = st.sidebar.selectbox(
        "Empresa ativa", list(por_id.keys()), format_func=lambda i: por_id[i]["name"])
    empresa = por_id[escolhida]

st.sidebar.markdown("---")
if st.sidebar.button("Sair"):
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    st.session_state["_apagar_cookie"] = True
    st.rerun()


def bloco_logo(e: dict, onde: str) -> None:
    """Caixa para enviar ou trocar a logo da empresa (usada em mais de uma tela)."""
    tem_logo = bool(baixar_logo(e.get("logo_url") or ""))
    titulo = "🖼️ Trocar a logo da empresa" if tem_logo else "🖼️ Enviar a logo da empresa (clique aqui)"
    with st.expander(titulo, expanded=not tem_logo):
        arq = st.file_uploader(
            "Escolha o arquivo da logo (PNG com fundo transparente fica melhor; JPG também serve)",
            type=["png", "jpg", "jpeg"], key=f"logo_up_{onde}_{e['id']}")
        if arq is not None:
            st.image(arq, width=140, caption="Pré-visualização")
            if st.button("💾 Salvar esta logo", type="primary", key=f"logo_ok_{onde}_{e['id']}"):
                try:
                    db.enviar_logo(e["id"], preparar_logo(arq))
                    db.registrar_evento(e["id"], "tenant.logo_updated", "Logo da empresa atualizada")
                    st.success("Logo salva!")
                    time.sleep(0.6)
                    st.rerun()
                except Exception as err:  # noqa: BLE001
                    st.error(f"Não foi possível salvar a logo: {erro_seguro(err)}")


# ==========================================================================
# Página: Empresas
# ==========================================================================
def pagina_empresas() -> None:
    st.title("🏢 Empresas")
    st.caption("Cadastro das marcas atendidas pela agência. As empresas não acessam este painel.")
    abas = st.tabs(["✏️ Editar empresa ativa", "➕ Cadastrar nova", "📥 Importar do empresas.json",
                    "🔗 Redes sociais"])

    with abas[0]:
        if not empresa:
            st.info("Nenhuma empresa cadastrada ainda. Use a aba 'Cadastrar nova'.")
        else:
            e = empresa
            with st.form(f"editar_{e['id']}"):
                c1, c2 = st.columns(2)
                nome = c1.text_input("Nome da empresa", value=e["name"])
                nicho = c1.text_input("Nicho / setor", value=cfg(e, "nicho"))
                cores = c2.text_input("Cores oficiais da marca", value=(e.get("brand_colors") or {}).get("descricao", ""))
                status = c2.selectbox("Situação", list(STATUS_EMPRESA.keys()),
                                      index=list(STATUS_EMPRESA.keys()).index(e["status"]) if e["status"] in STATUS_EMPRESA else 0,
                                      format_func=lambda s: STATUS_EMPRESA[s])
                briefing = st.text_area("Briefing institucional", value=cfg(e, "briefing"), height=120)
                tom = st.text_area("Tom de voz da marca (como a marca fala)", value=e.get("tone_of_voice") or "", height=80)
                regras = st.text_area("Regras e restrições (o que NÃO pode ser dito/prometido)",
                                      value=e.get("business_rules") or "", height=80)
                whatsapp = st.text_input("WhatsApp Business da empresa (com DDD)", value=cfg(e, "whatsapp"),
                                         placeholder="Ex.: 21 99999-9999",
                                         help="Usado nas campanhas de clique para conversar no WhatsApp.")
                webhook = st.text_input("URL do webhook do n8n (modo antigo, opcional)", value=cfg(e, "n8n_webhook"))
                salvar = st.form_submit_button("💾 Salvar alterações", type="primary")
            if e.get("logo_url"):
                logo_atual = baixar_logo(e["logo_url"])
                if logo_atual:
                    st.image(logo_atual, width=140, caption="Logo atual")
            bloco_logo(e, "empresas")
            if salvar:
                try:
                    db.atualizar_empresa(e, nome, nicho, cores, briefing, webhook.strip(), tom, regras, status, whatsapp.strip())
                    db.registrar_evento(e["id"], "tenant.updated", "Cadastro da empresa atualizado")
                    st.success("Empresa atualizada.")
                    time.sleep(0.6)
                    st.rerun()
                except Exception as err:  # noqa: BLE001
                    st.error(f"Não foi possível salvar: {erro_seguro(err)}")

    with abas[1]:
        with st.form("nova_empresa", clear_on_submit=True):
            c1, c2 = st.columns(2)
            nome = c1.text_input("Nome da empresa *")
            nicho = c1.text_input("Nicho / setor")
            cores = c2.text_input("Cores oficiais da marca")
            whatsapp = c1.text_input("WhatsApp Business (com DDD)", placeholder="Ex.: 21 99999-9999")
            webhook = c2.text_input("URL do webhook do n8n (opcional)")
            briefing = st.text_area("Briefing institucional *", height=120)
            tom = st.text_area("Tom de voz da marca", height=70)
            regras = st.text_area("Regras e restrições", height=70)
            logo = st.file_uploader("Logotipo", type=["png", "jpg", "jpeg"])
            criar = st.form_submit_button("💾 Cadastrar empresa", type="primary")
        if criar:
            if not nome.strip() or not briefing.strip():
                st.warning("Preencha pelo menos o nome e o briefing.")
            else:
                try:
                    nova = db.criar_empresa(nome, nicho, cores, briefing, webhook.strip(), tom, regras, whatsapp.strip())
                    if logo is not None:
                        db.enviar_logo(nova["id"], preparar_logo(logo))
                    db.registrar_evento(nova["id"], "tenant.created", "Empresa cadastrada")
                    st.success(f"Empresa '{nova['name']}' cadastrada. Selecione-a na barra lateral.")
                except Exception as err:  # noqa: BLE001
                    st.error(f"Não foi possível cadastrar: {erro_seguro(err)}")

    with abas[2]:
        if not ARQUIVO_LEGADO.exists():
            st.info("Não há arquivo empresas.json nesta pasta.")
        else:
            try:
                legado = json.loads(ARQUIVO_LEGADO.read_text(encoding="utf-8"))
            except Exception as err:  # noqa: BLE001
                legado = {}
                st.error(f"Não consegui ler o empresas.json: {erro_seguro(err)}")
            if legado:
                st.write("Empresas encontradas no arquivo antigo: " + ", ".join(f"**{n}**" for n in legado))
                st.caption("Só preenche campos que estão vazios no Supabase. Logos locais também são enviadas.")
                if st.button("📥 Importar para o Supabase"):
                    for nome, dados in legado.items():
                        try:
                            resultado = db.importar_empresa_legada(nome, dados)
                            logo_rel = (dados.get("logo_path") or "").replace("\\", "/")
                            caminho = (PASTA_APP / logo_rel) if logo_rel else None
                            if caminho and caminho.exists():
                                alvo = db.id_empresa_por_nome(nome)
                                if alvo and not alvo.get("logo_url"):
                                    class _Arq:  # reaproveita a validação de logo
                                        def __init__(self, b): self._b = b
                                        def getvalue(self): return self._b
                                    db.enviar_logo(alvo["id"], preparar_logo(_Arq(caminho.read_bytes())))
                            st.success(f"{nome}: {resultado}.")
                        except Exception as err:  # noqa: BLE001
                            st.error(f"{nome}: {erro_seguro(err)}")
                    time.sleep(0.8)
                    st.rerun()


    with abas[3]:
        aba_redes_sociais()


def aba_redes_sociais() -> None:
    if not empresa:
        st.info("Cadastre uma empresa primeiro.")
        return
    e = empresa
    st.subheader(f"Contas conectadas — {e['name']}")
    contas = db.credenciais_da_empresa(e["id"])
    if contas:
        for c in contas:
            st.markdown(f"- **{c['platform'].title()}** — {c['label']} · {c['status']}")
    else:
        st.caption("Nenhuma conta conectada ainda.")
    st.markdown("---")
    meta = cliente_meta()
    if not meta:
        st.warning("O token da Meta ainda não foi configurado. Rode o CONFIGURAR.bat e preencha 'Token da Meta'.")
        return
    st.caption("O painel pergunta à Meta quais Páginas e Instagram o usuário n8n-bot enxerga. "
               "Marque só as contas que pertencem a esta empresa.")
    if st.button("🔎 Procurar contas na Meta"):
        try:
            with st.spinner("Consultando a Meta..."):
                st.session_state["meta_ativos"] = meta.descobrir_ativos()
        except MetaErro as err:
            st.session_state.pop("meta_ativos", None)
            st.error(f"A Meta recusou a consulta: {erro_seguro(err)}")
    ativos = st.session_state.get("meta_ativos")
    if ativos is None:
        return
    if not ativos:
        st.info("A Meta não devolveu nenhuma Página para este token. Confira se o n8n-bot tem acesso total à Página.")
        return
    escolhas = []
    for a in ativos:
        st.markdown(f"**Página:** {a['page_name']}")
        if st.checkbox(f"Facebook — {a['page_name']}", key=f"cx_fb_{e['id']}_{a['page_id']}"):
            escolhas.append(("facebook", f"Facebook — {a['page_name']}", a["page_id"], a["page_name"], None))
        if a.get("ig_id"):
            if st.checkbox(f"Instagram @{a['ig_username']}", key=f"cx_ig_{e['id']}_{a['ig_id']}"):
                escolhas.append(("instagram", f"Instagram @{a['ig_username']}", a["ig_id"],
                                 f"@{a['ig_username']}", a["page_id"]))
        else:
            st.caption("Sem Instagram profissional ligado a esta Página.")
    if escolhas and st.button(f"💾 Conectar as selecionadas a {e['name']}", type="primary"):
        try:
            for plataforma, rotulo, ext, nome, pai in escolhas:
                db.conectar_conta(e["id"], plataforma, rotulo, ext, nome, pai)
            db.registrar_evento(e["id"], "social.connected", f"{len(escolhas)} conta(s) conectada(s)")
            st.success("Contas conectadas! Agora elas aparecem como destino ao salvar e na Fila de posts.")
            time.sleep(0.8)
            st.rerun()
        except Exception as err:  # noqa: BLE001
            st.error(f"Não consegui conectar: {erro_seguro(err)}")


# ==========================================================================
# Página: Criar campanha
# ==========================================================================
def montar_prompt_redator(texto: str, empresa: dict) -> str:
    regras = empresa.get("business_rules") or "Sem regras específicas."
    return f"""
Você é redator e revisor de português do Brasil de uma agência de marketing.
Corrija o texto abaixo, que vai aparecer escrito sobre uma imagem ou vídeo da marca **{empresa['name']}**.

TEXTO: "{texto}"

Regras:
- Corrija ortografia, acentuação, concordância, crase e PONTUAÇÃO, mantendo o sentido, a intenção e o tom.
- Se o texto já estiver correto, devolva-o IGUAL e deixe "mudancas" vazio. Não troque nomes nem reescreva por estilo.
- Não invente informações, números, taxas, prazos ou promessas que não estejam no texto.
- Deixe curto e legível (no máximo 90 caracteres); se precisar, encurte sem mudar o sentido.
- Se o texto prometer aprovação garantida, "menor taxa do mercado" ou criar urgência falsa, reescreva de forma segura.
- Regras da marca (obrigatório respeitar): {regras}

{pv.REGRAS_PONTUACAO}
Responda SOMENTE com um JSON válido: {{"texto": "texto corrigido", "mudancas": ["o que foi corrigido, curto"]}}
""".strip()


def interpretar_redator(texto: str):
    bruto = re.sub(r"^```(?:json)?\s*|\s*```$", "", (texto or "").strip(), flags=re.I)
    try:
        d = json.loads(bruto)
    except Exception:
        return None
    if not isinstance(d, dict) or not str(d.get("texto", "")).strip():
        return None
    mud = [str(m).strip() for m in (d.get("mudancas") or []) if str(m).strip()]
    return {"texto": " ".join(str(d["texto"]).split()), "mudancas": mud[:6]}


def corrigir_texto_pt(texto: str, empresa: dict):
    """Redator: corrige o português do texto da mídia. Devolve (texto, mudancas, erro)."""
    texto = " ".join((texto or "").split())
    if not texto:
        return "", [], ""
    try:
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
        resposta = gerar_texto(montar_prompt_redator(texto, empresa), config)
        r = interpretar_redator((resposta.text or "") if resposta else "")
    except Exception as err:  # noqa: BLE001
        fixo, muds = pv.corrigir_pontuacao_texto_arte(texto)
        return fixo, ["Pontuação: " + m for m in muds], (f"Não consegui revisar o português agora ({erro_seguro(err)}). "
                                                         "Usei o seu texto, só com a pontuação ajustada.")
    if not r:
        fixo, muds = pv.corrigir_pontuacao_texto_arte(texto)
        return fixo, (["Pontuação: " + m for m in muds]), "O redator não devolveu um resultado utilizável. Usei o seu texto, só com a pontuação ajustada."
    fixo, muds = pv.corrigir_pontuacao_texto_arte(r["texto"])
    return fixo, r["mudancas"] + ["Pontuação: " + m for m in muds if m not in " ".join(r["mudancas"])], ""


def link_whatsapp(numero: str, mensagem: str):
    """Monta o link wa.me (clique para conversar). Devolve None se o número não for válido."""
    d = re.sub(r"\D", "", numero or "")
    if len(d) in (10, 11):
        d = "55" + d
    if not (len(d) in (12, 13) and d.startswith("55")):
        return None
    link = f"https://wa.me/{d}"
    if (mensagem or "").strip():
        link += "?text=" + urllib.parse.quote(mensagem.strip())
    return link


def compor_arte(base: Image.Image, texto: str, logo_bytes, posicao: str, tarja: str = "",
                info: dict | None = None) -> bytes:
    """Texto corrigido + tarja do WhatsApp + logo sobre a arte (sem sobreposição), em JPEG pronto para publicar."""
    imagem, plano = midia.compor(base, texto, logo_bytes, posicao, tarja)
    if info is not None:
        info["layout_arte"] = midia.resumo_layout(plano)
        info["padrao_arte"] = midia.checar_padrao(plano, com_logo=bool(logo_bytes))
        info["avisos_arte"] = plano.get("avisos", [])
    return para_jpeg(imagem)


def guardar_medidas(g: dict, info: dict) -> None:
    g["layout_arte"], g["padrao_arte"] = info.get("layout_arte"), info.get("padrao_arte")
    g["avisos_arte"] = info.get("avisos_arte", [])


# ==========================================================================
# Carrossel (vários slides)
# ==========================================================================
LARGURA_SLIDE = 1080


def eh_carrossel(g: dict) -> bool:
    return g.get("formato") == FORMATO_CARROSSEL


def _chave_natural(nome: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", nome or "")]


def normalizar_proporcao(imagens: list) -> list:
    """O Instagram exige a MESMA proporção em todos os slides (entre 4:5 e 1,91:1). Recorta tudo para o formato
    do primeiro slide (limitado a 4:5), preservando o topo (onde fica o rosto), e padroniza a largura."""
    if not imagens:
        return imagens
    w0, h0 = imagens[0].size
    alvo = min(max(w0 / h0, 0.8), 1.91)
    altura = int(round(LARGURA_SLIDE / alvo))
    saida = []
    for im in imagens:
        im = im.convert("RGB")
        w, h = im.size
        r = w / h
        if r > alvo + 0.005:                      # mais larga: corta as laterais
            nw = int(round(h * alvo))
            x = (w - nw) // 2
            im = im.crop((x, 0, x + nw, h))
        elif r < alvo - 0.005:                    # mais alta: corta mais embaixo que em cima
            nh = int(round(w / alvo))
            y = int((h - nh) * 0.30)
            im = im.crop((0, y, w, y + nh))
        saida.append(im.resize((LARGURA_SLIDE, altura), Image.Resampling.LANCZOS))
    return saida


def novo_slide(n: int, papel: str = "conteudo", texto: str = "", cena: str = "", prompt: str = "") -> dict:
    return {"n": n, "papel": papel, "texto": texto, "cena": cena, "prompt_imagem": prompt,
            "base": None, "arte": None, "medidas": None, "padrao": None, "avisos": []}


def slides_do_conteudo(conteudo: dict | None, tema: str = "") -> list:
    """Cria a lista de slides a partir do que o redator devolveu (ou 5 slides em branco, se não devolveu)."""
    lista = list((conteudo or {}).get("slides") or [])[:pv.MAX_SLIDES]
    if len(lista) < pv.MIN_SLIDES:
        base = pv.SLIDES_PADRAO
        lista = [{"papel": "capa" if i == 0 else ("cta" if i == base - 1 else "conteudo"), "texto": "",
                  "cena": "", "prompt_imagem": ""} for i in range(base)]
    saida = []
    for i, it in enumerate(lista):
        saida.append(novo_slide(i + 1, it.get("papel") or "conteudo", it.get("texto") or "", it.get("cena") or "",
                                it.get("prompt_imagem") or (f"Commercial photo about {tema}" if tema else "")))
    saida[0]["papel"], saida[-1]["papel"] = "capa", "cta"
    return saida


def recompor_slides(g: dict, logo_bytes, posicao: str) -> None:
    """Escreve texto + logo (e a tarja do WhatsApp só no último slide) em todos os slides que já têm imagem."""
    slides = g.get("slides") or []
    for i, s in enumerate(slides):
        s["n"] = i + 1
        if not s.get("base"):
            continue
        tarja = (g.get("tarja_whatsapp") or "") if i == len(slides) - 1 else ""
        info = {}
        s["arte"] = compor_arte(Image.open(BytesIO(s["base"])), s.get("texto") or "", logo_bytes, posicao, tarja, info)
        s["medidas"], s["padrao"], s["avisos"] = info.get("layout_arte"), info.get("padrao_arte"), info.get("avisos_arte", [])
    primeira = next((s for s in slides if s.get("arte")), None)
    g["imagem"] = primeira["arte"] if primeira else None


def montar_prompt_redator_lote(textos: list, empresa: dict) -> str:
    regras = empresa.get("business_rules") or "Sem regras específicas."
    itens = json.dumps([{"n": i + 1, "texto": t} for i, t in enumerate(textos)], ensure_ascii=False)
    return f"""
Você é redator e revisor de português do Brasil de uma agência de marketing.
Corrija os textos abaixo, cada um vai escrito sobre um SLIDE de carrossel da marca **{empresa['name']}**.

TEXTOS: {itens}

Regras (valem para cada texto):
- Corrija ortografia, acentuação, concordância, crase e PONTUAÇÃO, mantendo sentido, intenção e tom.
- Os slides são lidos em sequência: use o MESMO padrão de pontuação em todos (sem ponto final nos títulos; perguntas com ?;
  numeração em sequência, se houver).
- Se já estiver correto, devolva IGUAL e deixe "mudancas" vazio. Não reescreva por estilo.
- Não invente informações, números, taxas, prazos ou promessas.
- Curto e legível: no máximo 90 caracteres (ideal até 60); se precisar, encurte sem mudar o sentido.
- Promessa de aprovação garantida, "menor taxa do mercado" ou urgência falsa: reescreva de forma segura.
- Regras da marca (obrigatório respeitar): {regras}

{pv.REGRAS_PONTUACAO}
Responda SOMENTE com JSON válido: {{"textos": [{{"n": 1, "texto": "corrigido", "mudancas": ["curto"]}}]}} (mesma ordem e quantidade).
""".strip()


def _pontuar_lote(textos: list, muds: list) -> list:
    """Rede de segurança (sem IA): garante ? nas perguntas, sem ponto final nos títulos e o mesmo padrão em todos."""
    saida = []
    for i, t in enumerate(textos):
        fixo, m = pv.corrigir_pontuacao_texto_arte(t)
        if m and i < len(muds):
            muds[i] = list(muds[i]) + ["Pontuação: " + x for x in m if x not in " ".join(muds[i])]
        saida.append(fixo)
    return saida


def corrigir_textos_pt(textos: list, empresa: dict):
    """Redator: corrige o português dos textos de todos os slides numa só chamada. Devolve (textos, mudancas, erro)."""
    limpos = [" ".join((t or "").split()) for t in textos]
    if not any(limpos):
        return limpos, [[] for _ in limpos], ""
    try:
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
        resposta = gerar_texto(montar_prompt_redator_lote(limpos, empresa), config)
        bruto = re.sub(r"^```(?:json)?\s*|\s*```$", "", ((resposta.text or "") if resposta else "").strip(), flags=re.I)
        itens = json.loads(bruto).get("textos")
        if not isinstance(itens, list) or len(itens) != len(limpos):
            raise ValueError("quantidade diferente de textos")
        novos, muds = [], []
        for orig, it in zip(limpos, itens):
            t = " ".join(str((it or {}).get("texto", "")).split()) if isinstance(it, dict) else ""
            novos.append(t if (t or not orig) else orig)
            muds.append([str(m).strip() for m in ((it or {}).get("mudancas") or []) if str(m).strip()][:4]
                        if isinstance(it, dict) else [])
        return _pontuar_lote(novos, muds), muds, ""
    except Exception as err:  # noqa: BLE001
        muds = [[] for _ in limpos]
        return _pontuar_lote(limpos, muds), muds, (f"Não consegui revisar o português agora ({erro_seguro(err)}). "
                                                  "Usei os textos como estavam, só com a pontuação ajustada.")


def chave_revisao(g: dict, legenda_final: str | None = None) -> str:
    """Resumo de tudo o que o editor avalia. Se mudar, a revisão antiga deixa de valer."""
    c = g.get("conteudo") or {}
    corpo = legenda_final if legenda_final is not None else json.dumps(
        [l["texto"] for l in c.get("legendas", [])], ensure_ascii=False)
    h = hashlib.sha256()
    for parte in (corpo, " ".join(c.get("hashtags") or []), g.get("texto_midia") or "", g.get("objetivo") or "",
                  g.get("whatsapp_link") or "", g.get("tarja_whatsapp") or ""):
        h.update(parte.encode("utf-8"))
        h.update(b"\x00")
    for blob in (g.get("imagem"), g.get("video")):
        h.update(hashlib.sha256(blob or b"").digest())
    for sl in g.get("slides") or []:
        h.update((sl.get("texto") or "").encode("utf-8"))
        h.update(hashlib.sha256(sl.get("arte") or b"").digest())
    return h.hexdigest()[:24]


MAX_VIDEO_REVISAO = 18 * 1024 * 1024
CHECKLIST_BASE = [
    "Aderência ao briefing e ao produto",
    "Público-alvo correto",
    "Regras e restrições da marca",
    "Conformidade (promessas, taxas, prazos, urgência)",
    "Gancho e clareza da mensagem",
    "Chamada para ação",
    "Tom de voz",
    "Adequação ao canal (tamanho, formato)",
    "Ortografia e gramática",
    "Hashtags",
]
CHECKLIST_MIDIA = [
    "Qualidade e coerência visual (mídia x mensagem x público)",
    "Texto escrito na mídia (ortografia, acentuação, PONTUAÇÃO como ? e !, e legibilidade)",
    "Logo da marca (presença e posição)",
    "Marca d'água, arte provisória ou elementos proibidos",
]
CHECKLIST_WHATSAPP = [
    "Chamada para conversar no WhatsApp (clara, única, sem pressão). Se a legenda pedir também 'link da bio' ou outro canal, aponte como problema média",
    "Fluxo de conversa (boas-vindas, perguntas e fechamento coerentes e sem promessas)",
]


def montar_prompt_revisao(empresa: dict, briefing: str, canal: str, formato: str, tom: str, tema: str,
                          publico: str, conteudo: dict, extra: str = "", com_midia: bool = False,
                          com_whatsapp: bool = False) -> str:
    regras = empresa.get("business_rules") or "Sem regras específicas cadastradas."
    legendas = "\n".join(f"- [{l['titulo']}] {l['texto']}" for l in conteudo["legendas"])
    tags = " ".join(conteudo.get("hashtags") or []) or "(nenhuma)"
    lista = CHECKLIST_BASE + (CHECKLIST_MIDIA if com_midia else []) + (CHECKLIST_WHATSAPP if com_whatsapp else [])
    itens = "\n".join(f"  - {i}" for i in lista)
    return f"""
Você é um editor-chefe de marketing sênior, rigoroso e imparcial, revisando uma campanha ANTES da publicação
para a marca **{empresa['name']}**. Seu trabalho é achar problemas reais, não elogiar.

**BRIEFING DA EMPRESA:**
{briefing or 'Sem briefing cadastrado.'}

**TOM DE VOZ DA MARCA:** {empresa.get('tone_of_voice') or 'não informado'}

**REGRAS E RESTRIÇÕES (violar qualquer uma = problema de gravidade alta):**
{regras}

**PEDIDO DA CAMPANHA:** canal {canal}; formato {formato}; tom {tom}; tema "{tema}"; público "{publico or 'principal da marca'}".

**CONCEITO VISUAL:** {conteudo.get('conceito_visual') or '(não informado)'}

**LEGENDAS GERADAS:**
{legendas}

**HASHTAGS:** {tags}
{extra}
Avalie cada item do checklist (ok = true/false, com comentário curto em português):
{itens}

Julgue o que será PUBLICADO (as legendas, o conceito visual, as hashtags e a mídia anexada). Se apenas o
PEDIDO/tema pedia algo proibido mas o conteúdo publicado NÃO repete isso, registre um problema de gravidade
"media" com legenda "geral" avisando o operador do risco do tema, sem reprovar por causa disso.
Se houver imagem ou vídeo anexado, OLHE a mídia: coerência com a mensagem e o público, qualidade, texto escrito
nela (erros de português, PONTUAÇÃO faltando como "?" em pergunta, texto cortado ou ilegível; confira slide por slide
e, no vídeo, o texto na tela e a fala), presença e posição da logo, números/taxas/promessas
visuais, marca d'água e qualquer elemento que viole as regras.

Atenção especial: promessas de aprovação, "menor taxa", valores, taxas ou prazos sem a ressalva exigida,
tom de pressão ou urgência falsa, parcerias não autorizadas, informações que não estão no briefing.

Responda SOMENTE com um JSON válido, neste formato exato:
{{
  "nota": 0 a 10 (número),
  "veredito": "aprovado" ou "ajustar" ou "reprovado",
  "resumo": "2 frases em português com a opinião geral",
  "checklist": [{{"item": "nome do item", "ok": true, "comentario": "curto"}}],
  "problemas": [{{"gravidade": "alta" ou "media" ou "baixa", "legenda": "título da legenda, 'midia' ou 'geral'",
                  "descricao": "o que está errado", "sugestao": "como corrigir"}}],
  "legendas_corrigidas": [{{"titulo": "mesmo título da original", "texto": "versão corrigida pronta para publicar"}}]
}}
Regras do veredito: "reprovado" se houver problema de gravidade alta; "ajustar" se houver só média/baixa;
"aprovado" apenas se nada relevante. Em "legendas_corrigidas" devolva as legendas completas já corrigidas
(se uma legenda já está boa, repita-a igual), MANTENDO os parágrafos curtos separados por linha em branco e os
emojis no início dos parágrafos; se a legenda vier em bloco único, reorganize em parágrafos curtos com emojis.
""".strip()


def recalcular_veredito(rev: dict) -> dict:
    """O veredito sai dos problemas encontrados: não confiamos só no que a IA escreveu."""
    probs = rev["problemas"]
    if any(p["gravidade"] == "alta" for p in probs):
        rev["veredito"] = "reprovado"
    elif probs:
        rev["veredito"] = "ajustar"
    elif rev.get("veredito") not in ("aprovado", "ajustar", "reprovado"):
        rev["veredito"] = "ajustar"
    return rev


def interpretar_revisao(texto: str):
    """Transforma a resposta do revisor em dicionário seguro. Devolve None se não for utilizável."""
    bruto = re.sub(r"^```(?:json)?\s*|\s*```$", "", (texto or "").strip(), flags=re.I)
    try:
        d = json.loads(bruto)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    try:
        nota = max(0.0, min(10.0, float(str(d.get("nota")).replace(",", "."))))
    except (TypeError, ValueError):
        nota = None
    problemas = []
    for p in d.get("problemas") or []:
        if not isinstance(p, dict):
            continue
        grav = str(p.get("gravidade", "media")).strip().lower()
        grav = {"alta": "alta", "média": "media", "media": "media", "baixa": "baixa"}.get(grav, "media")
        desc = str(p.get("descricao", "")).strip()
        if desc:
            problemas.append({"gravidade": grav, "legenda": str(p.get("legenda", "geral")).strip() or "geral",
                              "descricao": desc, "sugestao": str(p.get("sugestao", "")).strip()})
    checklist = []
    for c in d.get("checklist") or []:
        if isinstance(c, dict) and str(c.get("item", "")).strip():
            checklist.append({"item": str(c["item"]).strip(), "ok": bool(c.get("ok")),
                              "comentario": str(c.get("comentario", "")).strip()})
    corrigidas = []
    for l in d.get("legendas_corrigidas") or []:
        if isinstance(l, dict) and str(l.get("texto", "")).strip():
            corrigidas.append({"titulo": str(l.get("titulo", "")).strip() or f"Opção {len(corrigidas) + 1}",
                               "texto": str(l["texto"]).strip()})
    rev = {"nota": nota, "veredito": str(d.get("veredito", "")).strip().lower(),
           "resumo": str(d.get("resumo", "")).strip(), "checklist": checklist, "problemas": problemas,
           "legendas_corrigidas": corrigidas}
    return recalcular_veredito(rev)


def regras_fixas(rev: dict, g: dict) -> dict:
    """Regras que não dependem da IA: arte provisória (com marca d'água) nunca pode ser publicada."""
    if g.get("imagem") and str(g.get("aviso_imagem") or "").startswith("⚠️ ARTE PROVISÓRIA"):
        rev["problemas"].insert(0, {
            "gravidade": "alta", "legenda": "midia",
            "descricao": "A arte é provisória, com marca d'água do serviço reserva. Não pode ser publicada.",
            "sugestao": "Gere a arte pelo Gemini (API) ou envie uma arte feita na Mesa de arte manual."})
    for ok_p, msg_p in (g.get("padrao_arte") or []):
        if not ok_p:
            sobrep = "sobrep" in msg_p.lower()
            rev["problemas"].insert(0, {
                "gravidade": "alta" if sobrep else "media", "legenda": "📐 padrão visual",
                "descricao": f"Fora do padrão visual: {msg_p}.",
                "sugestao": "Aplique o texto/logo de novo ou ajuste o texto (curto, até 90 caracteres)."})
    # TEXTO ESCRITO NA MÍDIA: ortografia/pontuação conferidas pelo sistema (sem depender da IA)
    gravid = {"alta": "alta", "media": "media", "baixa": "baixa"}
    if eh_carrossel(g):
        for sl in (g.get("slides") or []):
            if sl.get("arte") or sl.get("texto"):
                for grav_t, msg_t in pv.analisar_texto_arte(sl.get("texto") or ""):
                    rev["problemas"].insert(0, {
                        "gravidade": gravid[grav_t], "legenda": f"✍️ Texto do slide {sl['n']}",
                        "descricao": f"{msg_t} (texto na arte: “{sl.get('texto')}”).",
                        "sugestao": "Corrija o texto do slide e clique em “Aplicar textos e logo em todos”."})
        for grav_t, msg_t in pv.analisar_textos_carrossel([sl.get("texto") for sl in (g.get("slides") or [])]):
            rev["problemas"].insert(0, {"gravidade": gravid[grav_t], "legenda": "✍️ Textos do carrossel",
                                        "descricao": msg_t + ".",
                                        "sugestao": "Padronize a pontuação e a numeração entre os slides."})
    elif g.get("texto_midia") and (g.get("imagem") or g.get("video")):
        for grav_t, msg_t in pv.analisar_texto_arte(g.get("texto_midia") or ""):
            rev["problemas"].insert(0, {
                "gravidade": gravid[grav_t], "legenda": "✍️ Texto na arte/vídeo",
                "descricao": f"{msg_t} (texto: “{g['texto_midia']}”).",
                "sugestao": "Corrija o texto e clique em “Aplicar na arte/vídeo”."})
    if eh_carrossel(g):
        prontos = [sl for sl in (g.get("slides") or []) if sl.get("arte")]
        if g.get("slides") and prontos and len(prontos) < pv.MIN_SLIDES:
            rev["problemas"].insert(0, {
                "gravidade": "alta", "legenda": "📐 carrossel",
                "descricao": f"O carrossel tem só {len(prontos)} slide com imagem; o mínimo é {pv.MIN_SLIDES}.",
                "sugestao": "Envie ou gere as imagens dos demais slides."})
        elif g.get("slides") and len(prontos) < len(g["slides"]):
            rev["problemas"].insert(0, {
                "gravidade": "media", "legenda": "📐 carrossel",
                "descricao": f"{len(g['slides']) - len(prontos)} slide(s) ainda sem imagem: só {len(prontos)} serão publicados.",
                "sugestao": "Complete as imagens ou remova os slides vazios na Mesa do carrossel."})
        if len(prontos) > pv.MAX_SLIDES:
            rev["problemas"].insert(0, {"gravidade": "alta", "legenda": "📐 carrossel",
                                        "descricao": f"O Instagram aceita no máximo {pv.MAX_SLIDES} slides.",
                                        "sugestao": "Remova slides."})
        tamanhos = {Image.open(BytesIO(sl["arte"])).size for sl in prontos}
        if len(tamanhos) > 1:
            rev["problemas"].insert(0, {"gravidade": "alta", "legenda": "📐 carrossel",
                                        "descricao": "Os slides têm tamanhos/proporções diferentes (o Instagram recusa).",
                                        "sugestao": "Reenvie as imagens: o sistema recorta todas para a mesma proporção."})
        for sl in prontos:
            for ok_p, msg_p in (sl.get("padrao") or []):
                if not ok_p:
                    rev["problemas"].insert(0, {
                        "gravidade": "alta" if "sobrep" in msg_p.lower() else "media",
                        "legenda": f"📐 Slide {sl['n']}", "descricao": f"Fora do padrão visual: {msg_p}.",
                        "sugestao": "Encurte o texto do slide ou aplique texto/logo de novo."})
    if not g.get("imagem") and not g.get("video") and not any(sl.get("arte") for sl in (g.get("slides") or [])):
        if eh_carrossel(g):
            rev["problemas"] = [pr for pr in rev["problemas"] if pr.get("legenda") != "📐 carrossel"]
            rev["problemas"].insert(0, {
                "gravidade": "media", "legenda": "midia",
                "descricao": "O carrossel ainda não tem imagens: o texto foi revisado, mas os slides não foram conferidos.",
                "sugestao": "Gere os slides pela API ou envie as imagens na Mesa do carrossel; a revisão roda de novo sozinha."})
        else:
            rev["problemas"].insert(0, {
                "gravidade": "media", "legenda": "midia",
                "descricao": "A campanha ainda não tem arte nem vídeo: o texto foi revisado, mas a mídia não foi conferida.",
                "sugestao": "Gere a arte pelo Gemini (API) ou envie na Mesa de arte manual; a revisão roda de novo sozinha."})
        if isinstance(rev.get("nota"), (int, float)):
            rev["nota"] = min(float(rev["nota"]), 7.0)
    return recalcular_veredito(rev)


def revisar_arte(empresa: dict, g: dict):
    """Diretor de arte e vídeo: olha a mídia final e confere composição, texto, logo, cenário, fala e gestos."""
    midias = []
    if g.get("imagem"):
        midias.append(types.Part.from_bytes(data=g["imagem"], mime_type="image/jpeg"))
    tem_video = bool(g.get("video"))
    if tem_video and len(g["video"]) <= MAX_VIDEO_REVISAO:
        midias.append(types.Part.from_bytes(data=g["video"], mime_type=g.get("video_mime") or "video/mp4"))
    elif tem_video:
        tem_video = False
    if not midias:
        return None, ""
    prompt = pv.montar_prompt_diretor(
        empresa, g["canal"], g["formato"], g["tema"], g["publico"], tem_imagem=bool(g.get("imagem")),
        tem_video=tem_video, texto_midia=g.get("texto_midia") or "", layout=g.get("layout_arte"),
        padrao=g.get("padrao_arte"), conceito=((g.get("conteudo") or {}).get("conceito_visual") or ""),
        cores=cores_da_empresa(empresa))
    try:
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2)
        resposta = gerar_texto([prompt] + midias, config)
        texto = (resposta.text or "") if resposta else ""
        arte = interpretar_revisao(texto)
        if arte:
            try:
                bruto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip(), flags=re.I)
                arte["prompt_melhorado"] = str(json.loads(bruto).get("prompt_melhorado", "") or "").strip()
            except Exception:
                arte["prompt_melhorado"] = ""
    except Exception as err:  # noqa: BLE001
        return None, erro_seguro(err)
    return (arte, "") if arte else (None, "O diretor de arte não devolveu um resultado utilizável.")


def revisar_carrossel(empresa: dict, g: dict, legenda: str = ""):
    """Supervisor de marketing do carrossel: vê TODOS os slides na ordem e avalia a história e cada slide."""
    slides = [s for s in (g.get("slides") or []) if s.get("arte")][:pv.MAX_SLIDES]
    if not slides:
        return None, ""
    partes = []
    for s in slides:
        partes.append(f"SLIDE {s['n']}")
        partes.append(types.Part.from_bytes(data=s["arte"], mime_type="image/jpeg"))
    prompt = pv.montar_prompt_carrossel(
        empresa, g["canal"], g["tema"], g["publico"], slides, legenda=legenda,
        conceito=((g.get("conteudo") or {}).get("conceito_visual") or ""), cores=cores_da_empresa(empresa),
        objetivo=g.get("objetivo") or "")
    try:
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2)
        resposta = gerar_texto([prompt] + partes, config)
        texto = (resposta.text or "") if resposta else ""
        arte = interpretar_revisao(texto)
        if arte:
            try:
                dados = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip(), flags=re.I))
            except Exception:
                dados = {}
            arte["prompt_melhorado"] = str(dados.get("prompt_melhorado", "") or "").strip()
            por_slide = []
            for it in dados.get("slides") or []:
                if not isinstance(it, dict):
                    continue
                try:
                    nota_s = max(0.0, min(10.0, float(str(it.get("nota")).replace(",", "."))))
                except (TypeError, ValueError):
                    nota_s = None
                por_slide.append({"n": it.get("n"), "nota": nota_s, "ok": bool(it.get("ok")),
                                  "funcao": str(it.get("funcao", "")).strip(),
                                  "comentario": str(it.get("comentario", "")).strip(),
                                  "correcao": str(it.get("correcao", "")).strip()})
            arte["slides"] = por_slide
    except Exception as err:  # noqa: BLE001
        return None, erro_seguro(err)
    return (arte, "") if arte else (None, "O supervisor do carrossel não devolveu um resultado utilizável.")


def revisar_campanha(empresa: dict, g: dict, briefing: str, legenda_final: str | None = None):
    """Editor de marketing: revisa texto, mídia (imagem/vídeo) e fluxo de WhatsApp. Devolve (revisao|None, erro)."""
    if not g.get("conteudo"):
        return None, "A campanha não veio no formato esperado; não há o que revisar automaticamente."
    conteudo = g["conteudo"]
    if legenda_final is not None:
        conteudo = dict(conteudo, legendas=[{"titulo": "Legenda final", "texto": legenda_final}])
    partes_extra = []
    midias = []
    slides_ok = [s for s in (g.get("slides") or []) if s.get("arte")][:pv.MAX_SLIDES]
    if slides_ok:
        for s in slides_ok:
            midias.append(f"SLIDE {s['n']}")
            midias.append(types.Part.from_bytes(data=s["arte"], mime_type="image/jpeg"))
        partes_extra.append(f"CARROSSEL com {len(slides_ok)} slides anexados, na ordem (arte final, já com texto e logo). "
                            "Textos escritos nos slides: " + " | ".join(
                                f"{s['n']}: {s.get('texto') or '(sem texto)'}" for s in slides_ok))
    elif g.get("imagem"):
        midias.append(types.Part.from_bytes(data=g["imagem"], mime_type="image/jpeg"))
        partes_extra.append("IMAGEM anexada (arte final, já com texto e logo).")
    if g.get("video"):
        if len(g["video"]) <= MAX_VIDEO_REVISAO:
            midias.append(types.Part.from_bytes(data=g["video"], mime_type=g.get("video_mime") or "video/mp4"))
            partes_extra.append("VÍDEO anexado (já com texto e logo, se foram aplicados).")
        else:
            partes_extra.append("Há um vídeo, grande demais para anexar à revisão: avalie só o texto e avise no resumo.")
    if g.get("texto_midia"):
        partes_extra.append(f'Texto que deve aparecer escrito na mídia: "{g["texto_midia"]}".')
    if str(g.get("aviso_imagem") or "").startswith("⚠️ ARTE PROVISÓRIA"):
        partes_extra.append("A imagem é PROVISÓRIA (marca d'água de serviço reserva).")
    com_wa = g.get("objetivo") == OBJ_WHATSAPP
    if com_wa:
        wa = conteudo.get("whatsapp") or {}
        partes_extra.append("OBJETIVO: gerar conversas no WhatsApp. Link: " + (g.get("whatsapp_link") or "(não informado)"))
        if wa:
            partes_extra.append("Fluxo de conversa gerado: mensagem inicial: " + wa.get("mensagem_inicial", "")
                                + " | boas-vindas: " + wa.get("boas_vindas", "")
                                + " | perguntas: " + " / ".join(wa.get("perguntas_qualificacao", []))
                                + " | fechamento: " + wa.get("fechamento", ""))
    extra = ("\n**MÍDIA E OBJETIVO:**\n" + "\n".join(partes_extra) + "\n") if partes_extra else ""
    prompt = montar_prompt_revisao(empresa, briefing, g["canal"], g["formato"], g["tom"], g["tema"],
                                   g["publico"], conteudo, extra=extra, com_midia=bool(midias), com_whatsapp=com_wa)
    def _rodar_texto():
        config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2)
        resposta = gerar_texto([prompt] + midias if midias else prompt, config)
        return interpretar_revisao((resposta.text or "") if resposta else "")

    def _rodar_midia():
        if slides_ok:
            return revisar_carrossel(empresa, g, conteudo["legendas"][0]["texto"] if conteudo.get("legendas") else "")
        return revisar_arte(empresa, g)

    tem_midia_rev = bool(slides_ok or g.get("imagem") or g.get("video"))
    try:
        # o editor de texto e o diretor de arte/supervisor do carrossel trabalham AO MESMO TEMPO (metade do tempo)
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_texto = pool.submit(_rodar_texto)
            f_midia = pool.submit(_rodar_midia) if tem_midia_rev else None
            revisao = f_texto.result()
            arte, erro_arte = f_midia.result() if f_midia else (None, "")
    except Exception as err:  # noqa: BLE001
        return None, erro_seguro(err)
    if not revisao:
        return None, "O revisor não devolveu um resultado utilizável."
    if tem_midia_rev:
        if arte:
            for pr in arte["problemas"]:
                pr["legenda"] = ("🎠 Supervisor do carrossel · " + pr["legenda"]) if slides_ok else "🎨 Diretor de arte"
                revisao["problemas"].append(pr)
            revisao["arte"] = arte
            if isinstance(arte.get("nota"), (int, float)) and isinstance(revisao.get("nota"), (int, float)):
                revisao["nota"] = min(float(revisao["nota"]), float(arte["nota"]))
        elif erro_arte:
            revisao["arte_erro"] = erro_arte
    revisao["padrao_arte"] = g.get("padrao_arte")
    return regras_fixas(revisao, g), ""


def mostrar_revisao(rev: dict) -> None:
    icone = {"aprovado": "✅", "ajustar": "⚠️", "reprovado": "⛔"}[rev["veredito"]]
    nota = f"{rev['nota']:.1f}/10" if rev["nota"] is not None else "sem nota"
    texto = f"{icone} **{rev['veredito'].upper()}** — nota {nota}. {rev['resumo']}"
    {"aprovado": st.success, "ajustar": st.warning, "reprovado": st.error}[rev["veredito"]](texto)
    if rev["problemas"]:
        st.markdown("**Problemas encontrados**")
        marca = {"alta": "🔴", "media": "🟠", "baixa": "🟡"}
        for p in rev["problemas"]:
            linha = f"{marca[p['gravidade']]} **{p['legenda']}** — {p['descricao']}"
            if p["sugestao"]:
                linha += f"  \n&nbsp;&nbsp;&nbsp;↳ *Sugestão:* {p['sugestao']}"
            st.markdown(linha)
    arte = rev.get("arte")
    if arte:
        nota_a = f"{arte['nota']:.1f}/10" if arte.get("nota") is not None else "sem nota"
        titulo_a = "🎠 Supervisor do carrossel" if arte.get("slides") else "🎨 Diretor de arte e vídeo"
        with st.expander(f"{titulo_a} — {arte['veredito']} ({nota_a})", expanded=arte["veredito"] != "aprovado"):
            if arte.get("resumo"):
                st.write(arte["resumo"])
            for sl in arte.get("slides") or []:
                nota_s = f"{sl['nota']:.1f}" if sl.get("nota") is not None else "?"
                linha = (f"{'✅' if sl.get('ok') else '⚠️'} **Slide {sl.get('n', '?')}** "
                         f"({sl.get('funcao') or 'sem função'}) — nota {nota_s}. {sl.get('comentario', '')}")
                if sl.get("correcao"):
                    linha += f"  \n&nbsp;&nbsp;&nbsp;↳ *Corrigir:* {sl['correcao']}"
                st.markdown(linha)
            for c in arte.get("checklist") or []:
                st.markdown(f"{'✅' if c['ok'] else '❌'} **{c['item']}** — {c['comentario']}")
            if arte.get("prompt_melhorado"):
                st.markdown("**Pedido melhorado para gerar de novo (cole no gerador de imagem/vídeo):**")
                st.code(arte["prompt_melhorado"], language=None)
    elif rev.get("arte_erro"):
        st.caption(f"🎨 O diretor de arte não conseguiu revisar agora: {rev['arte_erro']}")
    if rev["checklist"]:
        with st.expander("Checklist do editor"):
            for c in rev["checklist"]:
                st.markdown(f"{'✅' if c['ok'] else '❌'} **{c['item']}** — {c['comentario']}")


MAX_VIDEO_BYTES = 100 * 1024 * 1024
MIME_VIDEO = {"mp4": "video/mp4", "mov": "video/quicktime"}


def pedidos_para_gemini(e: dict, g: dict) -> tuple[str, str]:
    """Monta, em português, os pedidos prontos para colar no Gemini do navegador (sem API)."""
    conceito = ((g.get("conteudo") or {}).get("conceito_visual") or g["tema"]).strip()
    vertical = g["canal"] == "TikTok" or g["formato"] == "Roteiro de Vídeo Curto"
    proporcao = "vertical 9:16" if vertical else "vertical 4:5"
    base = (f"{conceito}. Empresa: {e['name']}. Tema: {g['tema']}. "
            f"Paleta de cores da marca: {cores_da_empresa(e)}.")
    imagem = (f"Crie uma imagem {proporcao} para {g['canal']}, estilo fotografia comercial profissional. {base} "
              f"{pv.bloco_imagem_pt(vertical)}")
    roteiro = ((g.get("conteudo") or {}).get("prompt_video") or "").strip()
    video = (f"Crie um vídeo para {g['canal']}, estilo fotográfico profissional. {base} "
             + (f"Roteiro: {roteiro} " if roteiro else "") + pv.bloco_video_pt())
    return imagem, video


def _id_arquivo(arq) -> str:
    return str(getattr(arq, "file_id", None) or f"{arq.name}:{arq.size}")


def estampar_no_video(g: dict, logo_bytes, posicao: str) -> str:
    """Queima texto corrigido + logo no vídeo original. Devolve '' se deu certo ou a mensagem de erro."""
    base = g.get("video_base")
    if not base:
        return ""
    try:
        info = {}
        g["video"] = midia.estampar_video(base, g.get("texto_midia") or "", logo_bytes, posicao,
                                          tarja=g.get("tarja_whatsapp") or "", info=info)
        g["video_mime"] = "video/mp4"
        if info.get("plano"):
            g["layout_arte"] = midia.resumo_layout(info["plano"])
            g["padrao_arte"] = midia.checar_padrao(info["plano"], com_logo=bool(logo_bytes))
        return ""
    except Exception as err:  # noqa: BLE001
        g["video"] = base
        return erro_seguro(err)


def bloco_texto_midia(e: dict, g: dict, logo_bytes, posicao: str) -> None:
    """Redator: texto que aparece escrito na arte/vídeo, com o português corrigido."""
    chave = f"tm_{g['id']}"
    if g.get("_tm_pendente") is not None:
        st.session_state[chave] = g.pop("_tm_pendente")
    elif chave not in st.session_state:
        st.session_state[chave] = g.get("texto_midia", "")
    st.markdown("**✍️ Texto escrito na arte/vídeo** (o redator corrige o português antes de aplicar)")
    texto = st.text_input("Texto que aparece na arte/vídeo", key=chave, max_chars=90,
                          help="Deixe em branco para não escrever nada. A logo é estampada sempre.")
    if g.get("texto_midia_mudancas"):
        st.caption("Redator corrigiu: " + "; ".join(g["texto_midia_mudancas"]))
    if g.get("texto_midia_erro"):
        st.warning(g["texto_midia_erro"])
    if g.get("wa_formatado"):
        g["usar_tarja"] = st.checkbox(f"🟢 Tarja verde do WhatsApp com o número {g['wa_formatado']}",
                                      value=bool(g.get("tarja_whatsapp")), key=f"tarja_{g['id']}",
                                      help="Aparece na arte/vídeo ao lado da logo, sem sobrepor o texto.")
    c1, c2 = st.columns(2)
    quer_arte = c1.button("🔁 Aplicar na arte", disabled=not g.get("imagem_base"))
    quer_video = c2.button("🎬 Aplicar no vídeo", disabled=not g.get("video_base"))
    if quer_arte or quer_video:
        with st.spinner("✍️ Redator revisando o português..."):
            corrigido, mudancas, erro = corrigir_texto_pt(texto, e)
        g["texto_midia"], g["texto_midia_mudancas"], g["texto_midia_erro"] = corrigido, mudancas, erro
        g["_tm_pendente"] = corrigido
        if g.get("wa_formatado"):
            g["tarja_whatsapp"] = g["wa_formatado"] if g.get("usar_tarja") else ""
        if quer_arte and g.get("imagem_base"):
            _info = {}
            g["imagem"] = compor_arte(Image.open(BytesIO(g["imagem_base"])), corrigido, logo_bytes, posicao,
                                      g.get("tarja_whatsapp") or "", _info)
            guardar_medidas(g, _info)
        if quer_video and g.get("video_base"):
            with st.spinner("🎬 Aplicando texto e logo no vídeo (pode levar alguns segundos)..."):
                falha = estampar_no_video(g, logo_bytes, posicao)
            if falha:
                g["texto_midia_erro"] = f"Não consegui aplicar no vídeo: {falha}"
        st.rerun()


def mesa_manual(e: dict, g: dict, logo_bytes, posicao: str) -> None:
    """Receber arte/vídeo feitos no Gemini do navegador (sem API), estampar texto e logo e anexar ao post."""
    with st.expander("🖐️ Mesa de arte manual — Gemini no navegador (sem API)"):
        st.caption("Use quando a API de imagem estiver indisponível. 1) Copie o pedido abaixo; "
                   "2) cole no Gemini (gemini.google.com) em 'Criar imagem' ou 'Criar vídeo'; "
                   "3) baixe o resultado e envie aqui. O texto corrigido e a logo são estampados automaticamente.")
        p_img, p_vid = pedidos_para_gemini(e, g)
        st.markdown("**Pedido para imagem**")
        st.code(p_img, language=None)
        st.markdown("**Pedido para vídeo**")
        st.code(p_vid, language=None)

        arq_img = st.file_uploader("Enviar a imagem feita no Gemini", type=["png", "jpg", "jpeg", "webp"],
                                   key=f"up_img_{g['id']}")
        if arq_img is not None and g.get("up_img_id") != _id_arquivo(arq_img):
            try:
                bruta = Image.open(BytesIO(arq_img.getvalue()))
                bruta.load()
                g["imagem_base"] = para_jpeg(bruta)
                _info = {}
                g["imagem"] = compor_arte(bruta, g.get("texto_midia") or "", logo_bytes, posicao,
                                          g.get("tarja_whatsapp") or "", _info)
                guardar_medidas(g, _info)
                g["aviso_imagem"] = "✅ Arte enviada manualmente (Gemini no navegador), com texto e logo estampados."
                g["up_img_id"] = _id_arquivo(arq_img)
                db.registrar_evento(e["id"], "media.manual_image", f"Arte manual enviada: {arq_img.name[:80]}")
            except Exception as err:  # noqa: BLE001
                st.error(f"Não consegui abrir essa imagem: {erro_seguro(err)}")

        arq_vid = st.file_uploader("Enviar o vídeo feito no Gemini (MP4 ou MOV, até 100 MB)",
                                   type=["mp4", "mov"], key=f"up_vid_{g['id']}")
        if arq_vid is not None and g.get("up_vid_id") != _id_arquivo(arq_vid):
            dados = arq_vid.getvalue()
            if len(dados) > MAX_VIDEO_BYTES:
                st.error("O vídeo passa de 100 MB. Gere um menor.")
            else:
                ext = arq_vid.name.rsplit(".", 1)[-1].lower()
                g["video_base"], g["video"] = dados, dados
                g["video_mime"] = MIME_VIDEO.get(ext, "video/mp4")
                g["up_vid_id"] = _id_arquivo(arq_vid)
                with st.spinner("🎬 Aplicando texto e logo no vídeo..."):
                    falha = estampar_no_video(g, logo_bytes, posicao)
                if falha:
                    st.warning(f"Não consegui estampar o vídeo ({falha}). Mantive o original, sem logo.")
                db.registrar_evento(e["id"], "media.manual_video", f"Vídeo manual enviado: {arq_vid.name[:80]}")
        if g.get("video"):
            st.video(g["video"])


def mesa_carrossel(e: dict, g: dict, logo_bytes, posicao: str) -> None:
    """Mesa do carrossel: texto, imagem e conferência de cada slide; imagens pela API ou enviadas à mão."""
    slides = g.get("slides") or []
    if not slides:
        st.warning("Este carrossel não tem slides. Gere a campanha de novo.")
        return
    total = len(slides)
    st.markdown("### 🎠 Mesa do carrossel")
    st.caption(f"{total} slides (de {pv.MIN_SLIDES} a {pv.MAX_SLIDES}; 5 a 8 funciona melhor). Todos recebem a logo no mesmo "
               "lugar; a tarja verde do WhatsApp só aparece no último slide. O sistema recorta todas as imagens para a "
               "mesma proporção, como o Instagram exige.")
    pend = g.pop("_sl_pend", None)
    for s in slides:
        k = f"sl_txt_{g['id']}_{s['n']}"
        if pend is not None:
            st.session_state[k] = pend.get(s["n"], s.get("texto", ""))
        elif k not in st.session_state:
            st.session_state[k] = s.get("texto", "")
    if g.get("aviso_slides"):
        st.warning(g.pop("aviso_slides"))
    if g.get("slides_mudancas"):
        st.caption("Redator corrigiu: " + " · ".join(g["slides_mudancas"]))
    for s in slides:
        c1, c2 = st.columns([1, 4])
        with c1:
            if s.get("arte"):
                st.image(s["arte"], width=150)
            else:
                st.info("sem imagem")
        with c2:
            s["texto"] = st.text_input(
                f"Slide {s['n']} — {pv.PAPEIS_SLIDE.get(s['papel'], s['papel'])}: texto na arte",
                key=f"sl_txt_{g['id']}_{s['n']}", max_chars=90)
            if s.get("padrao"):
                falhas = [m for ok, m in s["padrao"] if not ok]
                st.caption("📐 " + ("padrão visual ✅" if not falhas else "❌ " + "; ".join(falhas)))
            with st.expander(f"Pedido para o Gemini do navegador — slide {s['n']}"):
                st.code(pv.bloco_slide_pt(s["papel"], s["n"], total, s.get("texto") or "", s.get("cena") or ""), language=None)

    if g.get("wa_formatado"):
        g["usar_tarja"] = st.checkbox(f"🟢 Tarja verde do WhatsApp ({g['wa_formatado']}) no último slide",
                                      value=bool(g.get("tarja_whatsapp")), key=f"tarja_{g['id']}")
    b1, b2, b3, b4 = st.columns(4)
    if b1.button("🎨 Gerar slides com IA (API)", help="Gera só os slides que ainda estão sem imagem."):
        falta = [sl for sl in slides if not sl.get("base")]
        feitos, aviso = [], ""
        for sl in falta:
            with st.spinner(f"🎨 Gerando o slide {sl['n']} de {total}..."):
                try:
                    pr = (sl.get("prompt_imagem") or f"Commercial photo about {g['tema']}") + \
                        f". Brand color palette: {cores_da_empresa(e)}. Same visual style for every slide of the carousel. " + \
                        pv.bloco_imagem_en(False)
                    feitos.append((sl, _imagem_pelo_gemini(pr, False)))
                except Exception as err:  # noqa: BLE001
                    aviso = (f"Parei no slide {sl['n']}: a API de imagem não respondeu ({erro_seguro(err)}). "
                             "Use o envio manual abaixo (Gemini no navegador).")
                    break
        if feitos:
            padronizadas = normalizar_proporcao([im for _, im in feitos])
            for (sl, _), im in zip(feitos, padronizadas):
                sl["base"] = para_jpeg(im)
            recompor_slides(g, logo_bytes, posicao)
            db.registrar_evento(e["id"], "media.carousel_generated", f"{len(feitos)} slide(s) gerado(s) pela API")
        if aviso:
            g["aviso_slides"] = aviso
        st.rerun()
    if b2.button("🔁 Aplicar textos e logo em todos"):
        with st.spinner("✍️ Redator revisando o português dos slides..."):
            novos, muds, erro = corrigir_textos_pt([sl.get("texto", "") for sl in slides], e)
        for sl, t in zip(slides, novos):
            sl["texto"] = t
        g["_sl_pend"] = {sl["n"]: sl["texto"] for sl in slides}
        g["slides_mudancas"] = [f"slide {i + 1}: " + "; ".join(m) for i, m in enumerate(muds) if m]
        if erro:
            g["aviso_slides"] = erro
        if g.get("wa_formatado"):
            g["tarja_whatsapp"] = g["wa_formatado"] if g.get("usar_tarja", True) else ""
        recompor_slides(g, logo_bytes, posicao)
        st.rerun()
    if b3.button("➕ Adicionar slide", disabled=total >= pv.MAX_SLIDES):
        slides.insert(total - 1, novo_slide(total, "conteudo"))      # o último continua sendo a chamada para ação
        for i, sl in enumerate(slides):
            sl["n"] = i + 1
        g["_sl_pend"] = {sl["n"]: sl.get("texto", "") for sl in slides}
        st.rerun()
    if b4.button("➖ Remover o penúltimo", disabled=total <= pv.MIN_SLIDES):
        slides.pop(total - 2)
        for i, sl in enumerate(slides):
            sl["n"] = i + 1
        g["_sl_pend"] = {sl["n"]: sl.get("texto", "") for sl in slides}
        recompor_slides(g, logo_bytes, posicao)
        st.rerun()

    arqs = st.file_uploader("📤 Enviar as imagens do carrossel (todas de uma vez, na ordem; nomeie 1, 2, 3...)",
                            type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True, key=f"up_slides_{g['id']}")
    ids = tuple(_id_arquivo(a) for a in (arqs or []))
    if arqs and g.get("up_slides_ids") != ids:
        ordenadas = sorted(arqs, key=lambda a: _chave_natural(a.name))[:pv.MAX_SLIDES]
        imagens, erros = [], []
        for a in ordenadas:
            try:
                im = Image.open(BytesIO(a.getvalue()))
                im.load()
                imagens.append(im)
            except Exception as err:  # noqa: BLE001
                erros.append(f"{a.name[:40]}: {erro_seguro(err)}")
        if erros:
            st.error("Não consegui abrir: " + "; ".join(erros))
        elif len(imagens) < pv.MIN_SLIDES:
            st.error(f"Envie pelo menos {pv.MIN_SLIDES} imagens para formar o carrossel.")
        else:
            while len(slides) < len(imagens):
                slides.insert(len(slides) - 1, novo_slide(len(slides), "conteudo"))
            for i, sl in enumerate(slides):
                sl["n"] = i + 1
            for sl, im in zip(slides, normalizar_proporcao(imagens)):
                sl["base"] = para_jpeg(im)
            g["up_slides_ids"] = ids
            g["_sl_pend"] = {sl["n"]: sl.get("texto", "") for sl in slides}
            recompor_slides(g, logo_bytes, posicao)
            g["aviso_imagem"] = f"✅ {len(imagens)} imagens enviadas manualmente, com texto e logo estampados."
            db.registrar_evento(e["id"], "media.manual_carousel", f"Carrossel manual com {len(imagens)} imagens")
            st.rerun()


def pagina_campanha() -> None:
    e = empresa
    logo_bytes = baixar_logo(e.get("logo_url") or "")

    c_logo, c_titulo = st.columns([1, 4])
    with c_logo:
        if logo_bytes:
            st.image(logo_bytes, width=140)
        else:
            st.info("⚠️ Sem logo cadastrada")
    with c_titulo:
        st.title(f"Campanha — {e['name']}")
        st.caption(f"Setor: **{cfg(e, 'nicho', 'Geral')}** | Cores: **{cores_da_empresa(e)}**")
    bloco_logo(e, "campanha")
    if e["status"] != "active":
        st.warning(f"Esta empresa está como '{STATUS_EMPRESA.get(e['status'], e['status'])}': "
                   "agendamentos não serão publicados enquanto não estiver ativa.")

    with st.expander("📝 Briefing usado pela IA (alterar aqui vale só para esta campanha)"):
        briefing = st.text_area("Briefing", value=cfg(e, "briefing"), height=100, key=f"briefing_{e['id']}")

    p1, p2, p3, p4 = st.columns(4)
    canal = p1.selectbox("Canal", CANAIS)
    formato = p2.selectbox("Formato", FORMATOS)
    tom_campanha = p3.selectbox("Tom de voz", TONS)
    posicao = POSICOES[p4.selectbox("Posição da logo", list(POSICOES.keys()))]
    gerar_arte = st.checkbox("🎨 Gerar e estampar arte com IA", value=True)

    t1, t2 = st.columns(2)
    tema = t1.text_input("Tema central da campanha *", placeholder="Ex.: Lançamento do 5G, Consignado CLT")
    publico = t2.text_input("Público-alvo específico (opcional)", placeholder="Ex.: Trabalhadores CLT")

    o1, o2 = st.columns(2)
    objetivo = o1.selectbox("Objetivo da campanha", OBJETIVOS)
    texto_midia_cru = o2.text_input("Texto que deve aparecer na arte/vídeo (opcional)", max_chars=90,
                                    placeholder="Ex.: Consignado CLT: simule sem compromisso",
                                    help="O redator corrige o português antes de escrever na imagem/vídeo.")
    numero_wa, tarja_inicial = "", False
    if objetivo == OBJ_WHATSAPP:
        numero_wa = st.text_input("WhatsApp da empresa (com DDD)", placeholder="Ex.: 21 99999-9999",
                                  value=cfg(e, "whatsapp"), key=f"wa_{e['id']}",
                                  help="Fica salvo em Empresas > Editar > WhatsApp Business.")
        tarja_inicial = st.checkbox("🟢 Colocar na arte/vídeo uma tarja verde com o WhatsApp", value=True,
                                    help="Mostra o número em destaque, ao lado da logo e sem cobrir o texto.")

    docs = st.file_uploader("📎 Documentos de apoio (PDF, Word, TXT, CSV) — um ou vários",
                            type=["pdf", "docx", "txt", "csv", "md"], accept_multiple_files=True)
    texto_docs = ""
    if docs:
        partes = []
        for d in docs:
            texto, erro = extrair_texto_documento(d.getvalue(), d.name)
            if erro:
                st.error(erro)
            if texto:
                partes.append(f"--- DOCUMENTO: {d.name} ---\n{texto}\n")
        texto_docs = "\n".join(partes)
        if len(texto_docs) > MAX_CHARS_DOCUMENTOS:
            texto_docs = texto_docs[:MAX_CHARS_DOCUMENTOS]
            st.warning(f"Os documentos são longos: usei só os primeiros {MAX_CHARS_DOCUMENTOS:,} caracteres.".replace(",", "."))
        if texto_docs:
            st.success(f"📄 {len(docs)} documento(s) lido(s): {len(texto_docs.split())} palavras.")

    if st.button(f"⚡ Gerar campanha para {e['name']}", type="primary"):
        if not tema.strip():
            st.warning("Informe o tema da campanha.")
        elif objetivo == OBJ_WHATSAPP and not link_whatsapp(numero_wa, ""):
            st.warning("Informe o WhatsApp da empresa com DDD (ex.: 21 99999-9999) para a campanha de conversas.")
        else:
            prompt = montar_prompt(e, briefing, texto_docs, canal, formato, tom_campanha, tema.strip(),
                                   publico.strip(), objetivo)
            try:
                with st.spinner("🧠 Gerando estratégia com Gemini..."):
                    config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.9)
                    resposta = gerar_texto(prompt, config)
                texto_bruto = (resposta.text or "") if resposta else ""
            except Exception as err:  # noqa: BLE001
                st.error(f"Falha ao gerar o texto: {erro_seguro(err)}")
                db.registrar_evento(e["id"], "ai.text_failed", erro_seguro(err), nivel="error")
                texto_bruto = ""
            if texto_bruto:
                conteudo = interpretar_conteudo(texto_bruto)
                texto_midia, mud_midia, erro_midia = "", [], ""
                if texto_midia_cru.strip():
                    with st.spinner("✍️ Redator revisando o português do texto da arte..."):
                        texto_midia, mud_midia, erro_midia = corrigir_texto_pt(texto_midia_cru, e)
                link_wa = ""
                if objetivo == OBJ_WHATSAPP:
                    msg_wa = ((conteudo or {}).get("whatsapp") or {}).get("mensagem_inicial") or \
                        f"Olá! Vi o conteúdo sobre {tema.strip()} e quero saber mais."
                    link_wa = link_whatsapp(numero_wa, msg_wa) or ""
                imagem_bytes, imagem_base, aviso_img, prompt_img = None, None, "", ""
                slides_novos = []
                if formato == FORMATO_CARROSSEL:
                    slides_novos = slides_do_conteudo(conteudo, tema.strip())
                    if any(sl["texto"] for sl in slides_novos):
                        with st.spinner("✍️ Redator revisando o português dos slides..."):
                            _tx, _md, _er = corrigir_textos_pt([sl["texto"] for sl in slides_novos], e)
                        for sl, t in zip(slides_novos, _tx):
                            sl["texto"] = t
                wa_fmt = pv.formatar_telefone(numero_wa) if (objetivo == OBJ_WHATSAPP and link_wa) else ""
                tarja_wa_txt = wa_fmt if (wa_fmt and tarja_inicial) else ""
                info_arte = {}
                if gerar_arte and formato != FORMATO_CARROSSEL:
                    prompt_img = ((conteudo or {}).get("prompt_imagem")
                                  or f"High-end commercial advertisement for {e['name']}, theme: {tema}")
                    prompt_img += f". Brand color palette: {cores_da_empresa(e)}. Professional marketing photography, clean composition." + pv.bloco_imagem_en(vertical=(canal == "TikTok"))
                    with st.spinner("🎨 Renderizando a arte e estampando a marca..."):
                        bruta, aviso_img = gerar_imagem(prompt_img, vertical=(canal == "TikTok"))
                    if bruta is not None:
                        imagem_base = para_jpeg(bruta)
                        imagem_bytes = compor_arte(bruta, texto_midia, logo_bytes, posicao, tarja_wa_txt, info_arte)
                st.session_state["geracao"] = {
                    "id": uuid.uuid4().hex, "empresa_id": e["id"], "tema": tema.strip(), "publico": publico.strip(),
                    "canal": canal, "formato": formato, "tom": tom_campanha, "conteudo": conteudo,
                    "texto_bruto": texto_bruto, "imagem": imagem_bytes, "aviso_imagem": aviso_img,
                    "prompt_imagem": prompt_img, "post_id": None, "video": None, "video_mime": "", "up_img_id": None,
                    "revisao": None, "erro_revisao": "",
                    "objetivo": objetivo, "whatsapp_link": link_wa, "texto_midia": texto_midia,
                    "texto_midia_mudancas": mud_midia, "texto_midia_erro": erro_midia,
                    "imagem_base": imagem_base, "video_base": None, "revisao_chave": None,
                    "wa_formatado": wa_fmt, "tarja_whatsapp": tarja_wa_txt, "usar_tarja": bool(tarja_wa_txt),
                    "layout_arte": info_arte.get("layout_arte"), "padrao_arte": info_arte.get("padrao_arte"),
                    "avisos_arte": info_arte.get("avisos_arte", []),
                    "revisao_tentada": None, "revisao_final_chave": None,
                    "slides": slides_novos, "up_slides_ids": None,
                }
                if slides_novos and gerar_arte:
                    _g = st.session_state["geracao"]
                    for sl in slides_novos:
                        with st.spinner(f"🎨 Gerando o slide {sl['n']} de {len(slides_novos)}..."):
                            try:
                                _pr = (sl["prompt_imagem"] or f"Commercial photo about {tema.strip()}") + \
                                    f". Brand color palette: {cores_da_empresa(e)}. Same visual style for every slide of the carousel. " + \
                                    pv.bloco_imagem_en(False)
                                sl["_img"] = _imagem_pelo_gemini(_pr, False)
                            except Exception as err:  # noqa: BLE001
                                _g["aviso_slides"] = (f"A API de imagem não gerou o slide {sl['n']} ({erro_seguro(err)}). "
                                                      "Envie as imagens na Mesa do carrossel (Gemini no navegador).")
                                break
                    _feitos = [sl for sl in slides_novos if sl.get("_img") is not None]
                    for sl, im in zip(_feitos, normalizar_proporcao([sl["_img"] for sl in _feitos])):
                        sl["base"] = para_jpeg(im)
                    for sl in slides_novos:
                        sl.pop("_img", None)
                    if _feitos:
                        recompor_slides(_g, logo_bytes, posicao)
                db.registrar_evento(e["id"], "ai.content_generated", f"Campanha gerada: {tema.strip()[:100]}")
                if conteudo:
                    with st.spinner("🧐 Editor de marketing revisando a campanha..."):
                        rev, erro_rev = revisar_campanha(e, st.session_state["geracao"], briefing)
                    st.session_state["geracao"]["revisao"] = rev
                    st.session_state["geracao"]["erro_revisao"] = erro_rev
                    st.session_state["geracao"]["revisao_tentada"] = chave_revisao(st.session_state["geracao"])
                    if rev:
                        st.session_state["geracao"]["revisao_chave"] = chave_revisao(st.session_state["geracao"])
                    if rev:
                        db.registrar_evento(e["id"], "ai.review_done",
                                            f"Revisão: {rev['veredito']} (nota {rev['nota']})")

    g = st.session_state.get("geracao")
    if not g:
        return
    if g["empresa_id"] != e["id"]:
        st.info("Há uma campanha gerada para outra empresa. Volte a ela na barra lateral ou gere uma nova.")
        return

    st.markdown("---")
    st.subheader("📋 Resultado")
    if eh_carrossel(g):
        mesa_carrossel(e, g, logo_bytes, posicao)
    else:
        bloco_texto_midia(e, g, logo_bytes, posicao)
        mesa_manual(e, g, logo_bytes, posicao)
    col_txt, col_img = st.columns([3, 2])
    conteudo = g["conteudo"]
    with col_txt:
        if conteudo:
            if conteudo["conceito_visual"]:
                st.markdown(f"**Conceito visual:** {conteudo['conceito_visual']}")
            for leg in conteudo["legendas"]:
                st.markdown(f"**{leg['titulo']}**\n\n{leg['texto']}")
            if conteudo["hashtags"]:
                st.markdown(" ".join(conteudo["hashtags"]))
            if g.get("objetivo") == OBJ_WHATSAPP:
                st.markdown("**💬 Clique para conversar no WhatsApp**")
                st.code(g.get("whatsapp_link") or "link indisponível", language=None)
                wa = conteudo.get("whatsapp")
                if wa:
                    st.markdown(f"**Mensagem inicial do cliente:** {wa['mensagem_inicial']}")
                    st.markdown(f"**Boas-vindas do atendimento:** {wa['boas_vindas']}")
                    if wa["perguntas_qualificacao"]:
                        st.markdown("**Perguntas para entender o cliente:**\n\n" +
                                    "\n".join(f"{i}. {q}" for i, q in enumerate(wa["perguntas_qualificacao"], 1)))
                    st.markdown(f"**Fechamento:** {wa['fechamento']}")
        else:
            st.warning("A IA não devolveu o formato esperado. O texto bruto está abaixo; edite e salve como quiser.")
            st.markdown(g["texto_bruto"])
    with col_img:
        if g["imagem"] and not eh_carrossel(g):
            st.image(g["imagem"], caption="Arte final com a marca")
        if g["aviso_imagem"]:
            st.caption(g["aviso_imagem"])
        if g.get("padrao_arte"):
            with st.expander("📐 Padrão visual da arte (conferência automática)"):
                for ok_p, msg_p in g["padrao_arte"]:
                    st.markdown(f"{'✅' if ok_p else '❌'} {msg_p}")
                for av in g.get("avisos_arte") or []:
                    st.caption(f"ℹ️ {av}")

    st.markdown("---")
    st.subheader("🧐 Revisão do editor de marketing")
    _chave = chave_revisao(g)
    if g.get("conteudo") and g.get("revisao_chave") != _chave and g.get("revisao_tentada") != _chave:
        g["revisao_tentada"] = _chave
        with st.spinner("🧐 O conteúdo mudou: o editor está revisando de novo..."):
            _rev, _erro = revisar_campanha(e, g, briefing)
        g["revisao"], g["erro_revisao"] = _rev, _erro
        g["revisao_chave"] = _chave if _rev else None
        if _rev:
            db.registrar_evento(e["id"], "ai.review_done", f"Revisão: {_rev['veredito']} (nota {_rev['nota']})")
    if g.get("revisao"):
        mostrar_revisao(g["revisao"])
        corr = g["revisao"].get("legendas_corrigidas") or []
        if corr and g["conteudo"] and g["revisao"]["veredito"] != "aprovado":
            if st.button("✍️ Usar as legendas corrigidas pelo editor"):
                g["conteudo"]["legendas"] = corr
                g["revisao"] = None
                g["erro_revisao"] = "Legendas substituídas. Clique em 'Revisar novamente' para conferir."
                st.rerun()
    elif g.get("erro_revisao"):
        st.info(g["erro_revisao"])
    if g.get("conteudo") and st.button("🔄 Revisar novamente"):
        with st.spinner("🧐 Editor de marketing revisando..."):
            rev, erro_rev = revisar_campanha(e, g, briefing)
        g["revisao"], g["erro_revisao"] = rev, erro_rev
        g["revisao_tentada"] = _chave
        g["revisao_chave"] = _chave if rev else None
        st.rerun()

    st.markdown("---")
    st.subheader("💾 Salvar e agendar")
    formato_db = FORMATO_DB[g["formato"]]
    if g.get("video") and formato_db != "video":
        como = st.radio("Publicar como", ["Imagem (arte)", "Vídeo"] if g["imagem"] else ["Vídeo"],
                        horizontal=True, key=f"como_{g['id']}")
        if como == "Vídeo":
            formato_db = "video"
    slides_prontos = [sl for sl in (g.get("slides") or []) if sl.get("arte")] if (
        formato_db == "carousel" and eh_carrossel(g)) else []
    anexar_slides = bool(slides_prontos)
    anexar_arte = bool(g["imagem"]) and formato_db in ("image", "carousel") and not anexar_slides
    anexar_video = bool(g.get("video")) and formato_db == "video"
    if anexar_slides:
        st.caption(f"🎠 {len(slides_prontos)} slides serão publicados em ordem como carrossel.")
    if g.get("video") and not anexar_video:
        st.caption("O vídeo enviado não será anexado a este post; para publicá-lo, escolha 'Publicar como: Vídeo'.")
    if g["imagem"] and not anexar_arte and not anexar_slides:
        st.caption("A arte não será anexada a este formato (vídeo/texto). Baixe-a abaixo se quiser usar como capa.")
        st.download_button("⬇️ Baixar arte", g["imagem"], file_name="arte.jpg", mime="image/jpeg")

    opcoes_legenda = [l["titulo"] for l in conteudo["legendas"]] if conteudo else ["Texto gerado"]
    escolha = st.radio("Legenda para publicar", opcoes_legenda, horizontal=True, key=f"leg_{g['id']}")
    if conteudo:
        base = next(l["texto"] for l in conteudo["legendas"] if l["titulo"] == escolha)
        if conteudo["hashtags"]:
            base += "\n\n" + " ".join(conteudo["hashtags"])
    else:
        base = g["texto_bruto"]
    if g.get("whatsapp_link") and g.get("objetivo") == OBJ_WHATSAPP:
        if st.checkbox("Incluir o link do WhatsApp na legenda", value=True, key=f"inclwa_{g['id']}"):
            base += f"\n\n💬 Chame no WhatsApp: {g['whatsapp_link']}"
    base = pv.formatar_legenda(base)   # parágrafos curtos, emojis e ordem (texto → ressalva → WhatsApp → hashtags)
    st.caption("Facebook: o link do WhatsApp fica clicável. Instagram: legenda não aceita link clicável (limite da rede); "
               "o sistema troca por 'Toque no link da bio' e o número escrito.")
    legenda = st.text_area("Legenda final (editável)", value=base, height=300, key=f"txt_{g['id']}_{escolha}")

    contas = db.listar_contas(e["id"])
    rotulo = {c["id"]: f"{c['platform'].title()} — {c['label']}" for c in contas}
    plataforma_de = {c["id"]: c["platform"] for c in contas}
    if contas:
        selecionadas = st.multiselect("Publicar em", list(rotulo.keys()), format_func=lambda i: rotulo[i],
                                      key=f"contas_{g['id']}")
    else:
        selecionadas = []
        st.info("Nenhuma conta de rede social conectada para esta empresa ainda. O post será salvo sem destinos "
                "(rascunho) e poderá ser agendado depois que as contas forem conectadas.")

    modo = st.radio("O que fazer com este post?",
                    ["Salvar como rascunho", "Enviar para aprovação do cliente", "Aprovar e agendar"],
                    key=f"modo_{g['id']}")
    aprovar_mesmo_assim = False
    if g.get("msg_salvar"):
        st.error(g.pop("msg_salvar"))
    if modo != "Salvar como rascunho" and (g.get("revisao") or {}).get("veredito") == "reprovado":
        aprovar_mesmo_assim = st.checkbox("⛔ O editor reprovou. Aprovar mesmo assim (por minha conta)",
                                          key=f"forcar_{g['id']}")
    tz = fuso(e)
    padrao = datetime.now(tz) + timedelta(minutes=15)
    d1, d2 = st.columns(2)
    data = d1.date_input("Data da publicação", value=padrao.date(), key=f"data_{g['id']}")
    hora = d2.time_input("Hora (horário da empresa)", value=padrao.time().replace(second=0, microsecond=0),
                         key=f"hora_{g['id']}")

    if g["post_id"]:
        st.success(f"✅ Post salvo (código {g['post_id'][:8]}). Acompanhe na 'Fila de posts'. "
                   "Gere uma nova campanha para criar outro.")
        return

    if st.button("💾 Salvar post", type="primary"):
        status = {"Salvar como rascunho": "draft", "Enviar para aprovação do cliente": "pending_approval",
                  "Aprovar e agendar": "scheduled"}[modo]
        quando = datetime.combine(data, hora, tzinfo=tz)
        if status in ("pending_approval", "scheduled"):
            _kf = chave_revisao(g, legenda)
            if g.get("revisao_final_chave") != _kf:
                with st.spinner("🧐 O editor está conferindo a versão final antes de salvar..."):
                    _rf, _ef = revisar_campanha(e, g, briefing, legenda_final=legenda)
                if not _rf:
                    st.error(f"Não consegui revisar a versão final agora ({_ef}). Salve como rascunho ou tente de novo.")
                    return
                g["revisao"], g["revisao_final_chave"] = _rf, _kf
                db.registrar_evento(e["id"], "ai.review_final", f"Revisão final: {_rf['veredito']} (nota {_rf['nota']})")
                if _rf["veredito"] == "reprovado":
                    g["msg_salvar"] = ("O editor reprovou a versão final. Corrija o que ele apontou ou marque "
                                       "'Aprovar mesmo assim' e salve de novo.")
                    st.rerun()
        rev_atual = g.get("revisao")
        if status != "draft" and rev_atual and rev_atual["veredito"] == "reprovado" and not aprovar_mesmo_assim:
            st.error("O editor reprovou esta campanha. Corrija o que ele apontou ou marque 'Aprovar mesmo assim'.")
            return
        if status == "scheduled":
            problemas = validar_agendamento(
                formato_db,
                ["image"] * len(slides_prontos) if anexar_slides else (
                    ["image"] if anexar_arte else (["video"] if anexar_video else [])),
                [plataforma_de[i] for i in selecionadas])
            if problemas:
                st.error("Não dá para agendar ainda:\n\n- " + "\n- ".join(problemas) +
                         "\n\nVocê pode salvar como rascunho e agendar depois.")
                return
            if quando < datetime.now(tz):
                st.error("A data/hora escolhida já passou.")
                return
        try:
            asset_ids = []
            if anexar_slides:
                for sl in slides_prontos:
                    larg, alt = Image.open(BytesIO(sl["arte"])).size
                    asset = db.enviar_midia(e["id"], sl["arte"], "image/jpeg", "image", larg, alt,
                                            origem="upload" if g.get("up_slides_ids") else "ai_generated",
                                            alt=f"{g['tema'][:170]} — slide {sl['n']}")
                    asset_ids.append(asset["id"])
            if anexar_arte:
                larg, alt = Image.open(BytesIO(g["imagem"])).size
                asset = db.enviar_midia(e["id"], g["imagem"], "image/jpeg", "image", larg, alt,
                                        origem="upload" if g.get("up_img_id") else "ai_generated",
                                        alt=g["tema"][:200])
                asset_ids.append(asset["id"])
            if anexar_video:
                asset_v = db.enviar_midia(e["id"], g["video"], g.get("video_mime") or "video/mp4", "video",
                                          origem="upload", alt=g["tema"][:200])
                asset_ids.append(asset_v["id"])
            contexto = {"tema": g["tema"], "publico": g["publico"], "canal": g["canal"], "formato_ui": g["formato"],
                        "tom_de_voz": g["tom"], "modelo_texto": MODELO_TEXTO, "modelo_imagem": MODELO_IMAGEM,
                        "prompt_imagem": g["prompt_imagem"], "legenda_escolhida": escolha,
                        "midia_manual": bool(g.get("up_img_id") or g.get("up_vid_id")),
                        "objetivo": g.get("objetivo"), "whatsapp_link": g.get("whatsapp_link") or None,
                        "fluxo_whatsapp": (g.get("conteudo") or {}).get("whatsapp"),
                        "texto_midia": g.get("texto_midia") or None,
                        "tarja_whatsapp": g.get("tarja_whatsapp") or None,
                        "layout_arte": g.get("layout_arte"),
                        "slides": ([{"n": sl["n"], "papel": sl["papel"], "texto": sl.get("texto"),
                                     "medidas": sl.get("medidas")} for sl in slides_prontos] or None),
                        "revisao_editor": ({"veredito": g["revisao"]["veredito"], "nota": g["revisao"]["nota"],
                                            "problemas": g["revisao"]["problemas"][:12]}
                                           if g.get("revisao") else None)}
            post = db.criar_post(e["id"], g["tema"], formato_db, legenda, quando, status, contexto,
                                 asset_ids, selecionadas)
            g["post_id"] = post["id"]
            st.rerun()
        except Exception as err:  # noqa: BLE001
            st.error(f"Não foi possível salvar o post: {erro_seguro(err)}")
            db.registrar_evento(e["id"], "post.save_failed", erro_seguro(err), nivel="error")

    # ---- modo antigo: disparo imediato pelo webhook do n8n -------------------
    webhook = cfg(e, "n8n_webhook")
    if webhook and segredo("N8N_TOKEN"):
        with st.expander("🔌 Modo antigo: disparo imediato pelo webhook do n8n"):
            st.caption("Mantido para compatibilidade com o fluxo validado no MVP. O caminho novo é salvar/agendar acima.")
            if not webhook.startswith("https://"):
                st.error("A URL do webhook precisa começar com https://")
            else:
                cabecalho = {"X-API-TOKEN": segredo("N8N_TOKEN")}
                b1, b2 = st.columns(2)
                if b1.button("🧪 Testar conexão"):
                    try:
                        r = requests.post(webhook, json={"evento": "teste_conexao", "cliente": e["name"],
                                                         "timestamp": datetime.now().isoformat()},
                                          headers=cabecalho, timeout=8)
                        st.success(f"Conexão ok (HTTP {r.status_code}).") if r.status_code in (200, 201) \
                            else st.error(f"Erro HTTP {r.status_code}")
                    except Exception as err:  # noqa: BLE001
                        st.error(f"Falha ao conectar: {erro_seguro(err)}")
                if b2.button("📲 Disparar agora (n8n)"):
                    imagem_b64 = ""
                    if g["imagem"]:
                        imagem_b64 = "data:image/jpeg;base64," + base64.b64encode(g["imagem"]).decode()
                    try:
                        # Sem retentativa automática de propósito: repetir poderia publicar em duplicidade.
                        r = requests.post(webhook, headers=cabecalho, timeout=15, json={
                            "cliente": e["name"], "canal": g["canal"], "formato": g["formato"], "tema": g["tema"],
                            "variacao_selecionada": escolha, "conteudo_completo": g["texto_bruto"],
                            "legenda_final": legenda, "imagem_base64": imagem_b64,
                            "data_envio": datetime.now().isoformat()})
                        if r.status_code in (200, 201):
                            db.registrar_evento(e["id"], "n8n.dispatched", f"Disparo imediato: {g['tema'][:100]}")
                            st.success("Campanha enviada ao n8n.")
                        else:
                            st.error(f"Erro HTTP {r.status_code}")
                    except Exception as err:  # noqa: BLE001
                        st.error(f"Falha na conexão com o webhook: {erro_seguro(err)}")


# ==========================================================================
# Página: Fila de posts
# ==========================================================================
def mostrar_resultado_publicacao() -> None:
    res = st.session_state.pop("res_publicacao", None)
    if not res:
        return
    for r in res:
        if r["ok"] and r["platform"] == "agenda":
            st.success(f"📅 {r['mensagem']}")
        elif r["ok"]:
            st.success(f"✅ {r['platform'].title()} publicado: {r['mensagem']}")
        else:
            st.error(f"❌ {r['platform'].title()}: {r['mensagem']}")


def publicar_agora(e: dict, p: dict) -> None:
    meta = cliente_meta()
    if not meta:
        st.error("O token da Meta ainda não foi configurado (CONFIGURAR.bat).")
        return
    if p["status"] != "scheduled":
        ok, msg = db.mudar_status_post(e["id"], p, "scheduled")
        if not ok:
            st.error(msg)
            return
    if not db.antecipar_post(e["id"], p["id"]):
        st.error("Não consegui colocar o post na fila agora. Recarregue a página.")
        return
    with st.spinner("Publicando nas redes... (vídeos podem levar alguns minutos)"):
        res = publicador.processar_fila(db, meta, limite=5, worker="painel",
                                        log=lambda r: _dbg(f"publicar {r['platform']} ok={r['ok']}"))
    st.session_state["res_publicacao"] = res or [{"ok": False, "platform": "fila",
        "mensagem": "Nenhum envio estava pronto na fila (o post pode já ter sido enviado ou estar em espera). "
                    "Veja os destinos do post e a página Logs."}]
    time.sleep(0.5)
    st.rerun()


def rever_post(e: dict, p: dict) -> None:
    """Pede ao editor de marketing para reavaliar o post JÁ salvo (legenda + mídia) e guarda o resultado."""
    chave = f"rev_{p['id']}"
    if st.button("🔎 Rever com o editor", key=f"rever_{p['id']}"):
        ctx = p.get("ai_context") or {}
        g = {"conteudo": {"legendas": [{"titulo": "Legenda final", "texto": p["caption"]}], "hashtags": re.findall(r"#\w+", p["caption"] or ""),
                          "whatsapp": ctx.get("fluxo_whatsapp")},
             "canal": ctx.get("canal") or "Instagram", "formato": ctx.get("formato_ui") or p["post_format"],
             "tom": ctx.get("tom_de_voz") or "", "tema": ctx.get("tema") or p.get("title") or "",
             "publico": ctx.get("publico") or "", "objetivo": ctx.get("objetivo"),
             "whatsapp_link": ctx.get("whatsapp_link"), "texto_midia": ctx.get("texto_midia"),
             "layout_arte": ctx.get("layout_arte")}
        try:
            imgs = []
            for m in p.get("_midias", []):
                dado = requests.get(m["public_url"], timeout=60).content
                if m["kind"] == "image":
                    imgs.append(dado)
                elif m["kind"] == "video":
                    g["video"] = dado
            if imgs:
                g["imagem"] = imgs[0]
            if len(imgs) > 1:
                salvos = {x.get("n"): x for x in (ctx.get("slides") or []) if isinstance(x, dict)}
                g["slides"] = [dict(novo_slide(i + 1, (salvos.get(i + 1) or {}).get("papel") or "conteudo",
                                               (salvos.get(i + 1) or {}).get("texto") or ""),
                                    arte=b, medidas=(salvos.get(i + 1) or {}).get("medidas"))
                               for i, b in enumerate(imgs)]
        except Exception as err:  # noqa: BLE001
            st.error(f"Não consegui baixar a mídia para o editor ver: {erro_seguro(err)}")
            return
        with st.spinner("O editor está revisando..."):
            rev, erro = revisar_campanha(e, g, cfg(e, "briefing"), legenda_final=p["caption"])
        if not rev:
            st.error(f"A revisão falhou: {erro}")
            return
        st.session_state[chave] = rev
        ctx2 = dict(ctx, revisao_editor={"veredito": rev["veredito"], "nota": rev["nota"],
                                         "problemas": rev["problemas"][:12]})
        db.atualizar_contexto_post(e["id"], p["id"], ctx2)
        p["ai_context"] = ctx2
    rev = st.session_state.get(chave)
    if rev:
        mostrar_revisao(rev)
        novas = rev.get("legendas_corrigidas") or []
        if novas and novas[0].get("texto") and novas[0]["texto"].strip() != (p["caption"] or "").strip():
            with st.expander("✍️ Legenda corrigida pelo editor"):
                st.write(novas[0]["texto"])
                if st.button("Usar esta legenda no post", key=f"usar_leg_{p['id']}"):
                    db.atualizar_legenda_post(e["id"], p["id"], novas[0]["texto"])
                    st.session_state.pop(chave, None)
                    st.rerun()


def bloco_publicacao(e: dict, p: dict) -> None:
    status = p["status"]
    if status in ("failed", "partially_published"):
        if st.button("🔁 Tentar de novo os destinos que falharam", key=f"retry_{p['id']}"):
            if db.reenfileirar_post(e["id"], p["id"]):
                st.rerun()
            else:
                st.info("Não há destinos com falha para reenviar.")
        return
    if status not in ("draft", "pending_approval", "scheduled"):
        return
    contas = [c for c in db.credenciais_da_empresa(e["id"]) if c["status"] == "active"
              and c["platform"] in ("facebook", "instagram")]
    ja = {a["credential_id"] for a in p["_alvos"]}
    faltam = {c["id"]: c for c in contas if c["id"] not in ja}
    if faltam:
        esc = st.multiselect("Destinos para este post", list(faltam.keys()), default=list(faltam.keys()),
                             format_func=lambda i: faltam[i]["label"], key=f"dest_{p['id']}")
        if esc and st.button("➕ Adicionar estes destinos", key=f"add_{p['id']}"):
            db.adicionar_destinos(e["id"], p["id"], esc)
            st.rerun()
    if not [a for a in p["_alvos"] if a["status"] != "cancelled"]:
        return
    reprovado = ((p.get("ai_context") or {}).get("revisao_editor") or {}).get("veredito") == "reprovado"
    rev_salva = (p.get("ai_context") or {}).get("revisao_editor") or {}
    if reprovado:
        st.warning("O editor de marketing reprovou esta campanha na hora de salvar.")
    if isinstance(rev_salva.get("problemas"), list) and rev_salva["problemas"]:
        with st.expander(f"📝 O que o editor apontou (nota {rev_salva.get('nota', '?')})", expanded=reprovado):
            for pr in rev_salva["problemas"]:
                st.markdown(f"- **{pr.get('gravidade', '')}** · {pr.get('legenda', '')}: {pr.get('descricao', '')} "
                            f"_Sugestão: {pr.get('sugestao', '')}_")
    rever_post(e, p)
    tz = fuso(e)
    agora_local = datetime.now(tz)
    c1, c2, c3 = st.columns([2, 2, 3])
    dia = c1.date_input("Dia", value=agora_local.date(), key=f"dia_{p['id']}")
    hora = c2.time_input("Hora", value=(agora_local + timedelta(minutes=10)).time().replace(second=0, microsecond=0),
                         key=f"hora_{p['id']}")
    quando = datetime.combine(dia, hora).replace(tzinfo=tz)
    c3.write("")
    if c3.button("📅 Agendar neste horário", key=f"agenda_{p['id']}", disabled=quando <= agora_local):
        ok_a, msg_a = db.agendar_para(e["id"], p, quando)
        if ok_a:
            st.session_state["res_publicacao"] = [{"ok": True, "platform": "agenda",
                "mensagem": f"Agendado para {quando:%d/%m/%Y às %H:%M}. O robô (PUBLICAR.bat) publica nessa hora."}]
            st.rerun()
        else:
            st.error(msg_a)
    ok = st.checkbox("Confirmo: publicar de verdade nas redes sociais agora"
                     + (" (mesmo com a reprovação do editor)" if reprovado else ""), key=f"conf_{p['id']}")
    if st.button("🚀 Publicar agora", key=f"pub_{p['id']}", type="primary", disabled=not ok):
        publicar_agora(e, p)


def pagina_fila() -> None:
    e = empresa
    st.title(f"🗂️ Fila de posts — {e['name']}")
    mostrar_resultado_publicacao()
    posts = db.listar_posts(e["id"])
    if not posts:
        st.info("Nenhum post ainda para esta empresa.")
        return
    filtro = st.multiselect("Filtrar por situação", list(STATUS_POST.keys()), format_func=lambda s: STATUS_POST[s])
    tz = fuso(e)
    for p in posts:
        if filtro and p["status"] not in filtro:
            continue
        quando = parse_data(p.get("scheduled_at"))
        quando_txt = quando.astimezone(tz).strftime("%d/%m/%Y %H:%M") if quando else "-"
        with st.expander(f"{STATUS_POST.get(p['status'], p['status'])} · {p.get('title') or 'Sem título'} · {quando_txt}"):
            col_a, col_b = st.columns([3, 1])
            with col_a:
                st.caption(f"Formato: {p['post_format']} · código {p['id'][:8]}")
                st.write(p.get("caption") or "")
                for a in p["_alvos"]:
                    linha = f"- {a['platform'].title()} — {a['label']}: {STATUS_ALVO.get(a['status'], a['status'])}"
                    if a["status"] in ("pending", "processing") and a["attempt_count"]:
                        linha += f" (tentativa {a['attempt_count']}/{a['max_attempts']})"
                    if a.get("last_error"):
                        linha += f" · erro: {a['last_error'][:160]}"
                    st.markdown(linha)
                    if a.get("external_url"):
                        st.markdown(f"  [ver publicação]({a['external_url']})")
                if not p["_alvos"]:
                    st.caption("Sem contas de destino.")
            with col_b:
                for m in p["_midias"]:
                    if m["kind"] == "image":
                        st.image(m["public_url"], width=140)
                    elif m["kind"] == "video":
                        st.video(m["public_url"])
            acoes = {"draft": [("⏳ Enviar p/ aprovação", "pending_approval"), ("📅 Aprovar e agendar", "scheduled"),
                               ("🚫 Cancelar", "cancelled")],
                     "pending_approval": [("📅 Aprovar e agendar", "scheduled"), ("↩️ Voltar a rascunho", "draft"),
                                          ("🚫 Cancelar", "cancelled")],
                     "scheduled": [("↩️ Voltar a rascunho", "draft"), ("🚫 Cancelar", "cancelled")]}.get(p["status"], [])
            colunas = st.columns(len(acoes)) if acoes else []
            for col, (rotulo, novo) in zip(colunas, acoes):
                if col.button(rotulo, key=f"{p['id']}_{novo}"):
                    ok, msg = db.mudar_status_post(e["id"], p, novo)
                    if ok:
                        st.rerun()
                    else:
                        st.error(msg)
            bloco_publicacao(e, p)


# ==========================================================================
# Página: Métricas (somente leitura)
# ==========================================================================
def _num(v) -> str:
    return "—" if v is None else f"{v:,}".replace(",", ".") if isinstance(v, int) else str(v)


def pagina_metricas() -> None:
    e = empresa
    st.title(f"📈 Métricas — {e['name']}")
    st.caption("Leitura direta da Meta (somente leitura). Nada é alterado nas redes.")
    meta = cliente_meta()
    if not meta:
        st.warning("O token da Meta ainda não foi configurado. Rode o CONFIGURAR.bat e preencha 'Token da Meta'.")
        return
    contas = [c for c in db.credenciais_da_empresa(e["id"]) if c["status"] == "active"]
    ig = next((c for c in contas if c["platform"] == "instagram"), None)
    fb = next((c for c in contas if c["platform"] == "facebook"), None)
    if not ig and not fb:
        st.info("Conecte as contas em **🏢 Empresas > 🔗 Redes sociais** para ver as métricas.")
        return
    dias = st.radio("Período", [7, 28], horizontal=True, format_func=lambda d: f"Últimos {d} dias")
    if st.button("🔄 Atualizar métricas", type="primary"):
        saida = {}
        try:
            with st.spinner("Lendo as métricas na Meta..."):
                if ig:
                    saida["ig"] = meta.metricas_instagram(ig["external_account_id"], ig["parent_external_id"], dias)
                if fb:
                    saida["fb"] = meta.metricas_facebook(fb["external_account_id"], dias)
        except MetaErro as err:
            st.error(f"A Meta recusou a leitura: {erro_seguro(err)}")
        st.session_state["metricas"] = {"empresa": e["id"], "dados": saida}
    dados = (st.session_state.get("metricas") or {})
    if dados.get("empresa") != e["id"]:
        st.info("Clique em **Atualizar métricas** para ler os números agora.")
        return
    ig_d, fb_d = dados["dados"].get("ig"), dados["dados"].get("fb")
    if ig_d:
        perfil = ig_d["perfil"]
        st.subheader(f"Instagram @{perfil.get('username', '')}")
        c = st.columns(4)
        c[0].metric("Seguidores", _num(perfil.get("followers_count")))
        c[1].metric("Publicações", _num(perfil.get("media_count")))
        r = ig_d["resumo"]
        c[2].metric("Alcance", _num(r.get("reach")))
        c[3].metric("Visualizações", _num(r.get("views")))
        c = st.columns(4)
        c[0].metric("Interações", _num(r.get("total_interactions")))
        c[1].metric("Curtidas", _num(r.get("likes")))
        c[2].metric("Comentários", _num(r.get("comments")))
        c[3].metric("Salvamentos", _num(r.get("saves")))
        if ig_d["posts"]:
            st.markdown("**Últimos posts**")
            st.dataframe([{"Data": (x.get("timestamp") or "")[:10], "Tipo": x.get("media_product_type") or x.get("media_type"),
                           "Legenda": (x.get("caption") or "")[:60], "Curtidas": x.get("like_count"),
                           "Comentários": x.get("comments_count"), "Alcance": x["insights"].get("reach"),
                           "Visualizações": x["insights"].get("views"), "Link": x.get("permalink")}
                          for x in ig_d["posts"]], use_container_width=True)
        for a in ig_d["avisos"]:
            st.caption(f"ℹ️ Instagram: {a}")
    if fb_d:
        pg = fb_d["pagina"]
        st.subheader(f"Facebook — {pg.get('name', '')}")
        c = st.columns(4)
        c[0].metric("Seguidores", _num(pg.get("followers_count")))
        c[1].metric("Curtidas da Página", _num(pg.get("fan_count")))
        r = fb_d["resumo"]
        c[2].metric("Visualizações", _num(r.get("page_media_view")))
        c[3].metric("Interações com posts", _num(r.get("page_post_engagements")))
        if fb_d["posts"]:
            st.markdown("**Últimos posts**")
            st.dataframe([{"Data": (x.get("data") or "")[:10], "Texto": x.get("mensagem"), "Reações": x.get("reacoes"),
                           "Comentários": x.get("comentarios"), "Compartilhamentos": x.get("compartilhamentos"),
                           "Link": x.get("link")} for x in fb_d["posts"]], use_container_width=True)
        for a in fb_d["avisos"]:
            st.caption(f"ℹ️ Facebook: {a}")


# ==========================================================================
# Página: Logs
# ==========================================================================
def pagina_logs() -> None:
    e = empresa
    st.title(f"🧾 Logs — {e['name']}")
    eventos = db.listar_eventos(e["id"], 150)
    if not eventos:
        st.info("Sem eventos registrados ainda.")
        return
    nivel = st.multiselect("Nível", ["info", "warning", "error", "debug"], default=["info", "warning", "error"])
    st.dataframe([ev for ev in eventos if ev["level"] in nivel])


# ==========================================================================
# Roteador
# ==========================================================================
if pagina == "☀️ Campanha de energia":
    import campanha_energia
    importlib.reload(campanha_energia)
    if empresa:
        campanha_energia.pagina(empresa,db)
    else:
        st.info("Selecione uma empresa.")
elif pagina == "🏢 Empresas":
    pagina_empresas()
elif not empresa:
    st.title("Bem-vindo")
    st.info("Cadastre a primeira empresa no menu **🏢 Empresas**.")
elif pagina == "📊 Criar campanha":
    pagina_campanha()
elif pagina == "🗂️ Fila de posts":
    pagina_fila()
elif pagina == "📈 Métricas":
    pagina_metricas()
elif pagina == "💬 Atendimento":
    atendimento.pagina(db.c, empresa, registrar_evento=db.registrar_evento, erro_seguro=erro_seguro)
else:
    pagina_logs()
