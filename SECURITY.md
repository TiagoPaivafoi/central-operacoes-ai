# Segurança e limites da versão pública

## Escopo

Este repositório contém uma seleção de código para portfólio. Não é a distribuição operacional completa. A ausência de componentes não substitui controles de segurança e não impede a cópia dos arquivos já públicos.

## Conteúdo que deve permanecer privado

- Chaves de API, tokens, senhas, arquivos de ambiente e configurações preenchidas.
- Dados de clientes, documentos, propostas, conversas e bases comerciais internas.
- Logs, backups e exportações do ambiente operacional.
- Configurações de parceiros e componentes cuja publicação não tenha sido autorizada.

Exemplos devem usar dados fictícios e marcadores. Antes de publicar alterações, revise o conteúdo e o histórico que será enviado.

## Implantação autorizada

Use credenciais próprias e um ambiente isolado. Não conecte uma cópia de demonstração ao banco ou às contas de produção.

O painel com senha única destina-se à administração centralizada. A chave administrativa do Supabase pode contornar RLS; filtros de tenant no código não substituem autenticação e autorização de cada usuário. Acesso direto por clientes exige desenho e validação específicos de isolamento por empresa.

A execução integral depende de componentes e migrações adicionais não publicados. Não improvise estruturas de produção a partir deste recorte.

## Suspeita de exposição

Não publique credenciais, documentos ou dados pessoais em issues, comentários ou pull requests. Use um canal privado já acordado com o responsável pelo projeto.

Se uma credencial for exposta, revogue ou rotacione a credencial e confira seu uso. Apagar o arquivo atual não remove cópias anteriores nem o histórico Git.

## Limite desta documentação

Estas orientações não representam auditoria de segurança, certificação ou garantia de conformidade. A disponibilidade e a configuração de varredura de segredos devem ser verificadas nas configurações do repositório.
