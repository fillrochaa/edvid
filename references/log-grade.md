# LOG / HDR grading — the detail behind `detect_color.py`

Read this ONLY when `detect_color.py` returns a LOG/HLG/PQ profile, when it
reports `confidence: low`, or when you are adding a new vendor preset. On a
`rec709` source — the common case — none of it applies and the grade is `""`.

`detect_color.py` resolves this automatically; the table below is what it encodes
and what you need when reading its evidence or extending it. Probe by hand only
when the helper reports `low` confidence:

```bash
ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,profile,color_transfer,color_primaries,color_space \
  -show_entries stream_tags=com.apple.proapps.logprofile -of default=nw=1 <source>
```

| What you see | Profile | Grade |
|---|---|---|
| `codec_name=prores`, `pix_fmt=yuv422p10le`, `color_primaries=bt2020`, `color_transfer=unknown`, encoder tag `Apple ProRes` | **Apple Log** | preset `apple_log` |
| `color_transfer=arib-std-b67` | HLG | tonemapped by `render.py`; light corrective only |
| `color_transfer=smpte2084` | PQ / HDR10 | tonemapped by `render.py`; light corrective only |
| Sony `slog3`/`s-gamut3`, Panasonic `v-log`, Canon `clog3` in the tags | that vendor's LOG | its own expansion — build one, then add it to `PRESETS` |

**Nothing in the file says "Apple Log".** The signature above IS the
identification — measured on a real iPhone ProRes file: BT.2020 primaries, a
10-bit 4:2:2 ProRes stream, and an EMPTY transfer tag. If you wait for a tag that
names the profile you will never find one, and an HDR-only check calls it plain SDR.

**Apple Log is the one that is already proven** (`apple_log` in `grade.py`,
approved 2026-07 on an iPhone ProRes talking head): cool, contrasty, skin rosy.
Two things about it that are not obvious:
- The file declares **BT.2020 primaries with an empty transfer tag**, so an
  HDR-only check reads it as ordinary SDR. `render.py`'s `wide_gamut_chain`
  converts it to Rec.709 before the grade — the preset assumes that already ran.
- `hue=h=-9` is load-bearing: expanding Apple Log pushes skin yellow-green, and
  the negative rotation brings it back. Rotating positive makes it worse.
- Its `colorlevels` **must** be fed 8-bit (see the 8-bit bullet above). LOG sources
  are the 10-bit ones, so this preset is exactly where the bug bites — and it bites
  silently: the `--candidates` montage grades an 8-bit frame and looks right, so
  only the rendered cut goes black. `verify_cut.py` catches it on the "black
  frames" line; don't dismiss that line as a false positive on a LOG source.
