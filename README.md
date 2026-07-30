# zip2mkv

Wrap any file inside a **valid MKV video**, and get it back byte for byte.

The output is a real video: it has a one-second video track, so players, `ffprobe`,
`mediainfo` and `file(1)` all recognise it as genuine. The embedded file rides along
untouched — no re-compression, same SHA-256 on the way out.

Pure Python, **no dependencies**. The Matroska container is written by hand in EBML:
no ffmpeg, no MKVToolNix.

> This is encapsulation, **not encryption**. Anyone with this script — or with
> MKVToolNix — can pull the file straight back out. Do not treat it as protection.

## Usage

```bash
python3 zip2mkv.py pack   archive.zip                 # -> archive.mkv
python3 zip2mkv.py attach holiday.mkv archive.zip     # ride along with a real video
python3 zip2mkv.py info   archive.mkv                 # list what is attached
python3 zip2mkv.py unpack archive.mkv                 # -> archive.zip
```

On Windows, `unpack.bat` is a drag-and-drop wrapper: drop a `.mkv` on it and the
file comes back out. Packing is command line only. See `README.txt` for the
end-user instructions shipped alongside it.

Despite the name, it is not limited to `.zip` — any file works. Only the MIME label
stored next to it says "zip".

## How it works

The `.zip` is stored as a Matroska **attachment** (`Attachments` → `AttachedFile`),
the official Matroska mechanism for embedding an arbitrary binary file. The video
track is a single MJPEG frame, embedded as a base64 constant, which is what keeps the
script dependency-free.

One detail matters more than it looks: the `Attachments` element is written **before
the first Cluster**. Parsers stop reading header-level elements as soon as they hit a
Cluster, so an attachment placed after it is invisible to `ffmpeg` and `mkvextract` —
this script would still find it, since it walks the whole file, but nothing else
would. Verified with `ffmpeg -dump_attachment`, which pulls the file back out
byte-exact.

## Attaching to a video you already have

`pack` builds a throwaway one-second carrier. `attach` takes a video you already have
and rides along with it, so the result is an ordinary video that plays normally:

```bash
python3 zip2mkv.py attach holiday.mkv archive.zip   # -> holiday_avec_archive.mkv
```

Nothing already in the file moves. The `Attachments` element is appended at the end of
the Segment's data and only the Segment size field is rewritten — Cue and SeekHead
positions are stored relative to the start of the Segment data, so they stay correct
even if that size field grows a byte. Large videos are streamed, never read into
memory.

Verified: after attaching, the decoded video and audio streams are **bit-identical**
to the original (same SHA-256 on the raw decoded output), and the payload comes back
byte-exact.

The trade-off: an attachment added this way lands *after* the first Cluster, so only
this script will find it. If you need `mkvextract` to see it too, use `pack`.

## Caveat: re-encoding destroys the payload

If the file goes through anything that re-encodes or remuxes it — a video host, a
messaging app that compresses videos, an editor — the attachment is dropped and the
embedded file is gone. Only the untouched original unpacks.

A transcoded carrier is easy to recognise: it collapses to a couple of kilobytes,
because all that is left is one second of a still image.

## Verified

- Byte-identical round-trip, checked by SHA-256.
- `ffmpeg -dump_attachment` recovers the file from a `pack`-built MKV byte-exact,
  confirming the attachment is where standard tools look for it.
- After `attach`, decoded video and audio are bit-identical to the source video.
- Files written by the older element ordering still unpack.
