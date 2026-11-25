# -*- coding: utf-8 -*-
"""
padrao_visual.py — padrão de arte e vídeo da agência.

Um só lugar com:
  * os blocos de texto que entram nos PROMPTS de imagem e vídeo (API ou Gemini do navegador),
    para que a mídia já nasça com espaço livre para texto, tarja e logo;
  * o checklist e o pedido do DIRETOR DE ARTE/VÍDEO (revisor especializado em mídia);
  * o texto do contato por rede (Facebook x Instagram).
"""
import re


# --------------------------------------------------------------------------
# Prompts de IMAGEM
# --------------------------------------------------------------------------
def bloco_imagem_en(vertical: bool = False) -> str:
    """Regras de composição para o prompt em inglês enviado à API de imagem."""
    return (
        " COMPOSITION RULES (mandatory): one clear main subject, with the face/product in the upper two thirds of the "
        "frame; keep the bottom 30% of the frame as soft, uncluttered background and keep the bottom-right corner "
        "empty (a text banner, a contact badge and a logo will be added later); do not place people or important "
        "objects in the bottom 30%; realistic Brazilian setting that matches the target audience and the product; "
        "natural skin, natural hands with five fingers, no distorted faces or objects; subject looking at the camera "
        "or toward the empty lower area; soft natural light; brand colors only as subtle accents. "
        "NO text, NO letters, NO numbers, NO logos, NO watermarks, NO UI elements inside the image. "
        + ("Vertical 9:16 framing." if vertical else "Vertical 4:5 framing.")
    )


def bloco_imagem_pt(vertical: bool = False) -> str:
    return (
        "Regras de composição (obrigatórias): um único assunto principal, com rosto/produto nos dois terços "
        "superiores; deixe os 30% inferiores da imagem com fundo limpo e suave, e o canto inferior direito vazio "
        "(depois entram a faixa de texto, a tarja de contato e a logo); não coloque pessoas nem objetos importantes "
        "na parte de baixo; cenário brasileiro realista, coerente com o público e o produto; pele e mãos naturais "
        "(cinco dedos), sem rostos ou objetos deformados; pessoa olhando para a câmera ou para a área vazia "
        "inferior; luz natural suave. Sem nenhum texto, letras, números, logotipo ou marca d'água dentro da imagem. "
        + ("Enquadramento vertical 9:16." if vertical else "Enquadramento vertical 4:5.")
    )


# --------------------------------------------------------------------------
# Prompts de VÍDEO
# --------------------------------------------------------------------------
def bloco_video_pt() -> str:
    return (
        "Padrão do vídeo (obrigatório): formato vertical 9:16, cerca de 8 segundos, plano médio com câmera estável "
        "e movimento suave. UMA pessoa adulta brasileira, aparência profissional e simpática, compatível com o "
        "público. Cenário limpo e coerente com o produto e o público (ex.: sala/escritório/casa), fundo levemente "
        "desfocado, luz natural, a mesma iluminação o vídeo todo. A pessoa olha para a câmera e FALA em português do "
        "Brasil, com voz clara e natural (no máximo 20 palavras, sem prometer aprovação, taxa ou prazo, e terminando "
        "convidando a conversar no WhatsApp). Gestos naturais das mãos; se apontar, aponta para o lado ESQUERDO ou "
        "para baixo, na direção do texto — nunca para o canto inferior direito, onde ficará a logo. Rosto no terço "
        "superior do quadro; mantenha os 30% inferiores e o canto inferior direito livres (a faixa de texto, a tarja "
        "e a logo são colocadas depois). SEM texto, legendas, números, logotipo ou marca d'água dentro do vídeo. "
        "Mãos e rosto naturais, sem deformações. Áudio limpo, sem música alta."
    )


def bloco_redator_prompts() -> str:
    """Instruções para o redator (Gemini texto) gerar prompts de mídia já no padrão."""
    return (
        "PADRÃO DE ARTE E VÍDEO (aplique ao 'prompt_imagem' e ao 'prompt_video'):\n"
        "- Imagem: assunto principal nos dois terços superiores; 30% inferiores limpos e canto inferior direito "
        "vazio (entrarão faixa de texto, tarja de contato e logo); sem texto, logotipo ou marca d'água; cenário "
        "brasileiro realista coerente com público e produto; mãos e rosto naturais.\n"
        "- Vídeo: vertical 9:16, ~8 s, UMA pessoa olhando para a câmera e falando em português do Brasil (até 20 "
        "palavras, sem promessas, terminando com convite ao WhatsApp quando o objetivo for WhatsApp); gestos naturais; "
        "se apontar, aponta para a esquerda/baixo (nunca para o canto inferior direito); descreva cenário, luz, "
        "câmera e a fala exata entre aspas; 30% inferiores e canto inferior direito livres; sem texto na tela.\n"
    )


# --------------------------------------------------------------------------
# Diretor de arte e vídeo
# --------------------------------------------------------------------------
CHECKLIST_ARTE = [
    "Composição: assunto principal claro, rosto/produto nos dois terços superiores, sem cortes estranhos",
    "Área livre: nada importante na parte de baixo, sob a faixa de texto, a tarja e a logo",
    "Texto na arte: legível e com bom contraste, até 3 linhas, sem cobrir rosto ou produto; ortografia, acentuação e PONTUAÇÃO corretas (pergunta termina com ?)",
    "Logo: nítida, tamanho padrão (12% a 20% da largura), com margem, fora do texto e do rosto",
    "Tarja do WhatsApp (se houver): legível, número correto, sem colidir com a logo",
    "Cenário, figurino e pessoa coerentes com o público e o produto (realismo, sem estereótipos)",
    "Naturalidade: mãos, rosto, olhos e objetos sem deformações, sem artefatos nem texto gerado ilegível",
    "Cores e estilo alinhados à paleta e ao tom da marca",
    "Proporção adequada ao canal (4:5 feed, 9:16 stories/reels)",
    "Conformidade visual: sem valores, taxas, promessas, 'aprovado', marca de terceiros ou marca d'água",
]
CHECKLIST_VIDEO = [
    "Gancho: algo chama atenção nos 2 primeiros segundos",
    "Fala: clara, sotaque brasileiro natural, até ~20 palavras, sem promessas e com convite à ação",
    "Linguagem corporal: olha para a câmera; gestos naturais; aponta para o lado do texto, nunca para a logo",
    "Cenário e luz consistentes durante todo o vídeo; fundo coerente com público e produto",
    "Texto na tela: aparece legível, não cobre o rosto e está com ortografia e pontuação corretas (pergunta termina com ?); fala e legenda iguais ao roteiro",
    "Logo presente o vídeo todo, no tamanho padrão e sem tapar nada importante",
    "Áudio limpo (sem ruído nem música alta) e sincronizado com a boca",
]


def montar_prompt_diretor(empresa: dict, canal: str, formato: str, tema: str, publico: str, tem_imagem: bool,
                          tem_video: bool, texto_midia: str = "", layout: dict | None = None,
                          padrao: list | None = None, conceito: str = "", cores: str = "") -> str:
    itens = CHECKLIST_ARTE + (CHECKLIST_VIDEO if tem_video else [])
    lista = "\n".join(f"  - {i}" for i in itens)
    fatos = []
    if layout:
        fatos.append("Medidas reais do layout aplicado pelo sistema: " + ", ".join(
            f"{k}={v}" for k, v in layout.items() if v is not None))
    if padrao:
        fatos.append("Conferência automática: " + "; ".join(("OK" if ok else "FALHOU") + " — " + m
                                                              for ok, m in padrao))
    if texto_midia:
        fatos.append(f'Texto que deve estar escrito na mídia: "{texto_midia}".')
    fatos_txt = "\n".join(f"- {f}" for f in fatos) or "- (sem medidas)"
    regras = empresa.get("business_rules") or "Sem regras específicas."
    anexo = ("a IMAGEM final (já com texto, tarja e logo)" if tem_imagem else "") + \
            (" e " if tem_imagem and tem_video else "") + \
            ("o VÍDEO final (já com texto e logo)" if tem_video else "")
    return f"""
Você é o DIRETOR DE ARTE E VÍDEO sênior de uma agência de marketing, revisando a mídia da marca
**{empresa['name']}** ANTES da publicação. Olhe {anexo} anexado(a) e seja rigoroso, não elogie por educação.

Canal: {canal}. Formato: {formato}. Tema: "{tema}". Público: "{publico or 'principal da marca'}".
Conceito visual planejado: {conceito or '(não informado)'}.
Paleta da marca: {cores or 'não informada'}. Regras da marca: {regras}

FATOS MEDIDOS (confie neles para tamanhos/posições; use os olhos para o resto):
{fatos_txt}

REGRA DA LOGO: a logo é OBRIGATÓRIA e já fica na margem padrão do sistema (cerca de 6% da borda), feita para não
conflitar com a interface das redes. NUNCA sugira remover, diminuir ou afastar a logo por estar "perto da borda"; só
aponte problema se ela cobrir texto ou rosto, estiver ilegível ou ausente.

{CONFERENCIA_TEXTO_MIDIA}
Avalie cada item (ok true/false + comentário curto em português):
{lista}

Se houver problema, escreva em "prompt_melhorado" um NOVO PEDIDO COMPLETO, em português, pronto para colar no
gerador de {'imagem e vídeo' if tem_video and tem_imagem else ('vídeo' if tem_video else 'imagem')}, que corrija o
que está errado e já inclua: cenário, pessoa, luz, enquadramento (30% inferiores e canto inferior direito livres),
{'fala exata entre aspas, direção do olhar e para onde aponta,' if tem_video else ''} e a proibição de texto/logo
dentro da mídia. Se estiver tudo bom, devolva "prompt_melhorado" vazio.

Responda SOMENTE com JSON válido neste formato exato:
{{
  "nota": 0 a 10,
  "veredito": "aprovado" ou "ajustar" ou "reprovado",
  "resumo": "2 frases em português",
  "checklist": [{{"item": "nome", "ok": true, "comentario": "curto"}}],
  "problemas": [{{"gravidade": "alta" ou "media" ou "baixa", "legenda": "midia",
                  "descricao": "o que está errado", "sugestao": "como corrigir"}}],
  "prompt_melhorado": "texto ou vazio"
}}
Regra do veredito: "reprovado" se houver problema de gravidade alta (ex.: logo sobre o texto, texto ilegível,
rosto deformado, marca d'água, promessa visual proibida); "ajustar" se só média/baixa; "aprovado" se nada relevante.
""".strip()


# --------------------------------------------------------------------------
# Contato do WhatsApp por rede
# --------------------------------------------------------------------------
_LINHA_WA = re.compile(r"^[^\n]*https?://wa\.me/(\d{10,15})[^\s]*[^\n]*$", re.M)


def formatar_telefone(numero: str) -> str:
    d = re.sub(r"\D", "", numero or "")
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return d


# --------------------------------------------------------------------------
# FORMATAÇÃO DA LEGENDA (parágrafos, emojis, ordem) — vale para todas as redes
# --------------------------------------------------------------------------
REGRAS_LEGENDA = (
    "FORMATO DA LEGENDA (vale para Facebook, Instagram e qualquer rede):\n"
    "- Escreva em PARÁGRAFOS CURTOS (1 a 2 frases cada), separados por UMA LINHA EM BRANCO (use \\n\\n dentro do texto "
    "JSON). Nada de bloco de texto corrido.\n"
    "- Estrutura: gancho na primeira linha → valor/explicação → convite à ação → ressalva obrigatória (se houver).\n"
    "- Use emojis com bom senso: UM emoji relevante no início de cada parágrafo (3 a 5 no total), combinando com o "
    "assunto (ex.: 💡 ideia, ✅ benefício, 💬 conversa, 📲 contato, ℹ️ ressalva). Sem exagero, sem emoji repetido em "
    "sequência e sem emojis infantis em assunto financeiro sério.\n"
    "- As hashtags ficam fora do texto (campo próprio). Não escreva URL nem telefone na legenda.\n"
)

_RE_EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u2B06\u2139\u203C\u2049\u2705\u274C\u2764]")
_EMOJI_POR_ASSUNTO = [
    (r"sujeito|an[aá]lise de cr[eé]dito|condi[cç][oõ]es|ressalva|\*", "ℹ️"),
    (r"whatsapp|conversa|atendente|fale|chame|mande uma mensagem|contato", "💬"),
    (r"simul|calcul|compare|conhe[cç]a", "🧮"),
    (r"d[uú]vida|entender|entenda|como funciona|saiba|explic", "💡"),
    (r"parcela|folha|holerite|sal[aá]rio|dinheiro|cr[eé]dito|consignado|or[cç]amento|finan", "💰"),
    (r"seguran|transpar|confian|tranquil|respons", "✅"),
]


def _emoji_do_paragrafo(par: str, usados: list) -> str:
    baixo = par.lower()
    for padrao, emo in _EMOJI_POR_ASSUNTO:
        if re.search(padrao, baixo) and (not usados or usados[-1] != emo):
            return emo
    return "✨" if not usados or usados[-1] != "✨" else "👉"


def formatar_legenda(legenda: str, com_emoji: bool = True) -> str:
    """Deixa a legenda bonita e legível em QUALQUER rede: parágrafos curtos separados por linha em branco, a
    ressalva e a chamada do WhatsApp em parágrafo próprio e as hashtags no fim. Se a legenda não tiver nenhum emoji,
    coloca um emoji por parágrafo (rede de segurança; o redator já costuma enviar com emojis)."""
    bruto = (legenda or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not bruto:
        return ""
    blocos = [b.strip() for b in re.split(r"\n\s*\n", bruto) if b.strip()]
    texto, hashtags, whats, ressalva = [], [], [], []
    for b in blocos:
        linhas = [l.strip() for l in b.split("\n") if l.strip()]
        so_tags = all(re.fullmatch(r"(#\w+\s*)+", l) for l in linhas)
        if so_tags:
            hashtags.append(" ".join(linhas))
            continue
        # linhas soltas dentro do bloco: separa WhatsApp / hashtags das demais
        resto = []
        for l in linhas:
            if re.fullmatch(r"(#\w+\s*)+", l):
                hashtags.append(l)
            elif l.startswith("👆") or (re.search(r"(?:whatsapp|wa\.me)", l, re.I)
                                       and (re.search(r"wa\.me/|\(\d{2}\)\s*\d", l) or l.startswith("💬"))):
                whats.append(l)
            else:
                resto.append(l)
        if resto:
            texto.append(" ".join(resto) if len(resto) == 1 or max(len(x) for x in resto) > 60 else "\n".join(resto))
    # separa frases longas em parágrafos curtos (até ~2 frases / ~190 caracteres) e isola a ressalva
    paragrafos = []
    for b in texto:
        if "\n" in b:
            paragrafos.extend(x for x in b.split("\n") if x.strip())
            continue
        frases = _dividir_frases(b)
        atual = ""
        for f in frases:
            if f.startswith("*") or re.match(r"(?i)^\W*(sujeito|condi[cç][oõ]es|sob an[aá]lise)", f):
                if atual:
                    paragrafos.append(atual); atual = ""
                ressalva.append(f)
                continue
            if atual and (len(atual) + len(f) + 1 > 190 or atual.count(" ") > 28):
                paragrafos.append(atual); atual = f
            else:
                atual = (atual + " " + f).strip()
        if atual:
            paragrafos.append(atual)
    # ressalvas que já estavam em parágrafo próprio
    for i, p in enumerate(list(paragrafos)):
        if p.startswith("*") or re.match(r"(?i)^\W*sujeito a an[aá]lise", p):
            ressalva.append(p); paragrafos.remove(p)
    # emojis: só se a legenda inteira não tiver nenhum
    if com_emoji and sum(len(p) for p in paragrafos) > 40 and not _RE_EMOJI.search("\n".join(paragrafos + ressalva)):
        usados = []
        for i, p in enumerate(paragrafos):
            emo = _emoji_do_paragrafo(p, usados)
            usados.append(emo)
            paragrafos[i] = f"{emo} {p}"
        ressalva = [("ℹ️ " + r.lstrip("* ").strip()) if not r.startswith("ℹ") else r for r in ressalva]
        ressalva = [re.sub(r"^ℹ️\s*", "ℹ️ *", r) if not re.match(r"^ℹ️\s*\*", r) else r for r in ressalva]
    partes = paragrafos + ressalva + (["\n".join(whats)] if whats else []) + hashtags
    return "\n\n".join(partes).strip()


def adaptar_legenda(legenda: str, plataforma: str) -> str:
    """
    O link longo do WhatsApp não funciona em legenda do Instagram e fica feio no Facebook.
      * Facebook: link curto e limpo (wa.me/número), que é clicável.
      * Instagram: o número escrito + chamada para o link da bio (legenda do Instagram não tem link clicável).
    """
    def troca(m):
        num = m.group(1)
        if plataforma == "instagram":
            return f"💬 WhatsApp: {formatar_telefone(num)}\n👆 Toque no link da bio para conversar agora"
        if plataforma == "facebook":
            return f"💬 Chame no WhatsApp: https://wa.me/{num}"
        return m.group(0)
    texto = _LINHA_WA.sub(troca, legenda or "")
    # Post orgânico não tem botão: troca "clique no botão" pela chamada correta de cada rede.
    if plataforma in ("instagram", "facebook"):
        chamada = "toque no link da bio" if plataforma == "instagram" else "clique no link abaixo"
        def _bt(m):
            ini = m.group(1)
            return (chamada.capitalize() if ini[:1].isupper() else chamada) + " e"
        texto = re.sub(
            r"\b([Cc]lique|[Tt]oque|[Cc]lica|[Aa]perte)\s+(?:no|neste|nesse)\s+(?:bot[aã]o|link)(?:\s+abaixo)?\s+e\b",
            _bt, texto)
        # "Clique para conversar..." / "Clique aqui para..." sem dizer onde clicar
        def _para(m):
            return (chamada.capitalize() if m.group(1)[:1].isupper() else chamada) + " para"
        texto = re.sub(r"\b([Cc]lique|[Tt]oque)\s+(?:aqui\s+)?para\b", _para, texto)
        if plataforma == "facebook":  # o Facebook não tem "link da bio" na legenda
            texto = re.sub(r"\b([Tt])oque no link da bio\b",
                           lambda m: "Clique no link abaixo" if m.group(1) == "T" else "clique no link abaixo", texto)
    return formatar_legenda(texto)


def tarja_whatsapp_texto(numero: str) -> str:
    return formatar_telefone(numero)


# --------------------------------------------------------------------------
# TEXTO ESCRITO NA MÍDIA (arte, slides e vídeo): ortografia e PONTUAÇÃO
# --------------------------------------------------------------------------
REGRAS_PONTUACAO = (
    "PADRÃO DE ESCRITA DO TEXTO NA MÍDIA (vale para arte estática, cada slide de carrossel e texto na tela de vídeo):\n"
    "- Frase de pergunta TERMINA com ponto de interrogação (?). Ex.: \"Como funciona o consignado CLT?\", "
    "\"O que é e quem tem direito?\", \"Como as parcelas são descontadas?\". Esquecer o ? é erro grave.\n"
    "- Exclamação termina com (!), no máximo UM sinal; nunca \"!!\" nem \"??\".\n"
    "- Título ou frase curta afirmativa fica SEM ponto final; use o mesmo padrão em todos os slides.\n"
    "- Primeira letra maiúscula (depois de numeração como \"1.\"); nomes próprios e siglas (CLT, WhatsApp) corretos.\n"
    "- Sem espaço antes de , . ; : ! ? e com um espaço depois; sem espaços duplos; sem vírgula/dois-pontos no fim.\n"
    "- Acentuação, crase, concordância e ortografia 100% corretas; sem palavra repetida; parênteses e aspas fechados.\n"
)

CONFERENCIA_TEXTO_MIDIA = (
    "CONFERÊNCIA DE TEXTO (OBRIGATÓRIA, faça antes de dar a nota): LEIA letra por letra TODO texto visível na mídia "
    "(cada slide, a faixa de texto, a tarja do WhatsApp, e no vídeo também texto na tela e a fala) e compare com o "
    "texto esperado informado. Confira ortografia, acentuação, crase e PONTUAÇÃO (?, !, vírgulas, ponto final). "
    "Pergunta sem ponto de interrogação, texto diferente do esperado, palavra cortada ou erro de português = problema "
    "de gravidade ALTA, indicando em 'sugestao'/'correcao' o texto exato já corrigido. Confira também a COERÊNCIA entre "
    "os slides (mesmo padrão de pontuação e de numeração). PADRÃO DA AGÊNCIA: títulos e frases curtas afirmativas ficam "
    "SEM ponto final e perguntas terminam com '?'; NÃO aponte a falta de ponto final em título como erro nem peça para "
    "acrescentá-lo.\n"
)

_PALAVRAS_PERGUNTA = (
    r"o\s+que|o\s+qu[eê]|que\s+tal|quem|qual|quais|por\s+qu[eê]|quanto|quantos|quanta|quantas|"
    r"ser[aá]\s+que|d[aá]\s+para|d[aá]\s+pra|tem\s+como|existe|[eé]\s+poss[ií]vel|"
    r"voc[eê]\s+(?:j[aá]|sabia|sabe|conhece|quer|precisa\s+de|tem\s+d[uú]vidas?)|"
    r"quer|precisa\s+de|tem\s+(?:d[uú]vidas?|alguma)|vale\s+a\s+pena|"
    r"como(?!\s+\w+(?:ar|er|ir|or)\b)"      # "Como funciona..." é pergunta; "Como economizar" (infinitivo) não é
)
_RE_PERGUNTA = re.compile(rf"^(?:{_PALAVRAS_PERGUNTA})\b", re.I)
_RE_NUMERACAO = re.compile(r"^\s*(\d{1,2})\s*[.)\-–]\s+")


def _dividir_frases(texto: str) -> list:
    return [f for f in re.split(r"(?<=[.!?…])\s+", texto.strip()) if f]


def _sem_numeracao(frase: str) -> str:
    return _RE_NUMERACAO.sub("", frase, count=1)


def _eh_pergunta(frase: str) -> bool:
    return bool(_RE_PERGUNTA.match(_sem_numeracao(frase).strip()))


def corrigir_pontuacao_texto_arte(texto: str):
    """Rede de segurança determinística (não depende da IA). Devolve (texto_corrigido, lista_de_mudancas)."""
    original = texto or ""
    t = " ".join(original.split())
    if not t:
        return "", []
    mud = []

    def _reg(msg, antes, depois):
        if antes != depois and msg not in mud:
            mud.append(msg)

    novo = re.sub(r"\s+([,.;:!?…])", r"\1", t)
    _reg("espaço antes da pontuação", t, novo); t = novo
    novo = re.sub(r"([,;:!?])(?=[A-Za-zÀ-ÿ])", r"\1 ", t)
    _reg("espaço depois da pontuação", t, novo); t = novo
    novo = re.sub(r"([a-zà-ÿ]{2,})\.(?=[A-ZÀ-Ý])", r"\1. ", t)
    _reg("espaço depois do ponto", t, novo); t = novo
    novo = re.sub(r"\?{2,}", "?", t); novo = re.sub(r"!{2,}", "!", novo); novo = re.sub(r"\.{4,}", "...", novo)
    _reg("sinal de pontuação repetido", t, novo); t = novo
    novo = re.sub(r"\b(\w{2,})(\s+\1\b)+", r"\1", t, flags=re.I) if not re.search(r"\d", t) else t
    _reg("palavra repetida", t, novo); t = novo

    # primeira letra maiúscula (depois da numeração), só se a primeira palavra for toda minúscula
    m = _RE_NUMERACAO.match(t)
    inicio = m.end() if m else 0
    m2 = re.match(r"([a-zà-ÿ]+)\b", t[inicio:])
    if m2 and len(m2.group(1)) >= 2 and m2.group(1).islower():
        novo = t[:inicio] + t[inicio].upper() + t[inicio + 1:]
        _reg("primeira letra maiúscula", t, novo); t = novo

    # perguntas: cada frase interrogativa termina com "?"
    frases = _dividir_frases(t)
    refeitas = []
    for i, f in enumerate(frases):
        ultima = i == len(frases) - 1
        if _eh_pergunta(f):
            if re.search(r"[.…]$", f) and not f.endswith("..."):
                f = f[:-1] + "?"
                _reg("ponto de interrogação", "a", "b")
            elif not re.search(r"[?!]$", f):
                f = f.rstrip(",;:") + "?"
                _reg("ponto de interrogação", "a", "b")
        elif ultima and re.search(r"[,;:]$", f):
            f = f.rstrip(",;:")
            _reg("pontuação sobrando no fim", "a", "b")
        refeitas.append(f)
    t = " ".join(refeitas)

    # título/frase curta afirmativa: sem ponto final (padrão único em todos os textos)
    if re.search(r"(?<![.])\.$", t) and len(_dividir_frases(t)) == 1:
        t = t[:-1].rstrip()
        _reg("ponto final removido (padrão de título)", "a", "b")
    return t, mud


def analisar_texto_arte(texto: str) -> list:
    """Confere o texto escrito na mídia. Devolve lista de (gravidade, mensagem); vazia = sem problema."""
    t = texto or ""
    prob = []
    if not t.strip():
        return prob
    if re.search(r"\s{2,}", t):
        prob.append(("media", "espaços duplicados"))
    if re.search(r"\s[,.;:!?]", t):
        prob.append(("media", "espaço antes de sinal de pontuação"))
    if re.search(r"\?{2,}|!{2,}", t):
        prob.append(("media", "sinal de pontuação repetido"))
    for f in _dividir_frases(" ".join(t.split())):
        if _eh_pergunta(f) and not f.rstrip().endswith("?"):
            prob.append(("alta", f"pergunta sem ponto de interrogação: “{f}”"))
    s = _sem_numeracao(t.strip())
    if s[:1].isalpha() and s[:1].islower() and re.match(r"[a-zà-ÿ]{2,}\b", s):
        prob.append(("alta", "texto começa com letra minúscula"))
    if re.search(r"[,;:]\s*$", t):
        prob.append(("media", "pontuação sobrando no fim do texto"))
    if re.search(r"\b(\w{2,})\s+\1\b", t, flags=re.I) and not re.search(r"\d", t):
        prob.append(("media", "palavra repetida"))
    if t.count("(") != t.count(")") or t.count('"') % 2 or t.count("“") != t.count("”"):
        prob.append(("media", "parênteses ou aspas sem fechar"))
    letras = [c for c in t if c.isalpha()]
    if len(letras) > 12 and all(c.isupper() for c in letras) and len(t.split()) > 3:
        prob.append(("media", "texto inteiro em MAIÚSCULAS (difícil de ler)"))
    return prob


def analisar_textos_carrossel(textos: list) -> list:
    """Conferência entre slides (padrão único de pontuação e numeração). Devolve (gravidade, mensagem)."""
    prob = []
    cheios = [(i + 1, " ".join((t or "").split())) for i, t in enumerate(textos) if (t or "").strip()]
    com_ponto = [n for n, t in cheios if re.search(r"(?<![.])\.$", t) and len(_dividir_frases(t)) == 1]
    sem_ponto = [n for n, t in cheios if not re.search(r"[.?!…]$", t)]
    if com_ponto and sem_ponto:
        prob.append(("media", f"padrão de pontuação misturado: slide(s) {', '.join(map(str, com_ponto))} terminam com "
                              f"ponto final e os slides {', '.join(map(str, sem_ponto))} não"))
    nums = []
    for n, t in cheios:
        m = _RE_NUMERACAO.match(t)
        if m:
            nums.append((n, int(m.group(1))))
    if len(nums) >= 2:
        valores = [v for _, v in nums]
        if valores != list(range(valores[0], valores[0] + len(valores))):
            prob.append(("media", "numeração dos tópicos fora de ordem: " + ", ".join(map(str, valores))))
    return prob


# --------------------------------------------------------------------------
# CARROSSEL (vários slides) — padrão e supervisor
# --------------------------------------------------------------------------
MIN_SLIDES, MAX_SLIDES, SLIDES_PADRAO = 2, 10, 5
PAPEIS_SLIDE = {"capa": "Capa (gancho)", "conteudo": "Conteúdo", "cta": "Chamada para ação"}


def bloco_redator_slides(n: int = SLIDES_PADRAO) -> str:
    """Instruções para o redator criar a história do carrossel (campo 'slides' do JSON)."""
    return (
        f"PADRÃO DO CARROSSEL ({n} slides — campo 'slides'):\n"
        "- Slide 1 = CAPA: gancho forte que faz deslizar (promessa de valor ou pergunta), no máximo 8 palavras; "
        "cena principal com rosto nos dois terços superiores.\n"
        f"- Slides 2 a {n - 1} = CONTEÚDO: UMA ideia por slide, em ordem lógica (problema → solução → benefício → "
        "como funciona), cada um com texto curto (até 60 caracteres) que faça sentido sozinho.\n"
        f"- Slide {n} = CTA: um único convite para a ação do objetivo da campanha (sem pressão e sem promessas "
        "proibidas).\n"
        "- Todos os slides com o MESMO estilo visual: mesma paleta, mesma iluminação, mesmo tipo de cenário e, se "
        "houver pessoa, a MESMA pessoa; cada slide muda só a cena/gesto. Mesma proporção vertical 4:5 em todos.\n"
        "- Em cada 'prompt_imagem' do slide siga o PADRÃO DE ARTE (30% inferiores limpos, canto inferior direito vazio, "
        "sem texto/logo na imagem). O texto escrito vai em 'texto_na_arte' (o sistema escreve na imagem).\n"
        "- A legenda do post deve COMPLEMENTAR os slides (contexto e convite), sem repetir o texto dos slides.\n"
        "- 'texto_na_arte' é escrito na imagem EXATAMENTE como você entregar: português perfeito e pontuação correta. "
        "Pergunta SEMPRE termina com ?; títulos afirmativos ficam sem ponto final; mesmo padrão em todos os slides; "
        "se numerar os tópicos (1., 2., 3.), numere em sequência.\n"
    )


def bloco_slide_pt(papel: str, n: int, total: int, texto: str, cena: str = "") -> str:
    """Pedido em português, para colar no Gemini do navegador, de UM slide do carrossel."""
    funcao = {"capa": "CAPA do carrossel: chamativa, rosto bem visível, convida a deslizar",
              "cta": "ÚLTIMO slide (chamada para ação): cena acolhedora, pessoa sorrindo convidando a conversar"}.get(
        papel, "slide de CONTEÚDO: uma única ideia, cena simples e clara")
    return (
        f"Slide {n} de {total} — {funcao}. "
        + (f"Cena: {cena} " if cena else "")
        + (f'(O texto "{texto}" será escrito depois pelo sistema; NÃO escreva nada na imagem.) ' if texto else "")
        + "Mantenha o MESMO estilo de todos os slides do carrossel: mesma paleta, mesma iluminação, mesmo tipo de "
          "cenário e a mesma pessoa. " + bloco_imagem_pt(False)
    )


CHECKLIST_CARROSSEL = [
    "Capa: gancho claro nos primeiros 2 segundos, promete valor e convida a deslizar",
    "História: ordem lógica, uma ideia por slide, sem repetição nem salto confuso",
    "Slide final: um único convite claro para a ação do objetivo (sem pressão)",
    "Consistência visual: mesma paleta, iluminação, cenário/pessoa e estilo em todos os slides",
    "Logo: mesmo tamanho e posição em todos os slides, fora do texto e do rosto",
    "Texto em cada slide: curto, legível, bom contraste, sem cobrir rosto; ortografia, acentuação e PONTUAÇÃO corretas em TODOS (pergunta termina com ?) e mesmo padrão entre os slides",
    "Área livre: nada importante sob a faixa de texto, a logo e a tarja; sem sobreposição",
    "Tarja do WhatsApp: só no slide final, legível e com o número correto",
    "Quantidade de slides adequada (5 a 8 é o ideal) e mesma proporção em todos",
    "Legenda complementa os slides (não repete) e combina com a história",
    "Conformidade: sem promessas de aprovação, 'menor taxa', valores/prazos sem ressalva, marca de terceiros",
    "Naturalidade: mãos, rostos e objetos sem deformações em nenhum slide",
]


def montar_prompt_carrossel(empresa: dict, canal: str, tema: str, publico: str, slides: list[dict],
                            legenda: str = "", conceito: str = "", cores: str = "", objetivo: str = "") -> str:
    """Supervisor de marketing do carrossel: vê TODOS os slides (imagens anexadas, na ordem) e avalia cada um."""
    linhas = []
    for s in slides:
        fatos = []
        if s.get("padrao"):
            falhas = [m for ok, m in s["padrao"] if not ok]
            fatos.append("conferência automática: " + ("OK" if not falhas else "FALHOU — " + "; ".join(falhas)))
        if s.get("medidas"):
            fatos.append("medidas: " + ", ".join(f"{k}={v}" for k, v in s["medidas"].items() if v is not None))
        linhas.append(f"- Slide {s['n']} ({PAPEIS_SLIDE.get(s.get('papel') or '', s.get('papel') or 'sem função definida')}): "
                      f"texto escrito na arte = \"{s.get('texto') or '(nenhum)'}\"" + (" | " + " | ".join(fatos) if fatos else ""))
    lista = "\n".join(f"  - {i}" for i in CHECKLIST_CARROSSEL)
    regras = empresa.get("business_rules") or "Sem regras específicas."
    return f"""
Você é o SUPERVISOR DE MARKETING E DIRETOR DE ARTE sênior de uma agência, revisando um CARROSSEL de
{canal} da marca **{empresa['name']}** ANTES da publicação. Os {len(slides)} slides estão anexados, NA ORDEM
(a imagem de cada slide vem logo depois da etiqueta "SLIDE n"). Seja rigoroso: ache problemas reais, não elogie por educação.

Tema: "{tema}". Público: "{publico or 'principal da marca'}". Conceito visual planejado: {conceito or '(não informado)'}.
Paleta da marca: {cores or 'não informada'}. Objetivo: {objetivo or 'marca e engajamento'}.
Regras da marca (violar = gravidade alta): {regras}

LEGENDA que acompanhará o post:
\"\"\"{legenda or '(sem legenda)'}\"\"\"

SLIDES (fatos medidos pelo sistema — confie neles para tamanhos/posições e use os olhos para o resto):
{chr(10).join(linhas)}

REGRA DA LOGO: a logo é OBRIGATÓRIA e já fica na margem padrão do sistema (cerca de 6% da borda), feita para não
conflitar com a interface das redes. NUNCA sugira remover, diminuir ou afastar a logo por estar "perto da borda"; só
aponte problema se ela cobrir texto ou rosto, estiver ilegível ou ausente.

{CONFERENCIA_TEXTO_MIDIA}
Avalie (ok true/false + comentário curto em português):
{lista}

Para CADA slide devolva: nota (0-10), "ok" (true se pode publicar assim), "funcao" (o papel real que ele cumpre na
história), "comentario" curto e "correcao" (o que mudar; vazio se nada). Em "problemas" use "legenda" = "Slide N" ou
"Carrossel" (problemas gerais de história/consistência).

Se algum slide precisar ser refeito, escreva em "prompt_melhorado" um PEDIDO COMPLETO em português, pronto para colar no
gerador de imagem, para refazer SÓ os slides com problema (cite o número do slide), mantendo o mesmo estilo dos demais,
30% inferiores e canto inferior direito livres, e proibindo texto/logo dentro da imagem. Se estiver tudo bom, deixe vazio.

Responda SOMENTE com JSON válido neste formato exato:
{{
  "nota": 0 a 10,
  "veredito": "aprovado" ou "ajustar" ou "reprovado",
  "resumo": "2 frases em português sobre a história e a qualidade do carrossel",
  "checklist": [{{"item": "nome", "ok": true, "comentario": "curto"}}],
  "slides": [{{"n": 1, "nota": 0 a 10, "ok": true, "funcao": "capa", "comentario": "curto", "correcao": ""}}],
  "problemas": [{{"gravidade": "alta" ou "media" ou "baixa", "legenda": "Slide 2 ou Carrossel",
                  "descricao": "o que está errado", "sugestao": "como corrigir"}}],
  "prompt_melhorado": "texto ou vazio"
}}
Regra do veredito: "reprovado" se houver gravidade alta (ex.: logo sobre o texto, texto ilegível, rosto deformado,
slide sem sentido, promessa proibida, slides de estilos totalmente diferentes); "ajustar" se só média/baixa; "aprovado"
se nada relevante.
""".strip()
