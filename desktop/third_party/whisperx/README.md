# Python e WhisperX no Edvid Desktop

O Edvid empacota CPython `3.12.13` e WhisperX `3.8.6`. O Python portatil e uma
distribuicao do projeto `python-build-standalone`, selecionada e verificada pelo
uv `0.12.3`. As dependencias Python sao resolvidas pelo arquivo
`desktop/python/whisperx/uv.lock`, que fixa URLs e hashes dos artefatos.

Prepare o runtime da plataforma atual com:

```sh
cd desktop
npm run stage:uv
npm run build:ffmpeg
npm run stage:python-whisperx
```

O staging nao usa o Python instalado pelo usuario. Ele valida as versoes e os
imports de WhisperX, PyTorch, faster-whisper e CTranslate2, testa a decodificacao
de audio com o FFmpeg interno e rejeita dependencias nativas do Homebrew ou de
`/usr/local` no macOS.

O TorchCodec usado pela versao fixada do PyTorch requer a ABI compartilhada do
FFmpeg 7. O build inclui um conjunto LGPL `7.1.5` apenas para essa integracao; o
FFmpeg/FFprobe principal do Edvid continua sendo `8.1.2`, com os recursos de
renderizacao e tratamento de voz ja definidos no aplicativo.

O aplicativo inclui o runtime e as bibliotecas, mas nao inclui os pesos dos
modelos de IA. Modelos devem ser baixados sob demanda para a pasta de dados do
Edvid, fora do pacote assinado, e reutilizados entre atualizacoes do aplicativo.
Essa separacao reduz muito o tamanho e permite oferecer modelos diferentes para
qualidade e velocidade.

WhisperX usa BSD-2-Clause e CPython usa PSF-2.0. Os textos principais, o lockfile
e os metadados de build sao incluidos no aplicativo. Avisos de todas as
dependencias permanecem nos respectivos diretorios `.dist-info`.
