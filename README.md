# zip2mkv

Wrap any file inside a **valid video file** (MKV or MP4), and get it back byte for
byte.

The output is a real video: it has a one-second video track, so players, `ffprobe`,
`mediainfo` and `file(1)` all recognise it as genuine. The embedded file rides along
untouched — no re-compression, same SHA-256 on the way out.

Pure Python, **no dependencies**. Both containers are written by hand: no ffmpeg,
no MKVToolNix.

> This is encapsulation, **not encryption**. Anyone with this script — or with
> MKVToolNix — can pull the file straight back out. Do not treat it as protection.

## Usage

```bash
python3 zip2mkv.py pack        archive.zip            # -> archive.mkv
python3 zip2mkv.py pack --mp4  archive.zip            # -> archive.mp4
python3 zip2mkv.py pack        archive.zip clip.mp4   # extension picks the container
python3 zip2mkv.py attach      holiday.mkv archive.zip  # ride along with a real video
python3 zip2mkv.py info        clip.mp4               # list what is attached
python3 zip2mkv.py unpack      clip.mp4               # -> archive.zip
```

`unpack`, `info` and `attach` detect the container on their own — you never have to
say which one it is.

On Windows, `unpack.bat` is a drag-and-drop wrapper: drop a `.mkv` or `.mp4` on it and
the file comes back out. Packing is command line only. See `README.txt` for the
end-user instructions shipped alongside it.

Despite the name, it is not limited to `.zip` — any file works. Only the MIME label
stored next to it says "zip".

## How it works

| | MKV | MP4 |
|---|---|---|
| Payload location | `Attachments` → `AttachedFile` | top-level `free` box |
| Why it is ignored | official Matroska attachment mechanism | ISO/IEC 14496-12 defines `free` contents as meaningless and skippable |
| Video track | one MJPEG frame | one H.264 keyframe (Constrained Baseline) |

**MKV** uses the documented Matroska attachment element, so `mkvextract` can recover
the file even without this script.

**MP4** has no attachment concept, so the payload goes into a top-level `free` box.
The box carries a small header (`ZIP2VID\0`, version, name, MIME type, length) so
`unpack` finds it unambiguously. The rest is a minimal but complete ISOBMFF tree —
`ftyp` / `free` / `mdat` / `moov`, with `stco` written last since it holds an absolute
file offset.

Both video frames are embedded as base64 constants, which is what keeps the script
dependency-free.

One MKV detail matters more than it looks: the `Attachments` element is written
**before the first Cluster**. Parsers stop reading header-level elements as soon as they hit a
Cluster, so an attachment placed after it is invisible to `ffmpeg` and `mkvextract` —
this script would still find it, since it walks the whole file, but nothing else
would. Verified with `ffmpeg -dump_attachment`, which pulls the file back out
byte-exact.

## Attaching to a video you already have

`pack` builds a throwaway one-second carrier. `attach` takes a video you already have
and rides along with it, so the result is an ordinary video that plays normally:

```bash
python3 zip2mkv.py attach holiday.mkv archive.zip   # -> holiday_avec_archive.mkv
python3 zip2mkv.py attach clip.mp4    archive.zip   # works on MP4 too
```

Nothing already in the file moves, which is what keeps the video intact:

- **MKV** — the `Attachments` element is appended at the end of the Segment's data and
  only the Segment size field is rewritten. Cue and SeekHead positions are stored
  relative to the start of the Segment data, so they stay correct even if that size
  field grows a byte.
- **MP4** — the `free` box is appended at the very end. `stco`/`co64` hold absolute
  file offsets, so appending is the one edit that cannot invalidate them.

Large videos are streamed, never read into memory.

Verified: after attaching, the decoded video and audio streams are **bit-identical**
to the original (same SHA-256 on the raw decoded output), and the payload comes back
byte-exact.

The trade-off, for MKV: an attachment added this way lands *after* the first Cluster,
so only this script will find it. If you need `mkvextract` to see it too, use `pack`.

## Caveat: re-encoding destroys the payload

If the file goes through anything that re-encodes or remuxes it — a video host, a
messaging app that compresses videos, an editor — the attachment or the `free` box is
dropped and the embedded file is gone. Only the untouched original unpacks.

A transcoded carrier is easy to recognise: it collapses to a couple of kilobytes,
because all that is left is one second of a still image.

## Verified

- Byte-identical round-trip for both containers, checked by SHA-256.
- `ffmpeg -dump_attachment` recovers the file from a `pack`-built MKV byte-exact,
  confirming the attachment is where standard tools look for it.
- The MP4 is genuinely decodable, not merely parseable: its frame decodes to a real
  image.
- After `attach`, decoded video and audio are bit-identical to the source video, for
  both containers.
- Files written by the older element ordering still unpack.
- Clean errors on a plain video with nothing embedded and on a non-video file.
