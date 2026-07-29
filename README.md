# zip2video

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

## Verified

- Byte-identical round-trip (SHA-256) for both containers.
- MP4 is genuinely decodable, not merely parseable: `ffprobe` reports
  `h264 (Constrained Baseline), yuv420p(progressive), 320x240`, and the frame decodes
  to a real image.
- Edge cases covered: empty payload, 3 MB non-zip binary, UTF-8 file names,
  payloads above 4 GB (64-bit `largesize` box).
- Clean errors on a plain video with nothing embedded, a non-video file, and a
  truncated file.
