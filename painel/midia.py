# -*- coding: utf-8 -*-
"""
midia.py — texto e logo sobre imagens e vídeos (sem API, tudo no computador).

* criar_sobreposicao(): desenha uma camada transparente com a faixa de texto e a logo.
* aplicar_texto(): coloca a faixa de texto numa imagem.
* estampar_video(): queima texto e logo no vídeo usando o ffmpeg (instalado junto com o pacote
  imageio-ffmpeg; não é preciso instalar nada à mão).
"""
import importlib
import re
import shutil
import subprocess
import sys
import tempfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONTES = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
COR_FAIXA = (11, 31, 74, 220)      # azul-marinho translúcido
COR_TEXTO = (255, 255, 255, 255)
MAX_LINHAS = 3


def _fonte(tamanho: int):
    for caminho in FONTES:
        try:
            return ImageFont.truetype(caminho, tamanho)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=tamanho)
    except TypeError:
        return ImageFont.load_default()


def _quebrar(draw: ImageDraw.ImageDraw, texto: str, fonte, largura_max: int) -> list:
    linhas, atual = [], ""
    for palavra in texto.split():
        teste = f"{atual} {palavra}".strip()
        if draw.textlength(teste, font=fonte) <= largura_max or not atual:
            atual = teste
        else:
            linhas.append(atual)
            atual = palavra
    if atual:
        linhas.append(atual)
    return linhas


# --------------------------------------------------------------------------
# Padrão visual (um só lugar para texto, logo e tarja)
# --------------------------------------------------------------------------
PADRAO = {
    "logo_largura": 0.16,        # logo ocupa ~16% da largura (aceito: 12% a 20%)
    "logo_altura_max": 0.14,     # e no máximo 14% da altura (logos quadradas/altas)
    "margem_logo": 0.06,         # distância da borda
    "margem_lateral": 0.06,      # margem lateral da faixa de texto
    "fonte_texto": 0.060,        # tamanho inicial do texto (% da largura)
    "fonte_texto_min": 0.042,    # nunca menor que isso
    "faixa_topo_min": 0.52,      # a faixa de texto fica sempre na metade de baixo
    "faixa_altura_max": 0.24,
    "max_linhas": MAX_LINHAS,
}
COR_TARJA = (18, 140, 70, 255)   # verde WhatsApp (escuro, com bom contraste)
ICONE_WHATSAPP = Path(__file__).resolve().parent / "logos" / "whatsapp.png"   # ícone oficial, colocado pelo usuário


def carregar_icone_whatsapp() -> bytes | None:
    """Ícone oficial do WhatsApp (PNG) salvo em logos/whatsapp.png. Se não existir, a tarja usa só o texto."""
    try:
        return ICONE_WHATSAPP.read_bytes() if ICONE_WHATSAPP.exists() else None
    except Exception:
        return None


def _medidor() -> ImageDraw.ImageDraw:
    return ImageDraw.Draw(Image.new("RGBA", (4, 4)))


def _cruza(a, b) -> bool:
    return bool(a and b and a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])


def planejar_layout(largura: int, altura: int, texto: str = "", logo_tam=None, posicao: str = "bottom_right",
                    tarja: str = "", escala_logo: float | None = None, icone_tam=None,
                    reserva_rodape: float = 0.0) -> dict:
    """
    (reserva_rodape = fração da altura deixada livre embaixo, para a interface dos Reels.)
    Calcula onde ficam a logo, a tarja do WhatsApp e a faixa de texto, SEM sobreposição:
    a logo e a tarja ocupam o rodapé (lados opostos) e a faixa de texto fica acima deles.
    Devolve retângulos (x0, y0, x1, y1) em pixels.
    """
    W, H = int(largura), int(altura)
    B = min(W, H)  # referência de tamanho: o menor lado (assim paisagem não fica com letras enormes)
    P = PADRAO
    medir = _medidor()
    plano = {"largura": W, "altura": H, "logo": None, "tarja": None, "faixa": None, "linhas": [], "fonte": 0,
             "fonte_tarja": 0, "tarja_texto": "", "texto": " ".join((texto or "").split()), "avisos": [],
             "posicao": posicao}
    m = int(W * P["margem_lateral"])
    mb = int(W * P["margem_logo"])
    mb_v = mb + int(H * reserva_rodape)  # margem de baixo (maior nos Reels)
    gap = int(H * 0.02)

    # 1) logo
    logo_embaixo = False
    lw = 0
    if logo_tam and logo_tam[0] > 0 and logo_tam[1] > 0:
        lw = int(W * (escala_logo or P["logo_largura"]))
        lh = int(logo_tam[1] * lw / float(logo_tam[0]))
        if lh > H * P["logo_altura_max"]:
            lh = int(H * P["logo_altura_max"])
            lw = int(logo_tam[0] * lh / float(logo_tam[1]))
        x = W - lw - mb - (int(W * 0.06) if reserva_rodape else 0) if "right" in posicao else mb
        y = H - lh - mb_v if posicao.startswith("bottom") else mb
        plano["logo"] = (x, y, x + lw, y + lh)
        logo_embaixo = posicao.startswith("bottom")

    # 2) selo verde do WhatsApp (pequeno, no canto do rodapé, do lado oposto da logo)
    tarja = " ".join((tarja or "").split())
    if tarja:
        com_icone = bool(icone_tam and icone_tam[0] > 0 and icone_tam[1] > 0)
        txt = tarja if com_icone else f"WhatsApp  {tarja}"
        if logo_embaixo:
            if "right" in posicao:
                x0, x1 = m, plano["logo"][0] - int(W * 0.03)
            else:
                x0, x1 = plano["logo"][2] + int(W * 0.03), W - m
        else:
            x0, x1 = m, W - m
        tam = int(B * 0.040)
        minimo = int(B * 0.026)
        while True:
            f = _fonte(tam)
            pad = int(tam * 0.55)
            th = int(tam * 1.7)
            iw = int(th * 0.72) if com_icone else 0
            larg = int(medir.textlength(txt, font=f)) + 2 * pad + (iw + int(tam * 0.4) if com_icone else 0)
            if larg <= (x1 - x0) or tam <= minimo:
                break
            tam = int(tam * 0.92)
        if larg > (x1 - x0):
            plano["avisos"].append("O selo do WhatsApp não coube ao lado da logo e foi omitido.")
        else:
            if logo_embaixo:
                cy = (plano["logo"][1] + plano["logo"][3]) // 2
                y0 = cy - th // 2
            else:
                y0 = H - mb_v - th
            xa = x0 if (not logo_embaixo or "right" in posicao) else x1 - larg
            plano["tarja"] = (xa, y0, xa + larg, y0 + th)
            plano["fonte_tarja"], plano["tarja_texto"] = tam, txt
            if com_icone:
                ih = int(th * 0.72)
                iw2 = int(icone_tam[0] * ih / float(icone_tam[1]))
                ix = xa + int(pad * 0.7)
                plano["icone"] = (ix, y0 + (th - ih) // 2, ix + iw2, y0 + (th - ih) // 2 + ih)

    # 3) faixa de texto, sempre acima do que ocupa o rodapé
    if plano["texto"]:
        topos = [r[1] for r in (plano["tarja"], plano["logo"] if logo_embaixo else None) if r]
        y_fim = (min(topos) - gap) if topos else int(H * (0.90 - reserva_rodape))
        pad = int(B * 0.035)
        tam = int(B * P["fonte_texto"])
        minimo = int(B * P["fonte_texto_min"])
        while True:
            f = _fonte(tam)
            linhas = _quebrar(medir, plano["texto"], f, W - 2 * m - 2 * pad)
            ent = int(tam * 1.25)
            alt = len(linhas) * ent + 2 * pad
            if (len(linhas) <= P["max_linhas"] and alt <= H * P["faixa_altura_max"]) or tam <= minimo:
                break
            tam = int(tam * 0.92)
        plano["faixa"] = (m, y_fim - alt, W - m, y_fim)
        plano["linhas"], plano["fonte"], plano["entrelinha"], plano["pad"] = linhas, tam, ent, pad

    rects = [plano[k] for k in ("logo", "tarja", "faixa") if plano.get(k)]
    plano["sobreposicao"] = any(_cruza(a, b) for i, a in enumerate(rects) for b in rects[i + 1:])
    return plano


def resumo_layout(plano: dict) -> dict:
    W, H = plano["largura"], plano["altura"]
    B = min(W, H)
    lg, fx = plano.get("logo"), plano.get("faixa")
    return {
        "logo_largura_pct": round(100 * (lg[2] - lg[0]) / W, 1) if lg else None,
        "logo_altura_pct": round(100 * (lg[3] - lg[1]) / H, 1) if lg else None,
        "faixa_altura_pct": round(100 * (fx[3] - fx[1]) / H, 1) if fx else None,
        "faixa_topo_pct": round(100 * fx[1] / H, 1) if fx else None,
        "fonte_pct": round(100 * plano["fonte"] / B, 1) if fx else None,
        "linhas": len(plano.get("linhas") or []),
        "tarja_whatsapp": bool(plano.get("tarja")),
        "sobreposicao": bool(plano.get("sobreposicao")),
        "posicao_logo": plano.get("posicao"),
    }


def checar_padrao(plano: dict, com_logo: bool = True) -> list:
    """Conferência automática (sem IA) do padrão visual. Devolve [(ok, mensagem)]."""
    W, H, P = plano["largura"], plano["altura"], PADRAO
    r = resumo_layout(plano)
    out = []
    if com_logo:
        lw, lh = r["logo_largura_pct"], r["logo_altura_pct"]
        ok_logo = plano["logo"] is not None and (11.5 <= lw <= 20.5 or 9 <= lh <= 14.5)
        out.append((ok_logo, f"Logo com tamanho padrão (12% a 20% da largura): {lw if lw is not None else 'ausente'}%"))
    out.append((not plano["sobreposicao"], "Logo, tarja e texto não se sobrepõem"))
    if plano.get("faixa"):
        out.append((r["fonte_pct"] >= P["fonte_texto_min"] * 100 - 0.2,
                    f"Texto legível (fonte {r['fonte_pct']}% do lado menor, mínimo {P['fonte_texto_min'] * 100:.1f}%)"))
        out.append((r["linhas"] <= P["max_linhas"], f"Texto com até {P['max_linhas']} linhas ({r['linhas']})"))
        out.append((r["faixa_topo_pct"] >= P["faixa_topo_min"] * 100 - 0.5,
                    "Faixa de texto na metade de baixo (não cobre o assunto principal)"))
    dentro = all(rc[0] >= 0 and rc[1] >= 0 and rc[2] <= W and rc[3] <= H
                 for rc in (plano[k] for k in ("logo", "tarja", "faixa")) if rc)
    out.append((dentro, "Todos os elementos dentro das margens da imagem"))
    return out


def renderizar(largura: int, altura: int, texto: str = "", logo_bytes: bytes | None = None,
               posicao: str = "bottom_right", tarja: str = "", escala_logo: float | None = None,
               icone_bytes: bytes | None = None, reserva_rodape: float = 0.0):
    """Camada RGBA transparente (faixa + tarja + logo) e o plano usado. Sem sobreposição."""
    logo = None
    if logo_bytes:
        try:
            logo = Image.open(BytesIO(logo_bytes)).convert("RGBA")
        except Exception:
            logo = None
    icone = None
    if tarja:
        try:
            dados_ic = icone_bytes if icone_bytes is not None else carregar_icone_whatsapp()
            icone = Image.open(BytesIO(dados_ic)).convert("RGBA") if dados_ic else None
        except Exception:
            icone = None
    plano = planejar_layout(largura, altura, texto, logo.size if logo else None, posicao, tarja, escala_logo,
                            icone.size if icone else None, reserva_rodape)
    camada = Image.new("RGBA", (largura, altura), (0, 0, 0, 0))
    draw = ImageDraw.Draw(camada)
    if plano["faixa"]:
        x0, y0, x1, y1 = plano["faixa"]
        draw.rounded_rectangle(plano["faixa"], radius=int(largura * 0.03), fill=COR_FAIXA)
        f = _fonte(plano["fonte"])
        y = y0 + plano["pad"]
        for linha in plano["linhas"]:
            w = draw.textlength(linha, font=f)
            draw.text(((largura - w) / 2, y), linha, font=f, fill=COR_TEXTO)
            y += plano["entrelinha"]
    if plano["tarja"]:
        x0, y0, x1, y1 = plano["tarja"]
        draw.rounded_rectangle(plano["tarja"], radius=(y1 - y0) // 2, fill=COR_TARJA)
        f = _fonte(plano["fonte_tarja"])
        w = draw.textlength(plano["tarja_texto"], font=f)
        if icone is not None and plano.get("icone"):
            ix0, iy0, ix1, iy1 = plano["icone"]
            pq = icone.resize((ix1 - ix0, iy1 - iy0), Image.Resampling.LANCZOS)
            camada.paste(pq, (ix0, iy0), mask=pq)
            tx = ix1 + int(plano["fonte_tarja"] * 0.4)
        else:
            tx = x0 + (x1 - x0 - w) / 2
        draw.text((tx, y0 + (y1 - y0) / 2), plano["tarja_texto"], font=f, fill=COR_TEXTO, anchor="lm")
    if logo is not None and plano["logo"]:
        x0, y0, x1, y1 = plano["logo"]
        pequeno = logo.resize((x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
        camada.paste(pequeno, (x0, y0), mask=pequeno)
    return camada, plano


def criar_sobreposicao(largura: int, altura: int, texto: str = "", logo_bytes: bytes | None = None,
                       posicao: str = "bottom_right", escala_logo: float | None = None, tarja: str = "") -> Image.Image:
    return renderizar(largura, altura, texto, logo_bytes, posicao, tarja, escala_logo)[0]


def compor(imagem: Image.Image, texto: str = "", logo_bytes: bytes | None = None, posicao: str = "bottom_right",
           tarja: str = ""):
    """Estampa faixa de texto, tarja do WhatsApp e logo na imagem (sem sobreposição). Devolve (imagem RGB, plano)."""
    base = imagem.convert("RGBA")
    camada, plano = renderizar(base.size[0], base.size[1], texto, logo_bytes, posicao, tarja)
    return Image.alpha_composite(base, camada).convert("RGB"), plano


def aplicar_texto(imagem: Image.Image, texto: str) -> Image.Image:
    """Compatibilidade: só a faixa de texto."""
    return compor(imagem, texto)[0] if (texto or "").strip() else imagem


# --------------------------------------------------------------------------
# Vídeo
# --------------------------------------------------------------------------
def _tentar_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def caminho_ffmpeg() -> str | None:
    """Acha o ffmpeg; se faltar, instala o pacote imageio-ffmpeg sozinho (uma vez) e tenta de novo."""
    achou = _tentar_ffmpeg()
    if achou:
        return achou
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "imageio-ffmpeg"],
                       capture_output=True, text=True, timeout=240)
        importlib.invalidate_caches()
    except Exception:
        return None
    return _tentar_ffmpeg()


def _tamanho_video(ffmpeg: str, arquivo: Path):
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(arquivo)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    m = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})[\s,\[]", r.stderr or "")
    if not m:
        raise RuntimeError("Não consegui ler o tamanho do vídeo.")
    return int(m.group(1)), int(m.group(2))


# Parte de baixo (fração da altura) que a interface dos Reels costuma cobrir.
RESERVA_RODAPE_REELS = 0.20


def estampar_video(video_bytes: bytes, texto: str = "", logo_bytes: bytes | None = None,
                   posicao: str = "bottom_right", limite_segundos: int = 300, tarja: str = "",
                   info: dict | None = None) -> bytes:
    """Devolve um MP4 com o texto e a logo queimados no vídeo. Levanta RuntimeError se algo falhar."""
    if not (texto or "").strip() and not logo_bytes and not (tarja or "").strip():
        return video_bytes
    ffmpeg = caminho_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("O programa de vídeo (ffmpeg) não está instalado. Rode o INICIAR.bat de novo para "
                           "instalar o pacote imageio-ffmpeg.")
    with tempfile.TemporaryDirectory() as pasta:
        pasta = Path(pasta)
        entrada, saida, png = pasta / "entrada.mp4", pasta / "saida.mp4", pasta / "camada.png"
        entrada.write_bytes(video_bytes)
        larg, alt = _tamanho_video(ffmpeg, entrada)
        larg, alt = larg - larg % 2, alt - alt % 2
        # Reels/vídeo vertical: o Instagram e o Facebook cobrem o rodapé com nome, legenda e botões.
        reserva = RESERVA_RODAPE_REELS if alt >= larg * 1.5 else 0.0
        camada, plano = renderizar(larg, alt, texto, logo_bytes, posicao, tarja, reserva_rodape=reserva)
        camada.save(png)
        if info is not None:
            info["plano"] = plano
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(entrada), "-i", str(png),
               "-filter_complex", f"[0:v]scale={larg}:{alt}[v];[v][1:v]overlay=0:0:format=auto,format=yuv420p[o]",
               "-map", "[o]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-c:a", "copy", "-movflags", "+faststart", str(saida)]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=limite_segundos)
        if r.returncode != 0 or not saida.exists():
            raise RuntimeError("Falha ao estampar o vídeo: " + (r.stderr or "erro desconhecido")[-300:])
        return saida.read_bytes()
