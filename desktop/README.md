# Edvid Desktop

Aplicativo Electron do Edvid. Esta pasta pertence ao produto; projetos de
edicao continuam em pastas escolhidas pelo usuario e nunca sao armazenados
dentro do aplicativo.

## Desenvolvimento

```bash
npm install
npm start
```

## Validacao e empacotamento

```bash
npm run typecheck
npm run package
```

O Node 26.7.0 continua sendo o runtime planejado para o motor do Edvid. Os
comandos de empacotamento executam o Electron Forge com Node 22.23.2 porque a
linha 7 do Forge tem um bug conhecido ao finalizar pacotes com Node 24/26. Isso
afeta apenas a ferramenta de build, nao o Node que sera entregue ao usuario.

O primeiro marco usa os executaveis encontrados no ambiente de desenvolvimento
apenas para validar o fluxo. Os runtimes empacotados por plataforma entrarao em
`resources/runtimes/<plataforma>-<arquitetura>/` e serao resolvidos pelo
aplicativo sem depender do `PATH` do computador.

O comando de pacote prepara automaticamente a distribuicao oficial do Node,
confere o SHA-256 publicado em `nodejs.org` e a inclui como sidecar. Em modo de
desenvolvimento ainda existe fallback explicito para o `PATH`; em um aplicativo
empacotado esse fallback e desativado.

O renderer importa `assets/preview/app.css` e os logos de `assets/preview/`.
Essa pasta e a fonte do design system existente do Edvid; o desktop mantem em
`src/styles.css` somente regras especificas do seu layout.
