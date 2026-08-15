/**
 * ScatterCaptions — caption style "disperso".
 *
 * Serif, lowercase, one word at a time, laid out in short ragged lines whose
 * horizontal offset looks random but is NOT: it is hashed off the cue index, so
 * every frame of a render agrees with every other. Never Math.random() here —
 * Remotion renders frames independently and a real random would jitter the
 * layout on every single frame.
 *
 * Two entrances, on purpose:
 *   - ordinary words just FADE IN — no movement at all;
 *   - a highlighted word per cue resolves OUT of a heavy blur at ~1.6× the size,
 *     and dissolves back INTO blur when the cue leaves.
 * The contrast is the style — if every word were special, none would be.
 *
 * Data: public/captions.json (word level, same file the karaoke style reads).
 * No extra generation step.
 */
import {
  AbsoluteFill,
  interpolate,
  Easing,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import {loadFont} from '@remotion/google-fonts/Lora';
import {measureText} from '@remotion/layout-utils';
import captions from '../public/captions.json';
import editData from '../public/edit-data.json';

const {fontFamily} = loadFont('normal', {weights: ['400', '600']});
loadFont('italic', {weights: ['400', '600']});

type Word = {text: string; startMs: number; endMs: number};
type Placed = Word & {size: number; hi: boolean};
type Cue = {startMs: number; endMs: number; lines: Placed[][]; shift: number[]; drop: number};

const C = (editData as any).captions ?? {};
const SAFE_W = C.scatterSafeWidth ?? 820;
const BASE = C.scatterFontSize ?? 58;
const HI_SCALE = 1.62;
const SPREAD = 0.45; // how far a line may wander off centre, 0..1 of the free room
const WORD_GAP = 12;
// Block centre as a fraction of height. The reference puts this text over
// B-roll, where the middle of the frame is free; on a talking head the middle IS
// the face, so the default sits on the chest instead. Raise it only over B-roll.
const OFFSET_Y = C.scatterOffsetY ?? 0.72;

// deterministic hash — same input, same layout, on every frame
const hash = (n: number) => {
  const x = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
};

// Off-white, not #fff, with a very slight darkening toward the baseline. The
// gradient rides on the WORD span, so it runs over the letterforms themselves
// rather than over the block — that is what makes it read as ink weight instead
// of a wash. Same background-clip:text technique as the stacked style, and the
// same caveat: it has to be on the element that OWNS the text, next to its
// filter, or the glyphs come out transparent.
const INK: React.CSSProperties = {
  backgroundImage: 'linear-gradient(180deg,#f8f5ef 0%,#f4f1e9 54%,#d5cec1 100%)',
  WebkitBackgroundClip: 'text',
  backgroundClip: 'text',
  WebkitTextFillColor: 'transparent',
  color: 'transparent',
};

const clean = (t: string) => t.replace(/[.,!?…]+$/, '').toLowerCase();
const isBreak = (t: string) => /[.,!?…]$/.test(t);

// A cue is a breath: punctuation, or 6 words, or a gap of 400ms+ in the speech.
function buildCues(words: Word[]): Cue[] {
  const groups: Word[][] = [];
  let cur: Word[] = [];
  words.forEach((w, i) => {
    cur.push(w);
    const next = words[i + 1];
    const gap = next ? next.startMs - w.endMs : Infinity;
    if (cur.length >= 6 || isBreak(w.text) || gap > 400) {
      groups.push(cur);
      cur = [];
    }
  });
  if (cur.length) groups.push(cur);

  return groups.map((g, gi) => {
    // one highlight per cue: the longest word, and only if it carries weight
    let hiIdx = -1;
    let hiLen = 6;
    g.forEach((w, i) => {
      const l = clean(w.text).length;
      if (l > hiLen) {
        hiLen = l;
        hiIdx = i;
      }
    });

    const placed: Placed[] = g.map((w, i) => ({
      ...w,
      size: i === hiIdx ? Math.round(BASE * HI_SCALE) : BASE,
      hi: i === hiIdx,
    }));

    // ragged lines of 2–3 words; a highlighted word takes a line of its own so
    // it can breathe at its bigger size
    const lines: Placed[][] = [];
    let line: Placed[] = [];
    placed.forEach((p, i) => {
      const want = hash(gi * 31 + i) > 0.5 ? 4 : 3;
      if (p.hi) {
        if (line.length) lines.push(line);
        lines.push([p]);
        line = [];
        return;
      }
      line.push(p);
      if (line.length >= want) {
        lines.push(line);
        line = [];
      }
    });
    if (line.length) lines.push(line);

    // horizontal offset per line, clamped so nothing leaves the safe width
    const shift = lines.map((ln, li) => {
      const w = ln.reduce(
        (acc, p, k) =>
          acc +
          measureText({text: clean(p.text), fontFamily, fontSize: p.size, fontWeight: 400}).width +
          (k ? WORD_GAP : 0),
        0,
      );
      // Only a fraction of the free room: at full range consecutive lines land
      // at opposite edges and the cue reads as scattered debris instead of a
      // phrase. SPREAD is what keeps it a paragraph that breathes.
      const room = Math.max(0, (SAFE_W - w) / 2) * SPREAD;
      return (hash(gi * 17 + li * 5 + 3) * 2 - 1) * room;
    });

    return {
      startMs: g[0].startMs,
      endMs: g[g.length - 1].endMs,
      lines,
      shift,
      drop: (hash(gi * 53 + 11) * 2 - 1) * 40, // vertical jitter of the block
    };
  });
}

const CUES = buildCues(captions as Word[]);

const ENTER = 7; // frames — fade + drop
const HI_ENTER = 10; // frames — blur resolve
const EXIT = 8; // frames — the cue leaves

const CueView: React.FC<{cue: Cue; endFrame: number}> = ({cue, endFrame}) => {
  const frame = useCurrentFrame();
  const {fps, height} = useVideoConfig();
  const exitStart = endFrame - EXIT;
  const out = interpolate(frame, [exitStart, endFrame], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center'}}>
      <div
        style={{
          width: SAFE_W,
          translate: `0px ${Math.round(height * (OFFSET_Y - 0.5)) + cue.drop}px`,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          fontFamily,
        }}
      >
        {cue.lines.map((ln, li) => (
          <div
            key={li}
            style={{
              display: 'flex',
              gap: WORD_GAP,
              alignItems: 'baseline',
              whiteSpace: 'pre',
              translate: `${cue.shift[li].toFixed(1)}px 0px`,
              lineHeight: 1.03,
            }}
          >
            {ln.map((p, wi) => {
              const start = (p.startMs / 1000) * fps;
              const dur = p.hi ? HI_ENTER : ENTER;
              const t = interpolate(frame, [start, start + dur], [0, 1], {
                extrapolateLeft: 'clamp',
                extrapolateRight: 'clamp',
                easing: Easing.out(Easing.cubic),
              });
              // Highlighted: blur resolves in, blurs back out on the way off.
              // Ordinary: fade ONLY. They also had a drop from above, and with a
              // word landing every ~200ms the whole screen read as frantic —
              // motion on every word is motion on nothing.
              const blur = p.hi ? (1 - t) * 26 + out * 30 : 0;
              return (
                <span
                  key={wi}
                  style={{
                    ...INK,
                    fontSize: p.size,
                    fontWeight: p.hi ? 600 : 400,
                    fontStyle: p.hi && hash(li * 7 + wi) > 0.65 ? 'italic' : 'normal',
                    opacity: t * (1 - out),
                    filter: `${blur > 0.4 ? `blur(${blur.toFixed(1)}px) ` : ''}drop-shadow(0 4px 14px rgba(0,0,0,0.5))`,
                  }}
                >
                  {clean(p.text)}
                </span>
              );
            })}
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};

export const ScatterCaptions: React.FC = () => {
  const {fps, durationInFrames} = useVideoConfig();
  const frame = useCurrentFrame();
  // ONE mounted layer picking the live cue, not a <Sequence> per cue: the same
  // reason the split layer is mounted flat — a Sequence would restart the local
  // frame and the per-word absolute timings would have to be rebased.
  let idx = -1;
  for (let i = 0; i < CUES.length; i++) {
    if (frame >= Math.round((CUES[i].startMs / 1000) * fps)) idx = i;
  }
  if (idx < 0) return null;
  const next = CUES[idx + 1];
  const endFrame = next
    ? Math.round((next.startMs / 1000) * fps)
    : Math.min(durationInFrames, Math.round((CUES[idx].endMs / 1000) * fps) + fps);
  if (frame >= endFrame) return null;
  return <CueView cue={CUES[idx]} endFrame={endFrame} />;
};
