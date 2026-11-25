# -*- coding: utf-8 -*-
"""
base_conhecimento.py — base de conhecimento (RAG) do agente de WhatsApp.

O que faz:
  * lê os documentos da pasta "Documentos projeto\\Base agente WhatsApp\\<empresa>";
  * divide cada documento em trechos curtos, respeitando os títulos;
  * gera os vetores (embeddings) com o Gemini (modelo gemini-embedding-2, 768 números);
  * grava no Supabase pela função kb_substituir_documento (arquivo 004);
  * busca os trechos certos para uma pergunta (função match_knowledge).

Regras de segurança:
  * Toda busca e toda gravação levam a empresa (tenant_id): uma empresa nunca vê a outra.
  * Trechos "interno" nunca voltam na busca padrão (só com incluir_interno=True).
  * Arquivos com número de CPF, número de cartão ou "senha:" são recusados.
  * Nenhuma chave é lida aqui: os clientes do Gemini e do Supabase chegam prontos.
"""
from __future__ import annotations

import hashlib
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Configuração
# --------------------------------------------------------------------------
MODELO_EMBEDDING = "gemini-embedding-2"
DIMENSOES = 768
VERSAO_DIVISAO = "v1"              # muda quando a regra de dividir trechos mudar (força reprocessar)

PRECO_USD_POR_MILHAO_TOKENS = 0.20  # preço do gemini-embedding-2 (out/2026)
COTACAO_USD_BRL = 6.00              # cotação propositalmente alta, para a estimativa nunca ficar abaixo do real
TETO_SEM_PERGUNTAR_BRL = 2.00       # acima disso o carregador pede confirmação antes de gastar

MAX_CARACTERES_TRECHO = 1500
LOTE_EMBEDDINGS = 20

EXTENSOES_ACEITAS = {".md", ".txt", ".pdf", ".docx"}
PASTA_PROJETO = Path(__file__).resolve().parent.parent
PASTA_BASE = PASTA_PROJETO / "Documentos projeto" / "Base agente WhatsApp"


class ErroBase(Exception):
    """Erro com mensagem já pronta para mostrar ao usuário."""


# --------------------------------------------------------------------------
# Leitura dos arquivos
# --------------------------------------------------------------------------
def ler_arquivo(caminho: Path) -> str:
    ext = caminho.suffix.lower()
    if ext in (".md", ".txt"):
        dados = caminho.read_bytes()
        for cod in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                return dados.decode(cod)
            except UnicodeDecodeError:
                continue
        return dados.decode("utf-8", errors="replace")
    if ext == ".pdf":
        from pypdf import PdfReader
        leitor = PdfReader(str(caminho))
        paginas = [(p.extract_text() or "").strip() for p in leitor.pages]
        texto = "\n\n".join(p for p in paginas if p)
        if len(texto.strip()) < 50:
            raise ErroBase(f"{caminho.name}: o PDF parece ser uma foto escaneada (sem texto). "
                           "Exporte de novo com texto ou salve como .docx.")
        return texto
    if ext == ".docx":
        import docx
        d = docx.Document(str(caminho))
        partes: list[str] = []
        for par in d.paragraphs:
            t = par.text.strip()
            if not t:
                continue
            estilo = (par.style.name or "").lower() if par.style is not None else ""
            m = re.search(r"(heading|título|titulo)\s*(\d)", estilo)
            partes.append(("#" * int(m.group(2)) + " " + t) if m else t)
        for tabela in d.tables:
            for linha in tabela.rows:
                celulas = [c.text.strip() for c in linha.cells]
                if any(celulas):
                    partes.append(" | ".join(celulas))
        return "\n\n".join(partes)
    raise ErroBase(f"{caminho.name}: formato não aceito (use .md, .txt, .pdf ou .docx).")


def separar_cabecalho(texto: str) -> tuple[dict, str]:
    """Lê o cabeçalho opcional entre linhas '---' no início do arquivo."""
    cab: dict = {}
    t = texto.lstrip("﻿")
    if t.startswith("---"):
        fim = t.find("\n---", 3)
        if fim != -1:
            for linha in t[3:fim].splitlines():
                if ":" in linha:
                    k, v = linha.split(":", 1)
                    cab[k.strip().lower()] = v.strip()
            t = t[fim + 4:]
    return cab, t.strip()


_CPF = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b|\bcpf\W{0,4}\d{11}\b", re.IGNORECASE)
_CARTAO = re.compile(r"\b(?:\d{4}[ .-]){3}\d{4}\b")
_SENHA = re.compile(r"\bsenha\s*[:=]\s*\S", re.IGNORECASE)


def checar_dados_sensiveis(texto: str) -> list[str]:
    problemas = []
    if _CPF.search(texto):
        problemas.append("tem número de CPF")
    if _CARTAO.search(texto):
        problemas.append("tem número de cartão")
    if _SENHA.search(texto):
        problemas.append('tem a palavra "senha:" seguida de um valor')
    return problemas


# --------------------------------------------------------------------------
# Divisão em trechos
# --------------------------------------------------------------------------
@dataclass
class Trecho:
    conteudo: str
    secao: str
    tokens_estimados: int = 0
    metadata: dict = field(default_factory=dict)


def estimar_tokens(texto: str) -> int:
    # Português fica perto de 4 caracteres por token; dividir por 3 deixa a estimativa folgada.
    return max(1, len(texto) // 3)


def _blocos(texto: str) -> list[str]:
    """Separa em parágrafos e itens de lista (cada item vira um bloco)."""
    blocos: list[str] = []
    atual: list[str] = []
    for linha in texto.splitlines():
        s = linha.strip()
        if not s:
            if atual:
                blocos.append(" ".join(atual))
                atual = []
            continue
        if re.match(r"^([-*•]|\d+[.)])\s+", s) and atual:
            blocos.append(" ".join(atual))
            atual = []
        atual.append(s)
    if atual:
        blocos.append(" ".join(atual))
    return blocos


def _fatiar(blocos: list[str], limite: int) -> list[str]:
    """Junta blocos até o limite; repete o último bloco no trecho seguinte (contexto)."""
    partes: list[str] = []
    atual: list[str] = []
    tamanho = 0
    for b in blocos:
        while len(b) > limite:  # bloco gigante: corta em frases
            corte = b.rfind(". ", 0, limite)
            corte = corte + 1 if corte > limite // 2 else limite
            blocos_extra, b = b[:corte].strip(), b[corte:].strip()
            if atual:
                partes.append("\n".join(atual))
                atual, tamanho = [], 0
            partes.append(blocos_extra)
        if atual and tamanho + len(b) + 1 > limite:
            partes.append("\n".join(atual))
            ultimo = atual[-1]
            atual = [ultimo] if len(ultimo) < limite // 3 else []
            tamanho = sum(len(x) + 1 for x in atual)
        atual.append(b)
        tamanho += len(b) + 1
    if atual:
        partes.append("\n".join(atual))
    return partes


def dividir_em_trechos(texto: str, titulo_doc: str, limite: int = MAX_CARACTERES_TRECHO) -> list[Trecho]:
    """
    Divide pelo título de cada seção (# ## ###). Cada trecho começa com o "caminho"
    (Documento > Seção > Subseção), para o agente saber de onde a informação veio.
    """
    secoes: list[tuple[list[str], list[str]]] = []
    pilha: list[tuple[int, str]] = []
    corpo: list[str] = []

    def fechar():
        if any(l.strip() for l in corpo):
            secoes.append(([t for _, t in pilha], list(corpo)))

    for linha in texto.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*\S)\s*$", linha)
        if m:
            fechar()
            corpo = []
            nivel = len(m.group(1))
            while pilha and pilha[-1][0] >= nivel:
                pilha.pop()
            pilha.append((nivel, m.group(2).strip()))
        else:
            corpo.append(linha)
    fechar()

    trechos: list[Trecho] = []
    for caminho, linhas in secoes:
        secao = " > ".join(caminho) if caminho else "Geral"
        cabeca = " > ".join([titulo_doc] + caminho)
        for parte in _fatiar(_blocos("\n".join(linhas)), limite):
            conteudo = f"{cabeca}\n{parte}".strip()
            trechos.append(Trecho(conteudo=conteudo, secao=secao,
                                  tokens_estimados=estimar_tokens(conteudo),
                                  metadata={"secao": secao}))
    return trechos


# --------------------------------------------------------------------------
# Documento pronto para carregar
# --------------------------------------------------------------------------
@dataclass
class Documento:
    chave: str              # "arquivo:cred-finance/01_portfolio.md" -> única por empresa
    titulo: str
    visibilidade: str       # 'publico' ou 'interno'
    trechos: list[Trecho]
    hash: str
    arquivo: Path
    fonte: str = ""

    @property
    def tokens(self) -> int:
        return sum(t.tokens_estimados for t in self.trechos) + 10 * len(self.trechos)


def _titulo_do_nome(caminho: Path) -> str:
    nome = re.sub(r"^\d+[_ -]*", "", caminho.stem).replace("_", " ").strip()
    return nome[:1].upper() + nome[1:] if nome else caminho.stem


def preparar_documento(caminho: Path, pasta_empresa: Path) -> Documento:
    texto = ler_arquivo(caminho)
    problemas = checar_dados_sensiveis(texto)
    if problemas:
        raise ErroBase(f"{caminho.name} foi RECUSADO porque {', '.join(problemas)}. "
                       "Tire esse dado do arquivo e rode de novo.")
    cab, corpo = separar_cabecalho(texto)
    relativo = caminho.relative_to(pasta_empresa).as_posix()
    interno = (relativo.lower().startswith("interno/") or caminho.name.lower().startswith("interno_")
               or cab.get("visibilidade", "").lower() == "interno")
    titulo = cab.get("titulo") or _titulo_do_nome(caminho)
    trechos = dividir_em_trechos(corpo, titulo)
    if not trechos:
        raise ErroBase(f"{caminho.name} está vazio.")
    visibilidade = "interno" if interno else "publico"
    for t in trechos:
        t.metadata.update({"arquivo": relativo})
    base_hash = "\n".join([MODELO_EMBEDDING, str(DIMENSOES), VERSAO_DIVISAO, visibilidade, titulo]
                          + [t.conteudo for t in trechos])
    return Documento(
        chave=f"arquivo:{relativo}",
        titulo=titulo,
        visibilidade=visibilidade,
        trechos=trechos,
        hash=hashlib.sha256(base_hash.encode("utf-8")).hexdigest(),
        arquivo=caminho,
        fonte=cab.get("fonte", ""),
    )


def listar_arquivos(pasta_empresa: Path) -> list[Path]:
    arquivos = []
    for p in sorted(pasta_empresa.rglob("*")):
        if not p.is_file():
            continue
        if p.name.startswith((".", "~$")) or p.name.lower().startswith("leia-me"):
            continue
        if p.suffix.lower() in EXTENSOES_ACEITAS:
            arquivos.append(p)
    return arquivos


def custo_brl(tokens: int) -> float:
    return tokens / 1_000_000 * PRECO_USD_POR_MILHAO_TOKENS * COTACAO_USD_BRL


# --------------------------------------------------------------------------
# Gemini: vetores
# --------------------------------------------------------------------------
def _texto_documento(titulo: str, conteudo: str) -> str:
    return f"title: {titulo or 'none'} | text: {conteudo}"


def _texto_pergunta(pergunta: str) -> str:
    return f"task: search result | query: {pergunta}"


def _e_erro_de_cota(e: Exception) -> bool:
    t = f"{type(e).__name__} {e}".lower()
    return any(s in t for s in ("429", "resource_exhausted", "quota", "billing", "spend", "budget"))


class ErroContagem(ErroBase):
    """O Gemini devolveu um número de vetores diferente do número de textos."""


def _embed_lote(cliente_gemini, textos: list[str]) -> list[list[float]]:
    from google.genai import types
    try:
        conteudos = [types.Content(parts=[types.Part.from_text(text=t)]) for t in textos]
    except TypeError:
        conteudos = [types.Content(parts=[types.Part(text=t)]) for t in textos]
    r = cliente_gemini.models.embed_content(
        model=MODELO_EMBEDDING,
        contents=conteudos,
        config=types.EmbedContentConfig(output_dimensionality=DIMENSOES),
    )
    vetores = [list(e.values) for e in (r.embeddings or [])]
    if len(vetores) != len(textos):
        raise ErroContagem(f"O Gemini devolveu {len(vetores)} vetores para {len(textos)} textos.")
    for v in vetores:
        if len(v) != DIMENSOES:
            raise ErroBase(f"O Gemini devolveu vetor com {len(v)} números (esperado {DIMENSOES}).")
    return vetores


def _com_tentativas(funcao, tentativas: int = 4):
    """Repete em caso de sobrecarga/instabilidade, esperando cada vez mais (5, 10, 20 s)."""
    for n in range(1, tentativas + 1):
        try:
            return funcao()
        except ErroBase:
            raise
        except Exception as e:  # noqa: BLE001
            if n == tentativas:
                if _e_erro_de_cota(e):
                    raise ErroBase(
                        "O Gemini recusou por limite de uso ou de gasto (pode ser o teto de R$ 50/mês). "
                        "Confira em Google Cloud → Faturamento → Budgets & caps.") from e
                raise ErroBase(f"Falha no Gemini: {type(e).__name__}: {str(e)[:200]}") from e
            time.sleep(min(5 * 2 ** (n - 1), 40))


def gerar_vetores(cliente_gemini, textos: list[str]) -> list[list[float]]:
    """Gera os vetores em lotes. Se a biblioteca juntar os textos, refaz um a um."""
    saida: list[list[float]] = []
    for i in range(0, len(textos), LOTE_EMBEDDINGS):
        lote = textos[i:i + LOTE_EMBEDDINGS]
        try:
            saida.extend(_com_tentativas(lambda: _embed_lote(cliente_gemini, lote)))
        except ErroContagem:
            for t in lote:
                saida.extend(_com_tentativas(lambda t=t: _embed_lote(cliente_gemini, [t])))
    return saida


def vetores_do_documento(cliente_gemini, doc: Documento) -> list[list[float]]:
    return gerar_vetores(cliente_gemini, [_texto_documento(doc.titulo, t.conteudo) for t in doc.trechos])


def vetor_da_pergunta(cliente_gemini, pergunta: str) -> list[float]:
    return gerar_vetores(cliente_gemini, [_texto_pergunta(pergunta)])[0]


def _vetor_texto(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


# --------------------------------------------------------------------------
# Supabase
# --------------------------------------------------------------------------
def empresa_por_slug(sb, slug: str) -> dict | None:
    r = sb.table("tenants").select("id,slug,name,status").eq("slug", slug).limit(1).execute()
    return (r.data or [None])[0]


def conferir_instalacao_004(sb) -> None:
    try:
        sb.table("knowledge_sources").select("id,source_key,visibility").limit(1).execute()
    except Exception as e:  # noqa: BLE001
        raise ErroBase("O Supabase ainda não tem a base de conhecimento nova. Rode o arquivo "
                       "004_base_conhecimento_whatsapp.sql no SQL Editor e tente de novo.") from e


def documentos_no_banco(sb, tenant_id: str) -> dict[str, dict]:
    r = (sb.table("knowledge_sources").select("id,source_key,content_hash,status,visibility,title")
         .eq("tenant_id", tenant_id).execute())
    return {d["source_key"]: d for d in (r.data or []) if d.get("source_key")}


def gravar_documento(sb, tenant_id: str, doc: Documento, vetores: list[list[float]]) -> str:
    trechos = [{
        "content": t.conteudo,
        "embedding": [round(x, 7) for x in v],
        "token_count": t.tokens_estimados,
        "metadata": t.metadata,
    } for t, v in zip(doc.trechos, vetores)]
    r = sb.rpc("kb_substituir_documento", {
        "p_tenant_id": tenant_id,
        "p_source_key": doc.chave,
        "p_title": doc.titulo,
        "p_source_type": "document",
        "p_visibility": doc.visibilidade,
        "p_content_hash": doc.hash,
        "p_embedding_model": f"{MODELO_EMBEDDING}@{DIMENSOES}",
        "p_metadata": {"arquivo": doc.chave.removeprefix("arquivo:"), "fonte": doc.fonte,
                       "trechos": len(doc.trechos), "divisao": VERSAO_DIVISAO},
        "p_chunks": trechos,
    }).execute()
    return str(r.data)


def remover_ausentes(sb, tenant_id: str, chaves_presentes: list[str]) -> int:
    if not chaves_presentes:
        return 0
    r = sb.rpc("kb_remover_ausentes", {"p_tenant_id": tenant_id,
                                       "p_chaves_presentes": chaves_presentes}).execute()
    return int(r.data or 0)


def buscar(sb, cliente_gemini, tenant_id: str, pergunta: str, quantidade: int = 5,
           incluir_interno: bool = False, vetor: list[float] | None = None) -> list[dict]:
    """
    Busca os trechos mais úteis para responder `pergunta`, SÓ da empresa `tenant_id`.
    Por padrão devolve apenas trechos públicos (que podem ser ditos ao cliente).
    """
    if not tenant_id:
        raise ErroBase("Busca sem empresa (tenant_id) não é permitida.")
    v = vetor if vetor is not None else vetor_da_pergunta(cliente_gemini, pergunta)
    r = sb.rpc("match_knowledge", {
        "p_tenant_id": tenant_id,
        "p_query_embedding": _vetor_texto(v),
        "p_query_text": pergunta,
        "p_match_count": quantidade,
        "p_incluir_interno": bool(incluir_interno),
    }).execute()
    linhas = r.data or []
    if not incluir_interno:  # terceira trava, além do banco
        linhas = [l for l in linhas if l.get("visibility") == "publico"]
    return linhas


def sem_acento(t: str) -> str:
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
