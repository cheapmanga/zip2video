# zip2video

Wrap any file inside a **valid video file** (MKV or MP4), and get it back byte for byte.

> **Two tools, pick by where the video is going.**
> `zip2video.py` hides the file in the *container* — compact and instant, but a video
> host that re-encodes will destroy it. `zipvisual.py` draws the file into the
> *picture* — far bigger and slower, but it **survives transcoding**. See
> [Surviving a video host](#surviving-a-video-host-zipvisualpy).

The output is a real video: it has a 1-second video track, so players, `ffprobe`,
`mediainfo` and `file(1)` all recognise it as genuine. The embedded file rides along
untouched — no re-compression, same SHA-256 on the way out.

Pure Python, **no dependencies**. Both containers are written by hand: no ffmpeg,
no MKVToolNix.

> This is encapsulation, **not encryption**. Anyone with this script (or MKVToolNix,
> for the MKV) can pull the file straight back out. Do not treat it as protection.

## Usage

```bash
python zip2video.py pack        archive.zip            # -> archive.mkv
python zip2video.py pack --mp4  archive.zip            # -> archive.mp4
python zip2video.py pack        archive.zip  clip.mp4  # extension picks the container
python zip2video.py info        clip.mp4               # show what is embedded
python zip2video.py unpack      clip.mp4               # -> archive.zip
```

`unpack` and `info` detect the container on their own — you never have to say which
one it is.

On Windows there are drag-and-drop wrappers: drop a `.zip` on `pack.bat` or
`pack-mp4.bat`, drop the resulting video on `unpack.bat`. See `README.txt` for the
end-user instructions shipped alongside them.

Despite the name, it is not limited to `.zip` — any file works. Only the MIME label
stored next to it says "zip".

## How it works

| | MKV | MP4 |
|---|---|---|
| Payload location | `Attachments` → `AttachedFile` | top-level `free` box |
| Why it is ignored | official Matroska attachment mechanism | ISO/IEC 14496-12 defines `free` contents as irrelevant and skippable |
| Video track | one MJPEG frame | one H.264 keyframe (Constrained Baseline) |

**MKV** uses the documented Matroska attachment element, so `mkvextract` can recover
the file even without this script.

**MP4** has no attachment concept, so the payload goes into a top-level `free` box.
The spec states its contents may be ignored, so every conformant parser walks past it.
The box carries a small header (`ZIP2VID\0`, version, name, MIME type, length) so
`unpack` can find it unambiguously. The rest is a minimal but complete ISOBMFF tree —
`ftyp` / `free` / `mdat` / `moov`, with `stco` written last since it stores an
absolute file offset.

Both the single H.264 frame and its `avcC` configuration record are embedded as
base64 constants, which is what keeps the runtime dependency-free.

## Caveat: re-encoding destroys the payload

If the file goes through anything that re-encodes or remuxes it — a video host, a
messaging app that compresses videos, an editor — the attachment or the `free` box is
dropped and the embedded file is gone. Only the untouched original unpacks.

A transcoded carrier is easy to recognise: it collapses to a couple of kilobytes,
because all that is left is one second of a still image. A 39 MB payload came back
as a 3 KB `.mp4`.

If your host transcodes, use `zipvisual.py` instead.

## Verified

- Byte-identical round-trip (SHA-256) for both containers.
- MP4 is genuinely decodable, not merely parseable: `ffprobe` reports
  `h264 (Constrained Baseline), yuv420p(progressive), 320x240`, and the frame decodes
  to a real image.
- Edge cases covered: empty payload, 3 MB non-zip binary, UTF-8 file names,
  payloads above 4 GB (64-bit `largesize` box).
- Clean errors on a plain video with nothing embedded, a non-video file, and a
  truncated file.

---

# Surviving a video host (`zipvisual.py`)

When the host re-encodes, the only channel that survives is the one it is trying to
preserve: **the picture**. `zipvisual.py` draws the file into the frames as a grid of
black and white blocks.

```bash
python zipvisual.py pack   archive.zip  carrier.mp4   # upload carrier.mp4
python zipvisual.py unpack downloaded.mp4             # after the host mangled it
python zipvisual.py info   downloaded.mp4             # how much survived
python zipvisual.py selftest                          # prove it end to end
```

Needs `numpy`, plus ffmpeg on PATH (or `pip install imageio-ffmpeg`). On Windows,
`pack-visual.bat` and `unpack-visual.bat` are the drag-and-drop equivalents.

## What the measurements actually showed

Two findings shaped the design, both from re-encoding real test patterns through
ffmpeg rather than reasoning about it:

**1. Pixel noise is not the problem.** With 12×12 pixel blocks, frames that survive a
1080p → 720p @ 2.1 Mbps transcode come back **bit-perfect** — 0 errors in 321,600
bits. Blocks that coarse are far above the codec's quantisation.

**2. Whole frames vanish.** A host retargeting 30 fps to 25 fps silently deletes one
frame in six. In one test, 36 frames in gave 32 out — and every survivor was perfect,
while frames 12, 18, 24 and 30 were simply gone.

So intra-frame error correction would have been wasted effort. What was needed was
frame identity and erasure coding:

- Every frame carries a shard id and a CRC32, so a damaged or missing frame is a
  clean **erasure**, never silent corruption.
- **Reed-Solomon over GF(256)**, systematic, 180 data + 54 parity shards per group.
  Any 180 of the 234 rebuild the group, so up to **23% of frames can vanish**.
- Shards are **interleaved** across groups, so a burst of consecutive lost frames is
  spread thinly instead of wiping one group out.
- The video is written at **24 fps** on purpose: a host retargeting to 25, 30 or 60
  then *duplicates* frames, which costs nothing, instead of deleting them.
- The manifest (name, size, CRC, coding parameters) is repeated throughout the video.

## Two grids

A block survives as long as it is still roughly 8 pixels wide in whatever resolution
the host outputs. Below that the information is genuinely destroyed — no sampling
trick recovers it.

| Mode | Grid | Capacity | Survives down to |
|---|---|---|---|
| default | 160×90 (12 px at 1080p) | 1790 B/frame | 720p |
| `--robust` | 80×45 (24 px at 1080p) | 440 B/frame | 360p |

`unpack` does not need to be told which was used — it tries both, and the frame CRC
makes the answer unambiguous.

## Measured results

Carrier: 600 KB payload, 24 fps, 468 shards, 360 needed.

| Host profile | default grid | `--robust` |
|---|---|---|
| 720p 2.1 Mbps, 24→25 fps | **PASS** (0 unreadable) | **PASS** |
| 720p 2.1 Mbps, 24→30 fps | **PASS** (0 unreadable) | **PASS** |
| 1080p CRF 28 (heavy quant) | **PASS** (0 unreadable) | **PASS** |
| 720p 1.5 Mbps | **PASS** (0 unreadable) | **PASS** |
| letterboxed to 4:3 | FAIL | **PASS** |
| 480p 800 kbps, 24→30 fps | FAIL | **PASS** (282 rebuilt) |
| 360p 500 kbps | FAIL | **PASS** |
| 240p 300 kbps | FAIL | FAIL |

Every PASS is a byte-for-byte match verified by SHA-256, not merely "it decoded".

## The price

Measured on a 5 MB payload: 30 s to pack, producing a **67 MB** video lasting 158 s.
That is roughly **13× the payload size**, and about 32 s of video per megabyte.

Extrapolated to a 39 MB archive: expect **~21 minutes of video, ~525 MB**, a few
minutes to encode. The ratio is brutal, and it is the honest cost of the only channel
a transcoder cannot strip. For a few megabytes it is very usable; at tens of
megabytes, consider whether a plain file host is not the better answer.

## Limits

- Below 360p output, the payload is unrecoverable even in `--robust` mode.
- A host that *blends* frames for frame-rate conversion (rather than dropping or
  duplicating them) would mix adjacent shards. Ordinary hosts drop/duplicate.
- A large burned-in watermark or logo destroys the blocks it covers; the parity
  absorbs a little of that, not a lot.
- Storing arbitrary files on a video host works against what the service is for.
  Do not treat it as a backup — such files tend to get purged.
