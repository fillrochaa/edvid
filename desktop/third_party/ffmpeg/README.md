# FFmpeg no Edvid Desktop

O Edvid fixa o FFmpeg e o FFprobe na versao `8.1.2`. O codigo-fonte oficial,
a assinatura do release e a chave publica sao baixados por:

```sh
cd desktop
npm run fetch:ffmpeg
```

O script usa um chaveiro GPG temporario, confere o fingerprint oficial
`FCF986EA15E6E293A5644F10B4322F04D67658D8` e so extrai o fonte depois que a
assinatura e validada. Downloads, fontes extraidos e binarios ficam em
`desktop/.runtime-cache/` ou `desktop/resources/runtimes/` e nao sao versionados.

## Decisao de licenca antes do build

O pipeline atual do Edvid chama o encoder `libx264`. No FFmpeg, habilitar esse
encoder tambem habilita partes GPL, portanto o binario resultante precisa ser
distribuido sob os termos da GPL. Esse perfil preserva o comportamento atual,
mas exige um processo de distribuicao e conformidade compativel com a GPL.

Um perfil LGPL pode usar os encoders nativos `h264_videotoolbox` no macOS e
Media Foundation no Windows. Essa alternativa exige adaptar e testar os
parametros de renderizacao do Edvid, pois `preset` e `crf` do `libx264` nao se
aplicam diretamente aos encoders de hardware.

O perfil inicial aprovado para producao e `GPL-2.0-or-later + libx264`. No Mac
Apple Silicon, compile e valide o sidecar com:

```sh
cd desktop
npm run build:ffmpeg
```

O build fixa a revisao do x264, desabilita deteccao automatica de bibliotecas
externas, executa um transcode real e rejeita qualquer dependencia dinamica que
nao seja fornecida pelo proprio macOS. O pacote inclui as licencas e um arquivo
`build-metadata.json` com fontes, checksums, flags e toolchain.

Antes de publicar comercialmente, o release tambem deve oferecer o codigo-fonte
correspondente e suas instrucoes de build pelo periodo e nos termos exigidos
pela GPL. A equipe deve validar o procedimento de distribuicao com assessoria
juridica; este repositorio registra os dados tecnicos, mas nao substitui essa
revisao.

Durante desenvolvimento, o Electron aplica uma assinatura ad-hoc ao aplicativo
e aos sidecars. Para um release publico, defina `EDVID_MAC_SIGN_IDENTITY` com a
identidade `Developer ID Application` instalada no Keychain e execute o build
em um ambiente configurado para assinatura e notarizacao da Apple.
