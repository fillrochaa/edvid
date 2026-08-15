# uv no Edvid Desktop

O Edvid empacota `uv` e `uvx` na versao `0.12.3` a partir dos binarios oficiais
da Astral. Prepare o runtime da plataforma atual com:

```sh
cd desktop
npm run stage:uv
```

O staging valida o checksum publicado na release, a GitHub Artifact Attestation
para o repositorio `astral-sh/uv`, o commit exato da release, a versao executada
e as dependencias dinamicas no macOS. A GitHub CLI (`gh`) e obrigatoria na
maquina de build para a verificacao de provenance.

O `uv` e distribuido sob licenca Apache-2.0 ou MIT. Os dois textos de licenca e
um `build-metadata.json` sao incluidos ao lado dos executaveis no aplicativo.
