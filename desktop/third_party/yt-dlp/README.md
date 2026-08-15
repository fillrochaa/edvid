# yt-dlp no Edvid Desktop

O Edvid empacota o binario oficial standalone do yt-dlp na versao `2026.07.04`.
No macOS, o artefato e universal (Apple Silicon e Intel); no Windows x64, o
aplicativo usa `yt-dlp.exe`. Nenhum Python instalado pelo usuario e necessario.

Prepare o runtime da plataforma atual com:

```sh
cd desktop
npm run stage:yt-dlp
```

O script usa um chaveiro GPG temporario e exige o fingerprint oficial
`AC0CBBE6848D6A873464AF4E57CF65933B5A7581`. A assinatura dos checksums e
validada antes do download ser aceito. O staging tambem confere a versao,
extratores essenciais, arquiteturas e dependencias dinamicas no macOS.

O projeto yt-dlp e dedicado ao dominio publico sob a Unlicense. Os binarios
recomendados para macOS e Windows, porem, incluem componentes empacotados com
PyInstaller e formam uma obra combinada distribuida sob GPL-3.0-or-later. O
aplicativo inclui `LICENSE`, `THIRD_PARTY_LICENSES.txt` e metadados com os
checksums verificados.

O Edvid nao executa a atualizacao automatica do sidecar. Novas versoes devem ser
fixadas no manifesto, revisadas e distribuidas como parte de uma nova versao
assinada do aplicativo.
