# -*- coding: utf-8 -*-
"""
testar_base.py — prova que a base de conhecimento responde certo (aberto pelo BASE_CONHECIMENTO.bat).

Faz perguntas de clientes de verdade e confere se o trecho do produto certo aparece entre os
3 primeiros resultados. Também testa a segurança:
  * trechos internos NÃO aparecem na busca padrão;
  * uma empresa nunca recebe trechos de outra.
Grava o resultado em base_conhecimento_relatorio.txt (sem nenhuma chave).
Custo: 13 perguntas ao Gemini, menos de R$ 0,01.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime
from pathlib import Path

import base_conhecimento as bc
from carregar_base import carregar_segredos, clientes, limpo, log

PASTA = Path(__file__).resolve().parent
RELATORIO = PASTA / "base_conhecimento_relatorio.txt"

# (pergunta do cliente, partes do título da seção esperada — basta uma)
PERGUNTAS_CRED_FINANCE = [
    ("Quais documentos preciso para pegar empréstimo dando meu apartamento como garantia?", ["home equity"]),
    ("Sou aposentado do INSS, vocês fazem empréstimo descontado no benefício?", ["consignado inss"]),
    ("Estou com o nome sujo, consigo antecipar meu saque-aniversário?", ["fgts"]),
    ("Quero pagar menos na conta de luz sem instalar placa solar", ["geracao distribuida"]),
    ("Minha empresa pagou imposto a mais, dá para recuperar esse dinheiro?", ["recuperacao tributaria"]),
    ("Dá para usar minha moto como garantia de um empréstimo?", ["garantia de veiculo"]),
    ("Trabalho de carteira assinada e estou cheio de dívida no cartão", ["consignado privado clt"]),
    ("Posso perder minha casa se fizer esse empréstimo?", ["home equity"]),
    ("Vocês pedem depósito antecipado para liberar o dinheiro?",
     ["diferenciais", "regras de comunicacao", "consignado inss"]),
    ("Não tenho margem no consignado, tenho limite no cartão de crédito. Tem alguma opção?",
     ["saque no limite"]),
]
PERGUNTA_INTERNA = "Qual é a estratégia de porta de entrada e quais produtos dão mais receita?"
MINIMO_ACERTOS = 9   # de 10


def _secao(linha: dict) -> str:
    return bc.sem_acento((linha.get("metadata") or {}).get("secao", "") + " " +
                         (linha.get("content") or "").split("\n", 1)[0])


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass
    linhas_rel: list[str] = [f"TESTE DA BASE DE CONHECIMENTO — {datetime.now():%d/%m/%Y %H:%M}", ""]

    def out(t: str = "") -> None:
        t = limpo(t)
        print(t, flush=True)
        linhas_rel.append(t)

    print("=" * 64)
    print("  BASE DE CONHECIMENTO — teste de busca")
    print("=" * 64)
    try:
        seg = carregar_segredos()
        sb, gemini = clientes(seg)
        empresa = bc.empresa_por_slug(sb, "cred-finance")
        if not empresa:
            raise bc.ErroBase("Empresa cred-finance não encontrada no Supabase.")
        tid = empresa["id"]
        docs = bc.documentos_no_banco(sb, tid)
        out(f"Empresa: {empresa['name']} — {len(docs)} documento(s) na base:")
        for k, d in sorted(docs.items()):
            out(f"  - {k.removeprefix('arquivo:')} ({d.get('visibility')}, {d.get('status')})")
        out()

        acertos = 0
        out("1) PERGUNTAS DE CLIENTES (o produto certo precisa estar entre os 3 primeiros)")
        for pergunta, esperado in PERGUNTAS_CRED_FINANCE:
            res = bc.buscar(sb, gemini, tid, pergunta, quantidade=3)
            secoes = [_secao(r) for r in res]
            ok = any(any(e in s for e in esperado) for s in secoes)
            acertos += ok
            out(f"  [{'OK' if ok else 'ERRO'}] {pergunta}")
            for r in res[:3]:
                titulo = (r.get("metadata") or {}).get("secao", "?")
                out(f"         -> {titulo}  (semelhança {float(r.get('similarity') or 0):.2f})")
        out(f"  Acertos: {acertos} de {len(PERGUNTAS_CRED_FINANCE)} (mínimo {MINIMO_ACERTOS})")
        out()

        out("2) SEGURANÇA")
        vetor_int = bc.vetor_da_pergunta(gemini, PERGUNTA_INTERNA)
        padrao = bc.buscar(sb, gemini, tid, PERGUNTA_INTERNA, quantidade=10, vetor=vetor_int)
        bruto = sb.rpc("match_knowledge", {
            "p_tenant_id": tid, "p_query_embedding": bc._vetor_texto(vetor_int),
            "p_query_text": PERGUNTA_INTERNA, "p_match_count": 10}).execute().data or []
        s1 = not any(r.get("visibility") == "interno" for r in bruto) and \
            not any(r.get("visibility") == "interno" for r in padrao)
        out(f"  [{'OK' if s1 else 'ERRO'}] Busca padrão não devolve trechos internos")

        tem_interno = any(d.get("visibility") == "interno" for d in docs.values())
        com_flag = bc.buscar(sb, gemini, tid, PERGUNTA_INTERNA, quantidade=5,
                             incluir_interno=True, vetor=vetor_int)
        s2 = (not tem_interno) or any(r.get("visibility") == "interno" for r in com_flag)
        out(f"  [{'OK' if s2 else 'ERRO'}] Com permissão do agente, a orientação interna é encontrada")

        outra = bc.buscar(sb, gemini, str(uuid.uuid4()), PERGUNTAS_CRED_FINANCE[0][0],
                          quantidade=5, incluir_interno=True, vetor=vetor_int)
        s3 = len(outra) == 0
        out(f"  [{'OK' if s3 else 'ERRO'}] Outra empresa não recebe nada da Cred Finance")

        try:
            bc.buscar(sb, gemini, "", "teste", vetor=vetor_int)
            s4 = False
        except bc.ErroBase:
            s4 = True
        out(f"  [{'OK' if s4 else 'ERRO'}] Busca sem empresa é recusada")
        out()

        aprovado = acertos >= MINIMO_ACERTOS and s1 and s2 and s3 and s4
        out("RESULTADO: " + ("TESTE APROVADO" if aprovado else "TESTE REPROVADO — envie este relatório ao Claude"))
        out(f"Custo deste teste: cerca de R$ {bc.custo_brl(13 * 40):.4f}")
    except bc.ErroBase as e:
        out(f"PAROU: {e}")
        aprovado = False
    except Exception as e:  # noqa: BLE001
        out(f"PAROU: {type(e).__name__}: {str(e)[:300]}")
        aprovado = False

    try:
        RELATORIO.write_text("\n".join(linhas_rel) + "\n", encoding="utf-8")
        print(f"\nRelatório salvo em: {RELATORIO.name}")
    except Exception:  # noqa: BLE001
        pass
    log(f"Teste da base: {'APROVADO' if aprovado else 'REPROVADO'}", tela=False)
    return 0 if aprovado else 1


if __name__ == "__main__":
    sys.exit(main())
