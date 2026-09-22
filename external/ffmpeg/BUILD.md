# ffmpeg.exe — minimal LGPL build

`ffmpeg.exe` here is **not** a stock download. It is a purpose-built binary
containing only the codecs this pipeline uses, which is why it is 1.54 MB
instead of the ~120 MB a stock Windows build costs (a shared build's
`ffmpeg.exe` is small, but it load-time links seven DLLs totalling ~120 MB —
none can be omitted, so the whole set would have to ship).

| | |
|---|---|
| Version | FFmpeg 7.1.2 (release tarball, not master) |
| License | **LGPL v2.1 or later** — no `--enable-gpl`, no `--enable-version3` |
| Linkage | Static; no DLLs, no external runtime |
| Size | 1.54 MB |

## What it can do

The Sounds phase runs exactly one ffmpeg command
(`asset_convert/audio/audio_converter.py`, `convert_file_to_xwm`):

```
ffmpeg -y -i <src> -ac 1 -ar 44100 -c:a pcm_s16le <dst.wav>
```

So the build enables only what that needs, plus a little headroom for the
source formats the games ship. Oblivion voice lines are MP3; **FO3/FNV voice
lines are Ogg Vorbis** (52,896 of FalloutNV.esm's 52,936), which is why the
`vorbis` decoder and `ogg` demuxer are enabled. ffmpeg's native Vorbis decoder
is LGPL, so it does not change this build's licence:

* **Decoders** — `mp3`, `mp3float`, `vorbis`, `pcm_s16le`, `pcm_u8`,
  `pcm_s24le`, `pcm_s32le`, `pcm_f32le`, `adpcm_ms`
* **Encoder** — `pcm_s16le`
* **Demuxers** — `mp3`, `ogg`, `wav`, `aiff` · **Muxer** — `wav`
* **Filters** — `aresample`, `aformat`, `anull`, `atrim`, `volume`
  (`-ac`/`-ar` build an `aresample` graph internally)
* **Protocol** — `file` only; the build is `--disable-network`

Anything else — video, every other audio codec, network protocols, devices —
is compiled out. Feeding it an unexpected format fails cleanly with
"Decoder not found" rather than silently producing a broken file.

x86-64 SIMD (nasm) is **enabled**: mp3 decode is the Sounds phase's inner
loop over tens of thousands of voice lines.

## Verification

Output was compared against the stock BtbN LGPL build on a real Oblivion
voice line. The PCM `data` chunk is **byte-identical**; the only difference
in the whole file is the `LIST/INFO` encoder-version string
(`Lavf61.7.100` vs `Lavf61.7.103`), which is metadata and never reaches the
game — xWMAEncode reads the samples only.

## Rebuilding

`tools/generators/build_ffmpeg.py` reproduces this binary from source. It needs a Linux
environment with a MinGW-w64 cross-compiler (on Windows, WSL is fine) and
takes no privileges — see the script's header for the no-sudo bootstrap it
uses when the toolchain is not already installed.

```bash
python tools/generators/build_ffmpeg.py --output external/ffmpeg/ffmpeg.exe
```

The exact `configure` line is embedded in that script (`CONFIGURE_FLAGS`) and
is the authoritative record of how this binary was produced.

A rebuild is **functionally** identical, not bit-for-bit: GCC bakes build
paths and timestamps into the image, so the `.exe` hash differs between
machines. Verified on a from-scratch rebuild — different binary hash, and
decoded PCM byte-identical to the shipped build's. Compare outputs, never
`.exe` hashes, when checking a rebuild.

## License compliance

FFmpeg is LGPL v2.1+; `COPYING.LGPLv2.1` sits next to the binary. Because
this build is **statically linked**, LGPL §6 requires that recipients be able
to relink it against a modified FFmpeg — satisfied by shipping the complete
build recipe (`tools/generators/build_ffmpeg.py`, which pins the exact upstream source
tarball and every configure flag) together with the unmodified upstream
sources it downloads. **No FFmpeg source is patched.**
