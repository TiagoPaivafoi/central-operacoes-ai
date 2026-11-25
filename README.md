# Central de Operações AI

Plataforma de marketing e atendimento com inteligência artificial, criada para a **Cred Finance Soluções Financeiras** (intermediação de crédito) e desenhada para atender várias empresas ao mesmo tempo (multi-tenant).

Ela junta, num só sistema:

- **Painel de campanhas** (Streamlit): recebe o briefing da empresa do jeito que o cliente mandar, transforma em briefing profissional e gera posts, carrosséis e artes com a identidade visual de cada marca. Também planeja e acompanha campanhas na Meta (Facebook e Instagram).
- **Agente de WhatsApp 24 horas** (Supabase Edge Functions + Gemini): atende pelo WhatsApp Business Cloud API, entende texto, áudio, imagem e PDF, mostra um menu de produtos, simula e cadastra propostas direto nas APIs dos parceiros e só passa para a equipe quando precisa.
- **Base de conhecimento com busca semântica** (RAG com pgvector): o agente responde com base nos documentos aprovados de cada empresa.

> Projeto em produção desde novembro de 2026. Código publicado para portfólio. Veja a [licença](LICENSE).

---

## Arquitetura

```
 Cliente no WhatsApp
        │
        ▼
 Meta WhatsApp Cloud API ──► whatsapp-webhook (Edge Function)
                                   │ grava a mensagem e enfileira
                                   ▼
                           whatsapp-agente (Edge Function, Deno/TypeScript)
                     ┌─────────────┼───────────────────────────┐
                     ▼             ▼                           ▼
              Gemini (IA)   Base de conhecimento        APIs de parceiros
              texto, áudio,  (pgvector, RAG)            Novo Saque (FGTS)
              imagem, PDF                               Facta (consignado CLT/INSS)
                     │                                          │
                     └──────────────► Supabase (PostgreSQL) ◄───┘
                                   tenants, contatos, conversas,
                                   operações, orçamento de IA, LGPD
                                          ▲
                                          │
 Equipe / agência ──► Painel Streamlit (Python) ──► Meta Graph API (posts e anúncios)
```

## Destaques técnicos

- **Multi-tenant de verdade:** cada empresa tem seus dados, mídias, base de conhecimento e credenciais separados por `tenant_id`, com Row Level Security no PostgreSQL.
- **Integrações de ponta a ponta:** simulação, proposta e link de assinatura enviados pelo próprio robô (Novo Saque para FGTS; Facta para consignado CLT, em homologação), com webhook de status e consulta de reserva a cada minuto (`pg_cron` + `pg_net`).
- **Robustez:** novas tentativas automáticas quando o parceiro cai (502/503/504), travas de concorrência por conversa, limites diários de respostas e frase de emergência quando a IA falha.
- **Controle de custo:** teto mensal de gasto com IA, com aviso no Telegram em 80% e 100%.
- **Proteção contra robôs:** identifica quando do outro lado é outro robô de WhatsApp, para sem gastar IA e passa para a equipe.
- **Privacidade (LGPD):** CPF nunca vai para os logs, dados pessoais só ficam guardados até a formalização e há limpeza automática diária.
- **Multimodal:** transcreve áudios, lê fotos de documentos e valida PDFs antes de mandar para a IA.

## Estrutura do repositório

| Pasta | O que tem |
|---|---|
| `painel/` | Painel Streamlit: geração de campanhas, padrão visual das marcas, publicação na Meta, tráfego pago, base de conhecimento e scripts de teste |
| `supabase/functions/whatsapp-agente/` | O agente de WhatsApp (versão em produção), com os módulos de menu, campanhas, áudio, imagem, Novo Saque e Facta |
| `supabase/functions/whatsapp-webhook/` | Recebe as mensagens da Meta e valida a assinatura |
| `supabase/functions/novosaque-webhook/` | Recebe os avisos de mudança de etapa da Novo Saque |
| `supabase/migrations/` | Estrutura do banco: tabelas, funções, segurança (RLS), base de conhecimento e agente |

## Stack

Python · Streamlit · TypeScript (Deno) · Supabase (PostgreSQL, Edge Functions, Storage, Vault, pgvector, pg_cron) · Google Gemini · Meta Graph API · WhatsApp Business Cloud API · Telegram Bot API

## Escopo da publicação e instalação

Este é um recorte público para apresentação e portfólio. Componentes e configurações operacionais foram omitidos intencionalmente para preservar a propriedade intelectual e informações internas. O conteúdo publicado não constitui uma distribuição completa, pronta para implantação.

As migrações disponíveis não abrangem todos os módulos referenciados pelo agente. Aplicá-las, por si só, não reproduz o ambiente de produção. Integrações com parceiros dependem de credenciamento próprio, configurações privadas e componentes adicionais.

Os arquivos `.streamlit/secrets.toml.example` e `empresas.example.json` são modelos de configuração, sem autorização para acessar o ambiente da Cred Finance.

Para uma implantação autorizada, é necessário obter a distribuição completa, revisar as dependências e configurar credenciais próprias em um ambiente isolado. O painel administrativo atual não deve ser oferecido diretamente a empresas clientes como portal de autosserviço: esse uso exige autenticação individual e autorização por empresa.

Consulte as [orientações de segurança](SECURITY.md) e a [licença](LICENSE). A licença define as condições de uso; a publicação pública permite visualizar e copiar tecnicamente os arquivos disponibilizados.

## Autor

**Tiago Paiva**: idealização, regras de negócio e gestão do projeto, com desenvolvimento assistido por IA.
