# Longform template (YouTube 16:9) — DATA-DRIVEN

Phase-2 Remotion composition for horizontal longform. **The code is immutable**
— write `public/edit-data.json`; never read or edit `src/Main.tsx`. Graphics
punctuate, they don't saturate. Captions are NOT burned — ship a `.srt`
(`captions_srt.py`) for YouTube CC.

## Scaffold (one command)

```bash
cp -R <skill>/assets/longform/. <edit>/remotion/ && cd <edit>/remotion && npm install
```

Copy `cut.mp4` into `public/`, then write `public/edit-data.json`.

## edit-data.json schema (times in seconds on the cut timeline)

```jsonc
{
  "width": 1920, "height": 1080,      // match cut.mp4 exactly (4K: 3840×2160)
  "fps": 30,                           // exact source fps (23.976 → 23.976)
  "durationSec": 512.4,                // EXACT cut.mp4 duration (ffprobe)
  "accent": "#33e0a3",                 // brand accent for bars/cards/callouts
  "broll": [                           // full-frame cutaways over narration
    {"kind": "image", "src": "broll/x.jpg", "start": 12, "dur": 4},
    {"kind": "video", "src": "broll/y.mp4", "start": 40, "dur": 6}
  ],
  "lowerThirds": [                     // name/title card, bottom-left
    {"name": "Fill Rocha", "title": "Criador · Edvid", "start": 6, "dur": 4}
  ],
  "chapters": [                        // title card at each chapter start
    {"title": "O que é isso", "start": 14, "dur": 2.4}
  ],
  "callouts": [                        // emphasis chip at a normalized x/y
    {"text": "2x mais rápido", "start": 33, "dur": 3, "x": 0.62, "y": 0.28}
  ],
  "soundtrack": {"enabled": false, "file": "trilha.mp3", "volume": 0.0445}
}
```

## Render

`npx remotion render Longform out/render.mp4`, loudnorm → `edit/final.mp4`.
Keep the last ~20s visually calm (YouTube end cards).
