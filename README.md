# edvid

Editor de vídeo por conversa. Você joga o material bruto numa pasta, abre seu
agente ali dentro e diz *"edita isso num Reels"*. Ele transcreve, escolhe as
melhores tomadas, corta os silêncios, corrige a cor e te mostra o resultado para
aprovação — depois disso entram legendas, gráficos e trilha.

Funciona em **short-form vertical** (Reels/TikTok/Shorts) e **longform
horizontal** (YouTube).

**A transcrição roda na sua máquina.** Nenhuma chave de API, nenhuma cota,
nenhum limite de tamanho.

---

## Instalação

São **dois comandos**: um para os programas que a edvid usa, outro para a edvid.

### 1. Os programas (uma vez na vida)

- **`uv`** — gerenciador de pacotes Python. A edvid usa para tudo, e ele também
  cuida de instalar o Python certo, então você não instala Python separado.
- **`ffmpeg`** — corte, cor e render (Fase 1).
- **`node`** — Remotion, usado nas legendas e gráficos (Fase 2).
- **`git`** — a edvid não usa, mas o **Claude Code** usa. Se você vai rodar a
  edvid dentro do Claude Code, instale; noutro agente, pode pular.

**Windows** — abra o **PowerShell** (não o Prompt de Comando antigo):

```powershell
winget install astral-sh.uv Gyan.FFmpeg OpenJS.NodeJS.LTS Git.Git
```

**macOS** — abra o Terminal. Se você ainda não tem o Homebrew, o comando está em
[brew.sh](https://brew.sh).

```bash
brew install uv ffmpeg node git
```

**Linux** — `ffmpeg`, `node` e `git` vêm do gerenciador da sua distro
(`sudo apt install ffmpeg nodejs npm git`), e o `uv` do instalador oficial:
`curl -LsSf https://astral.sh/uv/install.sh | sh`. O `node` do apt costuma ser
antigo; confira com `node --version` que é 18 ou maior.

### 2. Feche e reabra o terminal

Isso não é opcional. Programas recém-instalados só aparecem numa janela nova —
sem isso o próximo comando falha com *"não é reconhecido"*.

**Confira que os quatro responderam** antes de seguir. Um gerenciador de pacotes
pode instalar três e falhar no quarto sem que isso fique óbvio no meio da saída:

```powershell
uv --version; ffmpeg -version | Select-Object -First 1; node --version; git --version
```

```bash
uv --version; ffmpeg -version | head -1; node --version; git --version
```

Se algum não responder, instale só ele e volte aqui. No Windows:
`winget install astral-sh.uv` · `winget install Gyan.FFmpeg` ·
`winget install OpenJS.NodeJS.LTS` · `winget install Git.Git`.

Se você pular esta conferência não tem problema — o instalador do passo 3 refaz
ela no fim e diz o que faltou.

### 3. Instale a edvid — um comando

```bash
uv run https://raw.githubusercontent.com/fillrochaa/edvid/main/edvid_install.py
```

O mesmo comando, sem alterar nada, no PowerShell do Windows, no Terminal do Mac
e no Linux.

Pronto. Não há passo 4.

### O que o instalador faz

- Descobre sozinho qual agente você usa (Claude Code, Codex) e instala nas
  pastas de skills que existirem — sem você dizer qual.
- Baixa e instala a **skill do Remotion** também, que a Fase 2 precisa. Era o
  passo que todo mundo esquecia.
- Instala as dependências Python, inclusive o WhisperX da transcrição.
- Confere `ffmpeg` e `Node` no fim e, se você pulou o passo 1 ou esqueceu de
  reabrir o terminal, imprime o comando de instalação **da sua plataforma**.
- Não encosta numa instalação de desenvolvedor (pasta com `.git`) sem `--force`,
  e preserva seu `.env` se você já tinha um.

Na primeira transcrição ele baixa os modelos do Whisper e de alinhamento
(alguns GB). Depois disso ficam em cache e nunca mais baixam.

---

## Chaves de API

**Nenhuma é necessária.** A transcrição é local.

Estas são opcionais, e o agente pede cada uma na primeira vez que o recurso for
usado — você decide na hora:

| Chave | Para quê |
|---|---|
| `PEXELS_API_KEY` | imagens e vídeos ilustrativos na Fase 2 |
| `TREBLO_API_KEY` | trilha sonora gerada por IA na Fase 3 |
| `ELEVENLABS_API_KEY` | segunda opinião de transcrição num trecho duvidoso |
| `GOOGLE_API_KEY` + `GOOGLE_CSE_ID` | imagens de marcas/pessoas específicas |

Imagens funcionam sem chave via Wikimedia Commons, e trilha funciona com um
arquivo de música local. Nada da Fase 2 ou 3 fica bloqueado por falta de chave.

---

## Primeiro uso

1. Coloque seus vídeos brutos numa pasta.
2. Abra o Claude Code **dentro dessa pasta**.
3. Diga: *"edita esses vídeos num Reels"* ou *"faz um inventário dessas tomadas
   e me propõe uma estratégia"*.

Tudo o que for gerado vai para uma subpasta `edit/` — seus arquivos originais
não são tocados.

---

## Atualizar

Rode o mesmo comando da instalação. Ele substitui a versão antiga pela nova.

```bash
uv run https://raw.githubusercontent.com/fillrochaa/edvid/main/edvid_install.py
```

---

## Sobre a transcrição

A edvid usa [WhisperX](https://github.com/m-bain/whisperX): transcreve com o
Whisper e depois faz **alinhamento forçado** do texto contra a forma de onda,
com um modelo wav2vec2 do idioma detectado. Isso importa porque as legendas
karaokê da Fase 2 leem o tempo de cada palavra — um decoder comum estima esses
tempos e erra.

Medido contra a detecção acústica de fala da própria skill, num clipe em
português: **93% das palavras caem dentro de uma região real de fala**, e o fim
da fala é marcado com 10 ms de erro.

Velocidade: **melhora com a duração**, porque carregar o modelo é um custo fixo
de ~18 s. Um clipe de 16 s levou 23 s; um vídeo de 2min46s levou 109 s — mais
rápido que o próprio áudio.

Português usa `jonatasgrosman/wav2vec2-large-xlsr-53-portuguese`, e mais de 30
idiomas estão cobertos sem precisar de token. Em Mac com chip Apple roda na CPU
(o backend do WhisperX não tem suporte a Metal); com placa NVIDIA usa CUDA
automaticamente.

---

## Problemas comuns

**`uv` não é reconhecido como comando** — você não reabriu o terminal
depois de instalar o `uv`. Feche essa janela, abra outra e tente de novo.

**Uma janela pedindo para instalar as Ferramentas de Linha de Comando (macOS)**
— normal na primeira vez. Aceite, espere terminar e rode o comando de novo.

**O Claude não encontra a skill** — reinicie o Claude Code. O instalador
imprime onde instalou; confirme que a pasta está lá.

**`ModuleNotFoundError` ao usar a skill** — as dependências não terminaram de
instalar. Rode `uv sync --directory <pasta que o instalador imprimiu>`.

**A primeira transcrição parece travada** — está baixando os modelos (alguns
GB). Acontece uma vez só.

---

## Para quem quer contribuir com código

O instalador copia arquivos, o que é o certo para quem só usa. Para desenvolver,
clone onde você guarda seus projetos e crie um symlink para a pasta de skills —
o `install.md` documenta esse formato. O instalador detecta um clone git e não
mexe nele.

---

## Licença

Veja [LICENSE](LICENSE).
