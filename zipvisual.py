#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zipvisual.py - Store a file in the *picture* of a video, so it survives being
re-encoded by a video host.

Why this exists
---------------
zip2video.py hides a file in the container (an MKV attachment, or an MP4 'free'
box). That is compact and lossless, but a video host that transcodes decodes the
video to raw pixels and re-encodes it: everything that is not picture or sound is
discarded, and the payload is gone.

This script takes the opposite approach. The file is drawn *into the frames*, as
a grid of black and white blocks. Pixels are the one thing a host is trying to
preserve, so the data comes back.

How it survives
---------------
Measured against a realistic transcode (1080p source -> 720p @ 2.1 Mbps H.264):

  * With 12x12 pixel blocks, surviving frames come back BIT-PERFECT. Blocks that
    large are far coarser than the codec's quantisation, so nothing is lost.
  * The real damage is whole frames being DROPPED - a host converting 30 fps to
    25 fps silently deletes one frame in six. This is why the video is written at
    24 fps: a host retargeting to 25, 30 or 60 fps then DUPLICATES frames, which
    costs nothing (duplicates are deduped by shard id), instead of deleting them.

So there is no need for error correction inside a frame. What is needed is:
  1. every frame says which shard it carries, and carries a CRC32, so a damaged
     or missing frame is a clean erasure rather than silent corruption;
  2. Reed-Solomon erasure coding across frames, so lost frames are rebuilt.

Layout
------
  Frame       160x90 blocks, upscaled to 1920x1080 -> 14400 bits = 1800 bytes.
  Frame head  magic(2) version(1) shard_id(3) crc32(4)  = 10 bytes
  Payload     1790 bytes per frame.
  Coding      Reed-Solomon over GF(256), systematic, K data + M parity shards
              per group (default 180 + 54, i.e. 30% parity). Any K of the 234
              rebuild the group, so up to 23% of frames can vanish.
  Interleave  consecutive frames belong to different groups, so a burst of lost
              frames is spread thinly instead of wiping one group out.
  Manifest    file name, size and coding parameters, repeated throughout the
              video so it cannot be missed.

Requirements
------------
  numpy, and ffmpeg (either on PATH, or `pip install imageio-ffmpeg`).

Usage
-----
  python zipvisual.py pack     archive.zip  [carrier.mp4]
  python zipvisual.py unpack   carrier.mp4  [archive.zip]
  python zipvisual.py info     carrier.mp4
  python zipvisual.py selftest              (encode, simulate a host, decode)
"""

import os
import shutil
import struct
import subprocess
import sys
import zlib

try:
    import numpy as np
except ImportError:
    raise SystemExit("[ERROR] numpy is required:  pip install numpy")

# ---------------------------------------------------------------------------
# Geometry and coding parameters
# ---------------------------------------------------------------------------
OUT_W, OUT_H   = 1920, 1080       # the video is always written at 1080p
FPS            = 24

# Two grids, i.e. two block sizes. Measured: a block survives re-encoding as
# long as it is still roughly 8 pixels wide in whatever resolution the host
# outputs. Below that, H.264 quantisation genuinely destroys the information -
# no amount of clever sampling brings it back.
#
#   'normal'  160x90 blocks -> 12 px at 1080p, 8 px at 720p.  1790 B/frame.
#             Right choice when the host outputs 720p or better.
#   'robust'   80x45 blocks -> 24 px at 1080p, 8 px at 360p.   440 B/frame.
#             Four times less capacity, but survives a brutal downscale.
#
# The decoder does not need to be told which was used: it tries each grid and
# the frame CRC tells it when it guessed right.
GRIDS          = ((160, 90), (80, 45))
HEAD_BYTES     = 10

GRID_W, GRID_H = GRIDS[0]
FRAME_BYTES    = GRID_W * GRID_H // 8
SHARD_BYTES    = FRAME_BYTES - HEAD_BYTES


def _set_grid(w, h):
    """Select the block grid; frame and shard sizes follow from it."""
    global GRID_W, GRID_H, FRAME_BYTES, SHARD_BYTES
    GRID_W, GRID_H = w, h
    FRAME_BYTES = w * h // 8
    SHARD_BYTES = FRAME_BYTES - HEAD_BYTES

MAGIC          = 0x5A56           # 'ZV'
VERSION        = 1
MANIFEST_ID    = 0xFFFFFF         # shard_id value marking a manifest frame

K_DATA         = 180              # data shards per group
M_PARITY       = 54               # parity shards per group (30%)
MANIFEST_EVERY = 200              # emit a manifest frame this often
MANIFEST_LEAD  = 20               # manifest frames at the very start

BLACK, WHITE   = 16, 235          # limited-range safe levels


# ---------------------------------------------------------------------------
# GF(256) arithmetic, for Reed-Solomon erasure coding
# ---------------------------------------------------------------------------
def _build_gf():
    exp = np.zeros(512, dtype=np.uint8)
    log = np.zeros(256, dtype=np.uint8)
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D                      # primitive polynomial
    exp[255:510] = exp[0:255]
    return exp, log


_EXP, _LOG = _build_gf()

# Full 256x256 multiplication table: MUL[a, b] = a * b in GF(256).
_MUL = np.zeros((256, 256), dtype=np.uint8)
_a = np.arange(256, dtype=np.uint16)
for _b in range(1, 256):
    _nz = _a != 0
    _MUL[1:, _b] = _EXP[(_LOG[_a[_nz]].astype(np.uint16) + _LOG[_b]) % 255]
_MUL[0, :] = 0
_MUL[:, 0] = 0


def gf_inv(a):
    if a == 0:
        raise ZeroDivisionError("GF(256) inverse of 0")
    return int(_EXP[(255 - int(_LOG[a])) % 255])


def _cauchy_matrix(k, m):
    """Systematic (k+m) x k encoding matrix: identity on top, Cauchy below.

    Every k x k submatrix of a Cauchy matrix is invertible, which is exactly the
    property that lets any k surviving shards rebuild the group."""
    mat = np.zeros((k + m, k), dtype=np.uint8)
    mat[:k] = np.eye(k, dtype=np.uint8)
    for i in range(m):
        for j in range(k):
            mat[k + i, j] = gf_inv((k + i) ^ j)
    return mat


def _mat_mul_shards(rows, shards):
    """Multiply a coefficient matrix by a stack of shards, over GF(256).

    rows: (R, K) uint8, shards: (K, S) uint8 -> (R, S) uint8."""
    out = np.zeros((rows.shape[0], shards.shape[1]), dtype=np.uint8)
    for i in range(rows.shape[0]):
        acc = out[i]
        for j in range(rows.shape[1]):
            c = rows[i, j]
            if c:
                acc ^= _MUL[c][shards[j]]
    return out


def _invert(mat):
    """Invert a square GF(256) matrix by Gauss-Jordan elimination."""
    n = mat.shape[0]
    a = mat.copy()
    inv = np.eye(n, dtype=np.uint8)
    for col in range(n):
        piv = None
        for r in range(col, n):
            if a[r, col]:
                piv = r
                break
        if piv is None:
            raise SystemExit("[ERROR] singular matrix - cannot rebuild data")
        if piv != col:
            a[[col, piv]] = a[[piv, col]]
            inv[[col, piv]] = inv[[piv, col]]
        ic = gf_inv(int(a[col, col]))
        a[col] = _MUL[ic][a[col]]
        inv[col] = _MUL[ic][inv[col]]
        nz = np.nonzero(a[:, col])[0]
        for r in nz:
            if r == col:
                continue
            f = int(a[r, col])
            a[r] ^= _MUL[f][a[col]]
            inv[r] ^= _MUL[f][inv[col]]
    return inv


# ---------------------------------------------------------------------------
# ffmpeg discovery
# ---------------------------------------------------------------------------
def _ffmpeg():
    exe = shutil.which('ffmpeg')
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    raise SystemExit(
        "[ERROR] ffmpeg not found.\n"
        "        Install it and put it on PATH, or:  pip install imageio-ffmpeg")


# ---------------------------------------------------------------------------
# Frame encoding / decoding
# ---------------------------------------------------------------------------
def _make_frame(shard_id, payload):
    """Build one frame's 1800 bytes: header + payload + CRC."""
    body = struct.pack('>H', MAGIC) + bytes([VERSION]) + \
        shard_id.to_bytes(3, 'big')
    padded = payload + b'\x00' * (SHARD_BYTES - len(payload))
    crc = zlib.crc32(body + padded) & 0xFFFFFFFF
    return body + struct.pack('>I', crc) + padded


def _parse_frame(buf):
    """Return (shard_id, payload) if the frame is intact, else None."""
    if len(buf) != FRAME_BYTES:
        return None
    if struct.unpack('>H', buf[0:2])[0] != MAGIC or buf[2] != VERSION:
        return None
    shard_id = int.from_bytes(buf[3:6], 'big')
    crc = struct.unpack('>I', buf[6:10])[0]
    if zlib.crc32(buf[0:6] + buf[10:]) & 0xFFFFFFFF != crc:
        return None
    return shard_id, buf[10:]


def _frames_to_pixels(frames):
    """Turn a list of 1800-byte frames into raw GRID_W x GRID_H gray pixels."""
    arr = np.frombuffer(b''.join(frames), dtype=np.uint8)
    bits = np.unpackbits(arr).reshape(len(frames), GRID_H, GRID_W)
    return np.where(bits == 1, WHITE, BLACK).astype(np.uint8)


def _build_manifest(name, size, file_crc, ngroups):
    name_b = name.encode('utf-8')[:200]
    return (bytes([VERSION]) +
            struct.pack('>Q', size) +
            struct.pack('>I', file_crc) +
            struct.pack('>HH', K_DATA, M_PARITY) +
            struct.pack('>I', ngroups) +
            struct.pack('>H', SHARD_BYTES) +
            struct.pack('>H', len(name_b)) + name_b)


def _parse_manifest(payload):
    try:
        off = 1
        size = struct.unpack('>Q', payload[off:off + 8])[0]; off += 8
        file_crc = struct.unpack('>I', payload[off:off + 4])[0]; off += 4
        k, m = struct.unpack('>HH', payload[off:off + 4]); off += 4
        ngroups = struct.unpack('>I', payload[off:off + 4])[0]; off += 4
        shard_bytes = struct.unpack('>H', payload[off:off + 2])[0]; off += 2
        nlen = struct.unpack('>H', payload[off:off + 2])[0]; off += 2
        name = payload[off:off + nlen].decode('utf-8', 'replace')
        return dict(size=size, file_crc=file_crc, k=k, m=m, ngroups=ngroups,
                    shard_bytes=shard_bytes, name=name)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Pack
# ---------------------------------------------------------------------------
def pack(src_path, out_path):
    with open(src_path, 'rb') as f:
        data = f.read()
    name = os.path.basename(src_path)
    file_crc = zlib.crc32(data) & 0xFFFFFFFF

    nshards = max(1, (len(data) + SHARD_BYTES - 1) // SHARD_BYTES)
    ngroups = (nshards + K_DATA - 1) // K_DATA
    padded = data + b'\x00' * (ngroups * K_DATA * SHARD_BYTES - len(data))
    shards = np.frombuffer(padded, dtype=np.uint8).reshape(
        ngroups, K_DATA, SHARD_BYTES)

    print("[..] {} -> {} data shards in {} group(s), {}% parity".format(
        name, nshards, ngroups, round(100 * M_PARITY / K_DATA)))

    mat = _cauchy_matrix(K_DATA, M_PARITY)
    parity = np.zeros((ngroups, M_PARITY, SHARD_BYTES), dtype=np.uint8)
    for g in range(ngroups):
        parity[g] = _mat_mul_shards(mat[K_DATA:], shards[g])
        if ngroups > 1:
            print("\r     parity {}/{}".format(g + 1, ngroups), end='', flush=True)
    if ngroups > 1:
        print()

    manifest = _make_frame(MANIFEST_ID,
                           _build_manifest(name, len(data), file_crc, ngroups))

    total = ngroups * (K_DATA + M_PARITY)
    ff = _ffmpeg()
    proc = subprocess.Popen(
        [ff, '-y', '-hide_banner', '-loglevel', 'error',
         '-f', 'rawvideo', '-pix_fmt', 'gray',
         '-s', '{}x{}'.format(GRID_W, GRID_H), '-r', str(FPS), '-i', '-',
         '-vf', 'scale={}:{}:flags=neighbor'.format(OUT_W, OUT_H),
         '-pix_fmt', 'yuv420p', '-c:v', 'libx264', '-crf', '18',
         '-preset', 'veryfast', out_path],
        stdin=subprocess.PIPE)

    def emit(batch):
        proc.stdin.write(_frames_to_pixels(batch).tobytes())

    batch = [manifest] * MANIFEST_LEAD
    written = 0
    # Interleave: walk shard slot by shard slot, across all groups, so that
    # consecutive frames never belong to the same group.
    for t in range(K_DATA + M_PARITY):
        for g in range(ngroups):
            payload = shards[g, t] if t < K_DATA else parity[g, t - K_DATA]
            batch.append(_make_frame(g * (K_DATA + M_PARITY) + t,
                                     payload.tobytes()))
            written += 1
            if written % MANIFEST_EVERY == 0:
                batch.append(manifest)
            if len(batch) >= 256:
                emit(batch); batch = []
            if written % 512 == 0:
                print("\r     frames {}/{}".format(written, total),
                      end='', flush=True)
    batch += [manifest] * MANIFEST_LEAD
    if batch:
        emit(batch)
    print("\r     frames {}/{}".format(written, total))

    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit("[ERROR] ffmpeg failed while encoding")

    nframes = written + MANIFEST_LEAD * 2 + written // MANIFEST_EVERY
    print("[OK] video written: {}".format(out_path))
    print("     payload      : {} ({} bytes)".format(name, len(data)))
    print("     video        : {} frames, {:.1f} s, {:.1f} MB".format(
        nframes, nframes / FPS, os.path.getsize(out_path) / 1e6))
    print("     tolerates    : up to {}% of frames lost".format(
        round(100 * M_PARITY / (K_DATA + M_PARITY))))


# ---------------------------------------------------------------------------
# Unpack
# ---------------------------------------------------------------------------
def _read_frames(video_path):
    """Decode the video and yield each frame's raw bytes for the current grid.

    Each block is reduced to one sample by area-averaging, which is measurably
    steadier than picking a single pixel when the host's resolution is not an
    exact multiple of the grid. The crop first undoes any black bars the host
    may have added, so a change of aspect ratio does not shift the grid."""
    ff = _ffmpeg()
    vf = 'crop=min(iw\\,ih*16/9):min(ih\\,iw*9/16),' \
         'scale={}:{}:flags=area'.format(GRID_W, GRID_H)
    proc = subprocess.Popen(
        [ff, '-hide_banner', '-loglevel', 'error', '-i', video_path,
         '-vf', vf, '-f', 'rawvideo', '-pix_fmt', 'gray', '-'],
        stdout=subprocess.PIPE)
    px = GRID_W * GRID_H
    while True:
        raw = proc.stdout.read(px)
        if len(raw) < px:
            break
        bits = (np.frombuffer(raw, dtype=np.uint8) > 128).astype(np.uint8)
        yield np.packbits(bits).tobytes()
    proc.stdout.close()
    proc.wait()


def _collect_one(video_path):
    """Read the video once, at whatever grid is currently selected."""
    manifest, shards, nframes, bad = None, {}, 0, 0
    for buf in _read_frames(video_path):
        nframes += 1
        got = _parse_frame(buf)
        if got is None:
            bad += 1
            continue
        sid, payload = got
        if sid == MANIFEST_ID:
            if manifest is None:
                manifest = _parse_manifest(payload)
        elif sid not in shards:
            shards[sid] = payload
    return manifest, shards, nframes, bad


def _collect(video_path):
    """Try each grid in turn and keep the one that actually reads.

    The frame CRC makes this unambiguous: a wrong grid yields no valid frame at
    all, so there is no risk of half-decoding with the wrong geometry."""
    best = (None, {}, 0, 0)
    for (w, h) in GRIDS:
        _set_grid(w, h)
        man, shards, nframes, bad = _collect_one(video_path)
        if man is not None and shards:
            if len(GRIDS) > 1 and (w, h) != GRIDS[0]:
                print("[..] grid {}x{} detected".format(w, h))
            return man, shards, nframes, bad
        if nframes > best[2]:
            best = (man, shards, nframes, bad)
    _set_grid(*GRIDS[0])
    return best


def unpack(video_path, out_path=None):
    man, shards, nframes, bad = _collect(video_path)
    if man is None:
        raise SystemExit(
            "[ERROR] no manifest found - this video was not made by zipvisual,\n"
            "        or it is too damaged to read.")
    k, m, ngroups = man['k'], man['m'], man['ngroups']
    if man['shard_bytes'] != SHARD_BYTES:
        raise SystemExit("[ERROR] unsupported shard size {}".format(man['shard_bytes']))

    print("[..] {} frames read, {} unreadable, {} shards recovered".format(
        nframes, bad, len(shards)))

    mat = _cauchy_matrix(k, m)
    out = bytearray()
    for g in range(ngroups):
        base = g * (k + m)
        have = [(t, shards[base + t]) for t in range(k + m) if base + t in shards]
        if len(have) < k:
            raise SystemExit(
                "[ERROR] group {} has only {}/{} shards - too many frames lost.\n"
                "        Nothing can be rebuilt from this file.".format(
                    g, len(have), k))
        use = have[:k]
        idx = [t for t, _ in use]
        block = np.stack([np.frombuffer(p, dtype=np.uint8) for _, p in use])
        if idx == list(range(k)):
            rebuilt = block                       # nothing lost, no maths needed
        else:
            rebuilt = _mat_mul_shards(_invert(mat[idx]), block)
        out += rebuilt.tobytes()
        if ngroups > 1:
            print("\r     group {}/{}".format(g + 1, ngroups), end='', flush=True)
    if ngroups > 1:
        print()

    data = bytes(out[:man['size']])
    crc = zlib.crc32(data) & 0xFFFFFFFF
    if crc != man['file_crc']:
        raise SystemExit(
            "[ERROR] checksum mismatch - the recovered file is corrupt.\n"
            "        expected {:08x}, got {:08x}".format(man['file_crc'], crc))

    if out_path is None:
        out_path = man['name'] or 'output.bin'
    with open(out_path, 'wb') as f:
        f.write(data)
    print("[OK] extracted: {} ({} bytes)".format(out_path, len(data)))
    print("     CRC32 verified: {:08x}".format(crc))


def info(video_path):
    man, shards, nframes, bad = _collect(video_path)
    if man is None:
        print("No zipvisual manifest found in {}".format(video_path))
        return
    k, m, ng = man['k'], man['m'], man['ngroups']
    print("zipvisual payload in {}".format(video_path))
    print("  name        : {}".format(man['name']))
    print("  size        : {} bytes".format(man['size']))
    print("  crc32       : {:08x}".format(man['file_crc']))
    print("  coding      : {} data + {} parity per group, {} group(s)".format(k, m, ng))
    print("  frames      : {} read, {} unreadable".format(nframes, bad))
    print("  shards      : {} of {} needed".format(len(shards), ng * k))
    worst = min((sum(1 for t in range(k + m) if g * (k + m) + t in shards)
                 for g in range(ng)), default=0)
    print("  worst group : {}/{} shards ({})".format(
        worst, k, "recoverable" if worst >= k else "NOT recoverable"))


# ---------------------------------------------------------------------------
# Self-test: encode, simulate a hostile video host, decode, compare
# ---------------------------------------------------------------------------
def selftest(size=400000):
    import hashlib
    import tempfile
    ff = _ffmpeg()
    d = tempfile.mkdtemp(prefix='zipvisual_')
    src = os.path.join(d, 'payload.bin')
    car = os.path.join(d, 'carrier.mp4')
    hosted = os.path.join(d, 'hosted.mp4')
    got = os.path.join(d, 'recovered.bin')

    rng = np.random.default_rng(0)
    with open(src, 'wb') as f:
        f.write(rng.integers(0, 256, size=size, dtype=np.uint8).tobytes())
    want = hashlib.sha256(open(src, 'rb').read()).hexdigest()

    print("=== 1. pack ===")
    pack(src, car)

    print("=== 2. simulate a video host (720p @ 2.1 Mbps, 30 -> 25 fps) ===")
    subprocess.run([ff, '-y', '-hide_banner', '-loglevel', 'error', '-i', car,
                    '-vf', 'scale=1280:720', '-r', '25', '-c:v', 'libx264',
                    '-b:v', '2160k', '-preset', 'fast', '-pix_fmt', 'yuv420p',
                    hosted], check=True)
    print("     {:.1f} MB -> {:.1f} MB".format(
        os.path.getsize(car) / 1e6, os.path.getsize(hosted) / 1e6))

    print("=== 3. unpack from the transcoded copy ===")
    unpack(hosted, got)

    have = hashlib.sha256(open(got, 'rb').read()).hexdigest()
    print("=== 4. verdict ===")
    print("     original  {}".format(want))
    print("     recovered {}".format(have))
    ok = want == have
    print("     {}".format("PASS - byte for byte identical" if ok else "FAIL"))
    shutil.rmtree(d, ignore_errors=True)
    return 0 if ok else 1


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip())
        return 1
    cmd = argv[1]
    if cmd == 'pack':
        rest = argv[2:]
        if '--robust' in rest:
            rest = [a for a in rest if a != '--robust']
            _set_grid(*GRIDS[1])
        if not rest:
            print(__doc__.strip()); return 1
        src = rest[0]
        out = rest[1] if len(rest) > 1 else \
            os.path.splitext(src)[0] + '_visual.mp4'
        pack(src, out)
    elif cmd == 'unpack':
        if len(argv) < 3:
            print(__doc__.strip()); return 1
        unpack(argv[2], argv[3] if len(argv) > 3 else None)
    elif cmd == 'info':
        if len(argv) < 3:
            print(__doc__.strip()); return 1
        info(argv[2])
    elif cmd == 'selftest':
        return selftest(int(argv[2]) if len(argv) > 2 else 400000)
    else:
        print("Unknown command: {}\n".format(cmd))
        print(__doc__.strip())
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
