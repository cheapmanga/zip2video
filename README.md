# zip2mkv

Wrap any file inside a **valid video file** (MKV or MP4), and get it back byte for byte.

The output is a real video: it has a 1-second video track, so players, `ffprobe`,
`mediainfo` and `file(1)` all recognise it as genuine. The embedded file rides along
untouched — no re-compression, same SHA-256 on the way out.

Pure Python, **no dependencies**. Both containers are written by hand: no ffmpeg,
no MKVToolNix.

> This is encapsulation, **not encryption**. Anyone with this script (or MKVToolNix,
> for the MKV) can pull the file straight back out. Do not treat it as protection.

## Usage

```bash
python zip2mkv.py attach      holiday.mkv archive.zip  # ride along with a real video
python zip2mkv.py pack        archive.zip              # -> archive.mkv
python zip2mkv.py pack --mp4  archive.zip              # -> archive.mp4
python zip2mkv.py pack        archive.zip  clip.mp4    # extension picks the container
python zip2mkv.py info        clip.mp4                 # show what is embedded
python zip2mkv.py unpack      clip.mp4                 # -> archive.zip
```

## Attaching to a video you already have

`pack` builds a throwaway one-second carrier. `attach` takes a video you already
have and rides along with it, so the result is an ordinary video that plays normally:

```bash
python zip2mkv.py attach holiday.mkv archive.zip   # -> holiday_with_archive.mkv
```

Nothing already in the file moves, which is what keeps the video intact:

- **MP4** — the `free` box is *appended at the very end*. `stco`/`co64` store absolute
  file offsets, so appending is the one edit that cannot invalidate them.
- **MKV** — the `Attachments` element is appended at the end of the Segment's data and
  only the Segment size field is rewritten. Cue and SeekHead positions are stored
  relative to the start of the Segment data, so they stay correct even if that size
  field grows a byte.

Verified: after attaching, the decoded video and audio streams are **bit-identical**
to the original (same SHA-256 on the raw decoded output), and the payload comes back
byte-exact.

One limitation, measured rather than assumed: because the attachment lands *after* the
first Cluster, `ffmpeg` and `mkvextract` will not see it — parsers stop reading
header-level elements once they hit a Cluster. This script still finds it, since it
walks the whole file. If you need the attachment visible to standard tools, use `pack`
instead, which places it before the Cluster.

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
the file even without this script — verified by having `ffmpeg -dump_attachment` pull
it out byte-exact. This only holds because `pack` writes the `Attachments` element
*before* the first Cluster; placed after it, parsers never reach it.

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

## Verified

- Byte-identical round-trip (SHA-256) for both containers.
- MP4 is genuinely decodable, not merely parseable: `ffprobe` reports
  `h264 (Constrained Baseline), yuv420p(progressive), 320x240`, and the frame decodes
  to a real image.
- Edge cases covered: empty payload, 3 MB non-zip binary, UTF-8 file names,
  payloads above 4 GB (64-bit `largesize` box).
- Clean errors on a plain video with nothing embedded, a non-video file, and a
  truncated file.
