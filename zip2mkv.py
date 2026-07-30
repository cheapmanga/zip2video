#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zip2mkv.py - Wrap a .zip file inside a valid video container (MKV or MP4),
and extract it back bit-for-bit.

No external dependency: both containers are built by hand. No ffmpeg, no
MKVToolNix required.

How it works
------------
The generated file holds two things:
  1. A real video track (one MJPEG frame) -> the file is recognised as a valid
     video by players, mediainfo, ffprobe, the `file` command, etc.
  2. The .zip stored as-is, no re-compression: it comes back strictly identical
     to the original.

Storage differs per container:
  MKV  a Matroska attachment (AttachedFile element), the format's official
       mechanism for embedding an arbitrary binary file.
  MP4  MP4 has no attachment concept, so the .zip goes into a top-level 'free'
       box, which the spec explicitly defines as skippable.

Encryption
----------
The payload is encrypted. For pack and attach, the key is read from key/key.txt
if that file exists, otherwise it is asked for at the keyboard. For unpack the
key is ALWAYS asked for at the keyboard (key.txt is ignored). Outputs go into
pack/ and unpack/, created as needed.

Usage
-----
  # Pack a zip (the output extension picks the container):
  python3 zip2mkv.py pack        my_archive.zip          (-> pack/my_archive.mkv)
  python3 zip2mkv.py pack --mp4  my_archive.zip          (-> pack/my_archive.mp4)

  # Or attach it to a video you already have, which keeps playing normally:
  python3 zip2mkv.py attach my_video.mkv  my_archive.zip  [output.mkv]

  # Extract the zip back (the container is detected automatically):
  python3 zip2mkv.py unpack output.mkv      [my_archive.zip]

  # Show what is embedded:
  python3 zip2mkv.py info   output.mkv

If the output name is omitted, it is derived from the input name.
"""

import base64
import hashlib
import hmac
import os
import secrets
import struct
import sys

# ---------------------------------------------------------------------------
# Configuration: key, input/output folders
# ---------------------------------------------------------------------------
# Everything is resolved relative to the script's folder, not the current
# directory, so Windows drag-and-drop works wherever it is launched from.
KEY_FILE   = os.path.join('key', 'key.txt')
PACK_DIR   = 'pack'
UNPACK_DIR = 'unpack'

# Header of an encrypted payload, written in front of the ciphertext:
#   magic 8 | salt 16 | nonce 16 | mac 32   then the ciphertext
ENC_MAGIC   = b'Z2KENC\x00\x01'
_SALT_LEN   = 16
_NONCE_LEN  = 16
_MAC_LEN    = 32
_PBKDF2_ITER = 200_000
_KS_BLOCK    = 1 << 16           # 64 KiB of keystream per SHAKE256 call


def _base_dir():
    """The script's folder, where key/, pack/ and unpack/ are resolved."""
    return os.path.dirname(os.path.abspath(__file__))


def _key_from_file():
    """Return the key from key/key.txt, or None if the file is missing/empty.

    The first non-empty line that is not a comment (#) is taken, so the file can
    be commented without breaking the read."""
    path = os.path.join(_base_dir(), KEY_FILE)
    if not os.path.isfile(path):
        return None
    with open(path, 'rb') as f:
        for raw in f:
            line = raw.strip()
            if line and not line.startswith(b'#'):
                return line
    return None


def _prompt_key(prompt):
    """Ask for a key at the keyboard, without echoing it (like a password)."""
    import getpass
    try:
        entered = getpass.getpass(prompt)
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("\n[ERROR] No key provided.")
    key = entered.strip().encode('utf-8')
    if not key:
        raise SystemExit("[ERROR] Empty key.")
    return key


def key_for_pack():
    """Key for pack/attach: key/key.txt if it exists, otherwise ask for it."""
    return _key_from_file() or _prompt_key("Encryption key: ")


def key_for_unpack():
    """Key for unpack: always asked at the keyboard, key.txt is ignored."""
    return _prompt_key("Decryption key: ")


def _derive(key, salt):
    """Derive separate encryption and authentication keys."""
    dk = hashlib.pbkdf2_hmac('sha256', key, salt, _PBKDF2_ITER, dklen=64)
    return dk[:32], dk[32:]


def _keystream_xor(enc_key, nonce, data):
    """XOR data with a SHAKE256 keystream in counter mode.

    SHAKE256 is an extendable-output function: called with (key, nonce, counter)
    it yields a pseudo-random stream. The counter is part of each block's input,
    so no keystream block ever repeats."""
    out = bytearray(len(data))
    for i in range(0, len(data), _KS_BLOCK):
        chunk = data[i:i + _KS_BLOCK]
        block = hashlib.shake_256(
            enc_key + nonce + (i // _KS_BLOCK).to_bytes(8, 'big')
        ).digest(len(chunk))
        out[i:i + len(chunk)] = bytes(a ^ b for a, b in zip(chunk, block))
    return bytes(out)


def encrypt(data, key):
    """Encrypt data. Returns magic + salt + nonce + mac + ciphertext."""
    salt = secrets.token_bytes(_SALT_LEN)
    nonce = secrets.token_bytes(_NONCE_LEN)
    enc_key, mac_key = _derive(key, salt)
    cipher = _keystream_xor(enc_key, nonce, data)
    # Encrypt-then-MAC: the MAC covers the header AND the ciphertext, so any
    # tampering is caught before we decrypt anything.
    mac = hmac.new(mac_key, ENC_MAGIC + salt + nonce + cipher, 'sha256').digest()
    return ENC_MAGIC + salt + nonce + mac + cipher


def is_encrypted(blob):
    return blob[:len(ENC_MAGIC)] == ENC_MAGIC


def decrypt(blob, key):
    """Decrypt a blob produced by encrypt(). Checks the MAC first."""
    head = len(ENC_MAGIC) + _SALT_LEN + _NONCE_LEN + _MAC_LEN
    if len(blob) < head:
        raise SystemExit("[ERROR] Encrypted payload is truncated.")
    off = len(ENC_MAGIC)
    salt = blob[off:off + _SALT_LEN]; off += _SALT_LEN
    nonce = blob[off:off + _NONCE_LEN]; off += _NONCE_LEN
    mac = blob[off:off + _MAC_LEN]; off += _MAC_LEN
    cipher = blob[off:]

    enc_key, mac_key = _derive(key, salt)
    expected = hmac.new(mac_key, ENC_MAGIC + salt + nonce + cipher,
                        'sha256').digest()
    if not hmac.compare_digest(mac, expected):
        raise SystemExit(
            "[ERROR] Verification failed: wrong key, or the file was altered.\n"
            "        Nothing was written. Refusing to output bogus bytes.")
    return _keystream_xor(enc_key, nonce, cipher)


def _output_path(dirname, filename):
    """Return the path inside dirname, creating the folder if needed."""
    folder = os.path.join(_base_dir(), dirname)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, os.path.basename(filename))


# ---------------------------------------------------------------------------
# EBML/Matroska element IDs (raw bytes, VINT marker included)
# ---------------------------------------------------------------------------
EBML                 = b'\x1A\x45\xDF\xA3'
EBML_VERSION         = b'\x42\x86'
EBML_READ_VERSION    = b'\x42\xF7'
EBML_MAX_ID_LENGTH   = b'\x42\xF2'
EBML_MAX_SIZE_LENGTH = b'\x42\xF3'
DOC_TYPE             = b'\x42\x82'
DOC_TYPE_VERSION     = b'\x42\x87'
DOC_TYPE_READ_VER    = b'\x42\x85'

SEGMENT              = b'\x18\x53\x80\x67'

INFO                 = b'\x15\x49\xA9\x66'
TIMESTAMP_SCALE      = b'\x2A\xD7\xB1'
DURATION             = b'\x44\x89'
MUXING_APP           = b'\x4D\x80'
WRITING_APP          = b'\x57\x41'

TRACKS               = b'\x16\x54\xAE\x6B'
TRACK_ENTRY          = b'\xAE'
TRACK_NUMBER         = b'\xD7'
TRACK_UID            = b'\x73\xC5'
TRACK_TYPE           = b'\x83'
FLAG_LACING          = b'\x9C'
CODEC_ID             = b'\x86'
VIDEO                = b'\xE0'
PIXEL_WIDTH          = b'\xB0'
PIXEL_HEIGHT         = b'\xBA'

CLUSTER              = b'\x1F\x43\xB6\x75'
TIMESTAMP            = b'\xE7'
SIMPLE_BLOCK         = b'\xA3'

ATTACHMENTS          = b'\x19\x41\xA4\x69'
ATTACHED_FILE        = b'\x61\xA7'
FILE_DESCRIPTION     = b'\x46\x7E'
FILE_NAME            = b'\x46\x6E'
FILE_MIME_TYPE       = b'\x46\x60'
FILE_DATA            = b'\x46\x5C'
FILE_UID             = b'\x46\xAE'

# A small valid 320x240 JPEG image, used as the single frame of the video track.
# Embedded inline so the script has no dependency.
_FRAME_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIf"
    "IiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7"
    "Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozv/wAARCADwAUADASIA"
    "AhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQA"
    "AAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3"
    "ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWm"
    "p6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEA"
    "AwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSEx"
    "BhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElK"
    "U1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3"
    "uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDzKiii"
    "tSSpRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt"
    "0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFF"
    "QMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViCiiigC3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUU"
    "UVAy3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWI"
    "qUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDCiiigC3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUV"
    "Ay3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqU"
    "UUVAy3RRRViCiiigCpRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy"
    "3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDCiiigC3RRRV"
    "iKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3R"
    "RRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViCiiigCpRRRUDLdFFFWIqUUUVAy3RRRViK"
    "lFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRR"
    "ViKlFFFQMt0UUVYipRRRUDCiiigC3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlF"
    "FFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRVi"
    "CiiigCpRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFF"
    "QMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDCiiigC3RRRViKlFFFQMt0U"
    "UVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQM"
    "t0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViCiiigCpRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUV"
    "YipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0"
    "UUVYipRRRUDCiiigC3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYi"
    "pRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UU"
    "VYipRRRUDLdFFFWIqUUUVAy3RRRViCiiigCpRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipR"
    "RRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVY"
    "ipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDCiiigC3RRRViKlFFFQMt0UUVYipRRR"
    "UDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYip"
    "RRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViCiiigCpRRRUD"
    "LdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRR"
    "RUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYi"
    "pRRRUDCiiigC3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRU"
    "DLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipR"
    "RRUDLdFFFWIqUUUVAy3RRRViCiiigCpRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDL"
    "dFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRR"
    "UDLdFFFWIqUUUVAy3RRRViKlFFFQMt0UUVYipRRRUDP/Z"
)


# ---------------------------------------------------------------------------
# EBML writing
# ---------------------------------------------------------------------------
def encode_size(n):
    """Encode a size as a Matroska VINT (Variable-length INTeger)."""
    for length in range(1, 9):
        limit = (1 << (7 * length)) - 1          # the all-ones value is reserved
        if n < limit:
            return (n | (1 << (7 * length))).to_bytes(length, 'big')
    raise ValueError("size too large for a VINT")


def elem(elem_id, data):
    """Build an EBML element: id + size + data."""
    return elem_id + encode_size(len(data)) + data


def _build_ebml_header():
    return elem(EBML,
        elem(EBML_VERSION,         b'\x01') +
        elem(EBML_READ_VERSION,    b'\x01') +
        elem(EBML_MAX_ID_LENGTH,   b'\x04') +
        elem(EBML_MAX_SIZE_LENGTH, b'\x08') +
        elem(DOC_TYPE,             b'matroska') +
        elem(DOC_TYPE_VERSION,     b'\x04') +
        elem(DOC_TYPE_READ_VER,    b'\x02'))


def _build_info():
    return elem(INFO,
        elem(TIMESTAMP_SCALE, (1_000_000).to_bytes(3, 'big')) +   # 1 ms
        elem(DURATION,        struct.pack('>d', 1000.0)) +        # ~1 s
        elem(MUXING_APP,      b'zip2mkv') +
        elem(WRITING_APP,     b'zip2mkv'))


def _build_tracks():
    video = elem(VIDEO,
        elem(PIXEL_WIDTH,  (320).to_bytes(2, 'big')) +
        elem(PIXEL_HEIGHT, (240).to_bytes(1, 'big')))
    entry = elem(TRACK_ENTRY,
        elem(TRACK_NUMBER, b'\x01') +
        elem(TRACK_UID,    b'\x00\x00\x00\x00\x00\x00\x00\x01') +
        elem(TRACK_TYPE,   b'\x01') +                             # 1 = video
        elem(FLAG_LACING,  b'\x00') +
        elem(CODEC_ID,     b'V_MJPEG') +
        video)
    return elem(TRACKS, entry)


def _build_cluster():
    # SimpleBlock: track number (VINT) + int16 timecode + flags + frame data
    block = b'\x81' + (0).to_bytes(2, 'big') + b'\x80' + _FRAME_JPEG   # 0x80 = keyframe
    return elem(CLUSTER,
        elem(TIMESTAMP, b'\x00') +
        elem(SIMPLE_BLOCK, block))


def _build_attachments(zip_bytes, filename):
    attached = elem(ATTACHED_FILE,
        elem(FILE_DESCRIPTION, "Embedded zip archive".encode('utf-8')) +
        elem(FILE_NAME,        filename.encode('utf-8')) +
        elem(FILE_MIME_TYPE,   b'application/zip') +
        elem(FILE_DATA,        zip_bytes) +
        elem(FILE_UID,         b'\x00\x00\x00\x00\x00\x00\x00\x02'))
    return elem(ATTACHMENTS, attached)


def _pack_mkv(zip_bytes, name):
    """Return the bytes of an MKV container embedding zip_bytes."""
    segment_body = (
        _build_info() +
        _build_tracks() +
        # Attachments MUST come before the first Cluster: parsers stop reading
        # header-level elements once they hit a Cluster, so an attachment placed
        # after it is invisible to ffmpeg and mkvextract (this script would still
        # find it, since it walks the whole file, but nothing else would).
        # Verified with `ffmpeg -dump_attachment`.
        _build_attachments(zip_bytes, name) +
        _build_cluster()
    )
    return _build_ebml_header() + elem(SEGMENT, segment_body)


def pack(zip_path, out_path):
    """Wrap zip_path into a valid video container (out_path).

    The payload is encrypted with the key from key/key.txt if that file exists,
    otherwise the key is asked for at the keyboard. The result is placed in the
    pack/ folder (created if missing).

    The output extension picks the container: .mp4/.m4v give an MP4, anything
    else gives an MKV."""
    _need_file(zip_path, "File to pack")
    key = key_for_pack()
    with open(zip_path, 'rb') as f:
        clear = f.read()
    name = os.path.basename(zip_path)
    payload = encrypt(clear, key)

    if os.path.splitext(out_path)[1].lower() in ('.mp4', '.m4v'):
        kind, data = 'MP4', _pack_mp4(payload, name)
    else:
        kind, data = 'MKV', _pack_mkv(payload, name)

    final = _output_path(PACK_DIR, out_path)
    with open(final, 'wb') as f:
        f.write(data)

    print("[OK] {} created: {}".format(kind, final))
    print("     embedded zip: {} ({} bytes clear)".format(name, len(clear)))
    print("     encrypted: yes ({} bytes once encrypted)".format(len(payload)))
    print("     final size: {} bytes".format(len(data)))


# ---------------------------------------------------------------------------
# EBML reading (extraction)
# ---------------------------------------------------------------------------
def _copy_stream(src, dst, length=None):
    """Copy bytes without loading the file into memory."""
    remaining = length
    while True:
        want = 1 << 20 if remaining is None else min(1 << 20, remaining)
        if want <= 0:
            return
        chunk = src.read(want)
        if not chunk:
            return
        dst.write(chunk)
        if remaining is not None:
            remaining -= len(chunk)


def _attach_mkv(video_path, zip_bytes, name, out_path):
    """Add an attachment to an existing MKV video without altering it.

    The Attachments element is appended at the end of the Segment's data, and
    only the Segment size field is rewritten. Positions stored in Cues and
    SeekHead are relative to the start of the Segment data, so they stay valid
    even if that size field grows a byte. Nothing already in the file moves.

    Trade-off: the attachment ends up after the first Cluster, so only this
    script will find it (see the comment in pack)."""
    attachments = _build_attachments(zip_bytes, name)

    with open(video_path, 'rb') as f:
        f.seek(0, os.SEEK_END)
        filesize = f.tell()
        f.seek(0)

        if _read_id(f) != EBML:
            raise SystemExit(
                "[ERROR] {} is not a Matroska file.".format(video_path))
        # In two steps: _read_size advances the cursor, so f.tell() must be read
        # AFTER, otherwise header_end is off by the size field's length.
        ebml_size = _read_size(f)
        header_end = f.tell() + ebml_size

        f.seek(header_end)
        if _read_id(f) != SEGMENT:
            raise SystemExit(
                "[ERROR] {}: expected a Segment after the EBML header.\n"
                "        This file is too unusual to be edited here."
                .format(video_path))
        size_pos = f.tell()
        seg_size = _read_size(f)            # None = unknown size
        seg_data_start = f.tell()
        old_vint_len = seg_data_start - size_pos

    if seg_size is None:
        # Unknown-size Segment: it runs to the end of the file, so appending the
        # attachments after it is enough.
        with open(video_path, 'rb') as src, open(out_path, 'wb') as dst:
            _copy_stream(src, dst)
            dst.write(attachments)
    else:
        seg_end = seg_data_start + seg_size
        if seg_end != filesize:
            raise SystemExit(
                "[ERROR] {}: the Segment does not run to the end of the file\n"
                "        ({} bytes follow it). Attaching would corrupt them."
                .format(video_path, filesize - seg_end))

        new_vint = encode_size(seg_size + len(attachments))
        # Keep the same field width when possible, so nothing shifts at all.
        if len(new_vint) < old_vint_len:
            marker = 1 << (7 * old_vint_len)
            new_vint = ((seg_size + len(attachments)) | marker).to_bytes(
                old_vint_len, 'big')

        with open(video_path, 'rb') as src, open(out_path, 'wb') as dst:
            _copy_stream(src, dst, size_pos)       # EBML header + Segment id
            dst.write(new_vint)
            src.seek(seg_data_start)
            _copy_stream(src, dst, seg_size)       # the original Segment content
            dst.write(attachments)


def _read_id(f):
    """Read an EBML element ID (raw bytes). Returns None at end of file."""
    first = f.read(1)
    if not first:
        return None
    b = first[0]
    length = 0
    for i in range(8):
        if b & (0x80 >> i):
            length = i + 1
            break
    if length == 0:
        raise ValueError("invalid EBML id")
    return first + f.read(length - 1)


def _read_size(f):
    """Read an EBML size (VINT). Returns None for unknown size."""
    first = f.read(1)
    if not first:
        return None
    b = first[0]
    length = 0
    for i in range(8):
        if b & (0x80 >> i):
            length = i + 1
            break
    value = b & (0xFF >> length)
    rest = f.read(length - 1)
    for byte in rest:
        value = (value << 8) | byte
    all_ones = (1 << (7 * length)) - 1
    if value == all_ones:
        return None                              # unknown size (not used here)
    return value


# "Master" elements we recurse into to find the attachments.
_MASTERS = {SEGMENT, ATTACHMENTS, ATTACHED_FILE}


def _iter_attachments(path):
    """Walk the MKV and return the list of attachments.

    Each attachment: dict(name, mime, offset, size) - offset/size point at the
    raw bytes of FILE_DATA (the content is not loaded into memory here)."""
    results = []

    with open(path, 'rb') as f:
        f.seek(0, os.SEEK_END)
        filesize = f.tell()

        def walk(start, end):
            f.seek(start)
            current = {}
            while f.tell() < end:
                elem_id = _read_id(f)
                if elem_id is None:
                    break
                size = _read_size(f)
                data_start = f.tell()
                if size is None:
                    break
                data_end = data_start + size

                if elem_id in (SEGMENT, ATTACHMENTS):
                    walk(data_start, data_end)
                elif elem_id == ATTACHED_FILE:
                    att = _read_attached_file(f, data_start, data_end)
                    results.append(att)
                # (other top-level elements are ignored)
                f.seek(data_end)

        def _read_attached_file(f, start, end):
            att = {'name': None, 'mime': None, 'offset': None, 'size': 0}
            f.seek(start)
            while f.tell() < end:
                elem_id = _read_id(f)
                if elem_id is None:
                    break
                size = _read_size(f)
                data_start = f.tell()
                if size is None:
                    break
                if elem_id == FILE_NAME:
                    att['name'] = f.read(size).decode('utf-8', 'replace')
                elif elem_id == FILE_MIME_TYPE:
                    att['mime'] = f.read(size).decode('utf-8', 'replace')
                elif elem_id == FILE_DATA:
                    att['offset'] = data_start
                    att['size'] = size
                f.seek(data_start + size)
            return att

        walk(0, filesize)

    return results


# ---------------------------------------------------------------------------
# MP4 / ISOBMFF
# ---------------------------------------------------------------------------
# MP4 has no equivalent of a Matroska attachment. The payload is therefore
# stored in a top-level 'free' box: ISO/IEC 14496-12 states that the contents of
# a 'free' box are meaningless and may be ignored, so every conformant parser
# skips over it without complaining.
#
# Contents of the 'free' box:
#     magic       8 bytes   b'ZIP2VID\x00'
#     version     1 byte
#     name_len    2 bytes   big endian, then the UTF-8 name
#     mime_len    2 bytes   big endian, then the UTF-8 MIME type
#     data_len    8 bytes   big endian
#     data        the embedded file, as-is
MP4_MAGIC = b'ZIP2VID\x00'
MP4_PAYLOAD_VERSION = 1

# A 320x240 H.264 keyframe (Constrained Baseline) in AVCC form (4-byte length
# prefix + NAL unit), and its AVCDecoderConfigurationRecord. Embedded inline so
# the script stays dependency-free.
_AVCC_RECORD = base64.b64decode(
    "AULAHv/hABlnQsAephEFB+wEQAAAAwBAAAADAIPFi4RgAQAGaMhCDyyA")
_FRAME_H264 = base64.b64decode(
    "AAAA82WIggK///+HooAAoW+Tk5OTk5OTk5OTk5OTk5OTk5OTrrrrrrrrrrrrrrrrrrrr"
    "rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr"
    "rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr"
    "rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr"
    "rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrwA==")

_MP4_WIDTH     = 320
_MP4_HEIGHT    = 240
_MP4_TIMESCALE = 1000            # 1 unit = 1 ms
_MP4_DURATION  = 1000            # ~1 s

# Identity transformation matrix (16.16 fixed point, last row 2.30).
_MP4_MATRIX = struct.pack('>9i', 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)


def _box(box_type, payload):
    """Build an ISOBMFF box: size + type + data."""
    size = len(payload) + 8
    if size <= 0xFFFFFFFF:
        return struct.pack('>I', size) + box_type + payload
    # Oversized box: the size field is 1 and a 64-bit size follows.
    return struct.pack('>I', 1) + box_type + struct.pack('>Q', size + 8) + payload


def _fullbox(box_type, version, flags, payload):
    """Build an ISOBMFF FullBox (box prefixed with version + 24-bit flags)."""
    return _box(box_type, struct.pack('>BBBB', version,
                                      (flags >> 16) & 0xFF,
                                      (flags >> 8) & 0xFF,
                                      flags & 0xFF) + payload)


def _build_mp4_payload_box(zip_bytes, name, mime='application/zip'):
    """Build the top-level 'free' box holding the file."""
    name_b = name.encode('utf-8')
    mime_b = mime.encode('utf-8')
    body = (MP4_MAGIC +
            bytes([MP4_PAYLOAD_VERSION]) +
            struct.pack('>H', len(name_b)) + name_b +
            struct.pack('>H', len(mime_b)) + mime_b +
            struct.pack('>Q', len(zip_bytes)) +
            zip_bytes)
    return _box(b'free', body)


def _build_mp4_stbl(chunk_offset):
    """Sample table. chunk_offset = absolute position of the mdat data."""
    compressor = b'\x00' * 32          # length-prefixed string, left empty
    avc1_body = (
        b'\x00' * 6 +                          # reserved
        struct.pack('>H', 1) +                 # data_reference_index
        b'\x00' * 2 + b'\x00' * 2 + b'\x00' * 12 +
        struct.pack('>HH', _MP4_WIDTH, _MP4_HEIGHT) +
        struct.pack('>II', 0x00480000, 0x00480000) +  # 72 dpi horizontal/vertical
        struct.pack('>I', 0) +                 # reserved
        struct.pack('>H', 1) +                 # frame_count
        compressor +
        struct.pack('>H', 0x0018) +            # depth
        struct.pack('>h', -1) +                # pre_defined
        _box(b'avcC', _AVCC_RECORD)
    )
    stsd = _fullbox(b'stsd', 0, 0,
                    struct.pack('>I', 1) + _box(b'avc1', avc1_body))
    stts = _fullbox(b'stts', 0, 0,
                    struct.pack('>I', 1) +
                    struct.pack('>II', 1, _MP4_DURATION))
    stsc = _fullbox(b'stsc', 0, 0,
                    struct.pack('>I', 1) +
                    struct.pack('>III', 1, 1, 1))
    stsz = _fullbox(b'stsz', 0, 0,
                    struct.pack('>II', len(_FRAME_H264), 1))
    stco = _fullbox(b'stco', 0, 0,
                    struct.pack('>I', 1) + struct.pack('>I', chunk_offset))
    return _box(b'stbl', stsd + stts + stsc + stsz + stco)


def _build_mp4_moov(chunk_offset):
    """Build the moov box describing the single-frame video track."""
    mvhd = _fullbox(b'mvhd', 0, 0,
                    struct.pack('>IIII', 0, 0, _MP4_TIMESCALE, _MP4_DURATION) +
                    struct.pack('>I', 0x00010000) +   # rate 1.0
                    struct.pack('>H', 0x0100) +       # volume 1.0
                    b'\x00' * 2 + b'\x00' * 8 +
                    _MP4_MATRIX +
                    b'\x00' * 24 +
                    struct.pack('>I', 2))             # next_track_ID

    tkhd = _fullbox(b'tkhd', 0, 0x000003,             # enabled | in movie
                    struct.pack('>IIII', 0, 0, 1, 0) +
                    struct.pack('>I', _MP4_DURATION) +
                    b'\x00' * 8 +
                    struct.pack('>hhh', 0, 0, 0) +    # layer, alt group, volume
                    b'\x00' * 2 +
                    _MP4_MATRIX +
                    struct.pack('>II', _MP4_WIDTH << 16, _MP4_HEIGHT << 16))

    mdhd = _fullbox(b'mdhd', 0, 0,
                    struct.pack('>IIII', 0, 0, _MP4_TIMESCALE, _MP4_DURATION) +
                    struct.pack('>H', 0x55C4) +       # language 'und'
                    struct.pack('>H', 0))

    hdlr = _fullbox(b'hdlr', 0, 0,
                    struct.pack('>I', 0) + b'vide' + b'\x00' * 12 +
                    b'VideoHandler\x00')

    vmhd = _fullbox(b'vmhd', 0, 0x000001,
                    struct.pack('>HHHH', 0, 0, 0, 0))

    dref = _fullbox(b'dref', 0, 0,
                    struct.pack('>I', 1) +
                    _fullbox(b'url ', 0, 0x000001, b''))   # self-contained
    dinf = _box(b'dinf', dref)

    minf = _box(b'minf', vmhd + dinf + _build_mp4_stbl(chunk_offset))
    mdia = _box(b'mdia', mdhd + hdlr + minf)
    trak = _box(b'trak', tkhd + mdia)
    return _box(b'moov', mvhd + trak)


def _pack_mp4(zip_bytes, name):
    """Return the bytes of an MP4 embedding zip_bytes in a 'free' box."""
    ftyp = _box(b'ftyp',
                b'isom' + struct.pack('>I', 0x200) +
                b'isom' + b'iso2' + b'avc1' + b'mp41')
    free = _build_mp4_payload_box(zip_bytes, name)
    mdat = _box(b'mdat', _FRAME_H264)

    # stco stores an absolute file offset, so moov must be built last - once the
    # size of everything preceding the mdat payload is known.
    chunk_offset = len(ftyp) + len(free) + 8
    return ftyp + free + mdat + _build_mp4_moov(chunk_offset)


def _mp4_boxes(f, filesize):
    """Walk the top-level boxes: (type, data_start, box_offset, box_size)."""
    offset = 0
    while offset < filesize - 7:
        f.seek(offset)
        head = f.read(8)
        if len(head) < 8:
            return
        size = struct.unpack('>I', head[0:4])[0]
        box_type = head[4:8]
        body = offset + 8
        if size == 1:                       # 64-bit size
            size = struct.unpack('>Q', f.read(8))[0]
            body = offset + 16
        elif size == 0:                     # runs to the end of the file
            size = filesize - offset
        if size < 8 or offset + size > filesize:
            return
        yield box_type, body, offset, size
        offset += size


def _mp4_attachments(path):
    """Return the payloads embedded in an MP4.

    Same shape as _iter_attachments: name, mime, offset, size - where offset and
    size point at the raw bytes (nothing is loaded into memory here)."""
    results = []
    with open(path, 'rb') as f:
        f.seek(0, os.SEEK_END)
        filesize = f.tell()
        for box_type, body, _start, _size in _mp4_boxes(f, filesize):
            if box_type not in (b'free', b'skip'):
                continue
            f.seek(body)
            head = f.read(len(MP4_MAGIC) + 1 + 2)
            if not head.startswith(MP4_MAGIC):
                continue
            name_len = struct.unpack('>H', head[-2:])[0]
            name = f.read(name_len).decode('utf-8', 'replace')
            mime_len = struct.unpack('>H', f.read(2))[0]
            mime = f.read(mime_len).decode('utf-8', 'replace')
            data_len = struct.unpack('>Q', f.read(8))[0]
            results.append({'name': name, 'mime': mime,
                            'offset': f.tell(), 'size': data_len})
    return results


def _attach_mp4(video_path, zip_bytes, name, out_path):
    """Copy an MP4 and append the payload as a 'free' box at the very end.

    Chunk offsets (stco/co64) are absolute file positions: appending at the end
    is the one edit that cannot invalidate them. Nothing already in the file
    moves."""
    # A box may declare a size of 0, meaning "to the end of the file". Appending
    # after such a box would silently swallow the new data, so its real size
    # must be written out first.
    fix = None
    with open(video_path, 'rb') as f:
        f.seek(0, os.SEEK_END)
        filesize = f.tell()
        for box_type, _body, start, size in _mp4_boxes(f, filesize):
            if start + size == filesize:
                f.seek(start)
                if struct.unpack('>I', f.read(4))[0] == 0:
                    fix = (start, size)

    box = _build_mp4_payload_box(zip_bytes, name)
    with open(video_path, 'rb') as src, open(out_path, 'wb') as dst:
        _copy_stream(src, dst)
        dst.write(box)

    if fix is not None:
        start, real_size = fix
        if real_size > 0xFFFFFFFF:
            raise SystemExit(
                "[ERROR] {} ends with an open-ended box larger than 4 GB;\n"
                "        rewriting it would need a 64-bit size."
                .format(video_path))
        with open(out_path, 'r+b') as dst:
            dst.seek(start)
            dst.write(struct.pack('>I', real_size))


# ---------------------------------------------------------------------------
# Container dispatch
# ---------------------------------------------------------------------------
def _need_file(path, what="File"):
    """Clean error (no Python traceback) if the file does not exist."""
    if not os.path.isfile(path):
        raise SystemExit("[ERROR] {} not found: {}".format(what, path))


def detect_container(path):
    """Return 'mkv' or 'mp4' by sniffing the file signature."""
    _need_file(path, "Video")
    with open(path, 'rb') as f:
        head = f.read(12)
    if head.startswith(EBML):
        return 'mkv'
    if len(head) >= 8 and head[4:8] in (b'ftyp', b'moov', b'mdat', b'free'):
        return 'mp4'
    raise SystemExit(
        "[ERROR] {} is neither a Matroska file nor an MP4.".format(path))


def _attachments(path):
    """Return the attachments, whatever the container."""
    if detect_container(path) == 'mkv':
        return _iter_attachments(path)
    return _mp4_attachments(path)


def attach(video_path, zip_path, out_path):
    """Attach a file to an existing video, which keeps playing.

    Like pack, the payload is encrypted with the key from key/key.txt if that
    file exists, otherwise the key is asked for; the result goes into pack/."""
    _need_file(zip_path, "File to attach")
    kind = detect_container(video_path)     # also checks the video exists
    key = key_for_pack()
    with open(zip_path, 'rb') as f:
        clear = f.read()
    name = os.path.basename(zip_path)
    payload = encrypt(clear, key)

    final = _output_path(PACK_DIR, out_path)
    if kind == 'mkv':
        _attach_mkv(video_path, payload, name, final)
    else:
        _attach_mp4(video_path, payload, name, final)

    before = os.path.getsize(video_path)
    after = os.path.getsize(final)
    print("[OK] {} created: {}".format(kind.upper(), final))
    print("     carrier video: {} ({} bytes, unchanged)".format(
        os.path.basename(video_path), before))
    print("     embedded zip: {} ({} bytes clear)".format(name, len(clear)))
    print("     encrypted: yes")
    print("     final size: {} bytes  (+{})".format(after, after - before))


def unpack(video_path, out_path=None):
    """Extract the (first) attachment from an MKV or MP4.

    If the payload is encrypted, it is decrypted with a key asked for at the
    keyboard. The result is placed in the unpack/ folder (created if missing)."""
    atts = _attachments(video_path)
    if not atts:
        raise SystemExit(
            "[ERROR] No attachment found in {}\n"
            "        (if the file went through a video host, it was most\n"
            "         likely re-encoded, which drops the attachment)"
            .format(video_path))

    # Prefer a zip attachment, otherwise the first available one.
    chosen = next((a for a in atts if a['mime'] == 'application/zip'), atts[0])
    if chosen['offset'] is None:
        raise SystemExit("[ERROR] The attachment holds no data.")

    if out_path is None:
        out_path = chosen['name'] or 'output.zip'

    with open(video_path, 'rb') as f:
        f.seek(chosen['offset'])
        data = f.read(chosen['size'])
    if len(data) != chosen['size']:
        raise SystemExit(
            "[ERROR] Truncated file: expected {} bytes, read {}.".format(
                chosen['size'], len(data)))

    if is_encrypted(data):
        data = decrypt(data, key_for_unpack())
        state = "decrypted"
    else:
        # File produced before encryption was added: emit it as-is rather than
        # fail, but say so unambiguously.
        state = "PLAINTEXT (file predates encryption)"

    final = _output_path(UNPACK_DIR, out_path)
    with open(final, 'wb') as f:
        f.write(data)

    print("[OK] zip extracted: {} ({} bytes)".format(final, len(data)))
    print("     state: {}".format(state))


def info(video_path):
    """List the attachments contained in the MKV or MP4."""
    kind = detect_container(video_path)
    atts = _attachments(video_path)
    if not atts:
        print("No attachment.")
        return
    print("Attachments in {} ({}):".format(video_path, kind.upper()))
    for i, a in enumerate(atts, 1):
        print("  {}. {}  [{}]  {} bytes".format(
            i, a['name'], a['mime'], a['size']))


# ---------------------------------------------------------------------------
# Self-test: all in memory, touching neither the disk nor the key file
# ---------------------------------------------------------------------------
def selftest():
    """Check encryption, containers and wrong-key detection.

    Uses only the internal functions: does not read key/key.txt and writes
    neither pack/ nor unpack/. Returns 0 if everything passes, 1 otherwise."""
    ok = [0]; ko = [0]
    def chk(name, cond):
        if cond:
            ok[0] += 1; print("  ok   {}".format(name))
        else:
            ko[0] += 1; print("  FAIL {}".format(name))

    key = b'my-test-key-123'
    for size in (0, 1, 15, 4096, 200000):
        clear = secrets.token_bytes(size)
        blob = encrypt(clear, key)
        chk("encryption marked as such ({} B)".format(size), is_encrypted(blob))
        chk("decryption identical ({} B)".format(size),
            decrypt(blob, key) == clear)
        # Two encryptions of the same clear must differ (random salt/nonce).
        if size:
            chk("random salt/nonce ({} B)".format(size),
                encrypt(clear, key) != blob)

    clear = secrets.token_bytes(50000)
    blob = encrypt(clear, key)
    # Wrong key: must be rejected (SystemExit), not return bogus bytes.
    try:
        decrypt(blob, b'wrong-key')
        chk("wrong key rejected", False)
    except SystemExit:
        chk("wrong key rejected", True)
    # Tampered file: a single flipped byte must be detected.
    tampered = bytearray(blob); tampered[-1] ^= 0x01
    try:
        decrypt(bytes(tampered), key)
        chk("tampering detected", False)
    except SystemExit:
        chk("tampering detected", True)

    # Full round-trip in memory, for each container.
    clear = secrets.token_bytes(120000)
    payload = encrypt(clear, key)
    for kind, packer in (('MKV', _pack_mkv), ('MP4', _pack_mp4)):
        data = packer(payload, 'test.zip')
        tmp = os.path.join(_base_dir(), '._selftest_{}'.format(kind))
        try:
            with open(tmp, 'wb') as f:
                f.write(data)
            atts = _attachments(tmp)
            got = None
            if atts:
                with open(tmp, 'rb') as f:
                    f.seek(atts[0]['offset'])
                    got = f.read(atts[0]['size'])
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        chk("{}: attachment found".format(kind), atts and got is not None)
        chk("{}: encrypted content intact".format(kind), got == payload)
        chk("{}: final decryption identical".format(kind),
            got is not None and decrypt(got, key) == clear)

    print("\nselftest: {} ok, {} failure(s)".format(ok[0], ko[0]))
    return 0 if ko[0] == 0 else 1


# ---------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------
def _usage():
    print(__doc__.strip())


def main(argv):
    if len(argv) < 2:
        _usage()
        return 1

    cmd = argv[1]

    if cmd == 'pack':
        rest = argv[2:]
        # --mp4 / --mkv only matter when the output name is left implicit.
        default_ext = '.mkv'
        for flag, ext in (('--mp4', '.mp4'), ('--mkv', '.mkv')):
            if flag in rest:
                rest = [a for a in rest if a != flag]
                default_ext = ext
        if not rest:
            _usage(); return 1
        zip_path = rest[0]
        out_path = rest[1] if len(rest) > 1 else \
            os.path.splitext(zip_path)[0] + default_ext
        pack(zip_path, out_path)

    elif cmd == 'attach':
        if len(argv) < 4:
            _usage(); return 1
        video_path, zip_path = argv[2], argv[3]
        if len(argv) > 4:
            out_path = argv[4]
        else:
            base, ext = os.path.splitext(video_path)
            out_path = base + '_with_' + \
                os.path.splitext(os.path.basename(zip_path))[0] + ext
        attach(video_path, zip_path, out_path)

    elif cmd == 'unpack':
        if len(argv) < 3:
            _usage(); return 1
        mkv_path = argv[2]
        out_path = argv[3] if len(argv) > 3 else None
        unpack(mkv_path, out_path)

    elif cmd == 'info':
        if len(argv) < 3:
            _usage(); return 1
        info(argv[2])

    elif cmd == 'selftest':
        return selftest()

    else:
        print("Unknown command: {}\n".format(cmd))
        _usage()
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
