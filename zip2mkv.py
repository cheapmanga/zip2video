#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zip2video.py - Wrap a .zip file inside a valid video container (MKV or MP4),
and extract it back bit-for-bit.

No external dependencies: both containers are built by hand. No ffmpeg, no
MKVToolNix required.

How it works
------------
The generated file contains two things:
  1. A real video track (one frame) -> the file is recognized as a valid video
     by players, mediainfo, ffprobe, the `file` command, etc.
  2. The .zip stored verbatim, with no re-compression. It comes back strictly
     identical to the original.

The storage mechanism differs per container:
  MKV  the zip goes into a Matroska "attachment" (AttachedFile element), the
       official Matroska way to embed an arbitrary binary file. The video track
       is a single MJPEG frame.
  MP4  ISOBMFF has no attachment concept, so the zip goes into a top-level
       'free' box, which the spec defines as "to be ignored" - every parser
       skips it cleanly. The video track is a single H.264 frame.

Usage
-----
  # Wrap a zip into a video (the output extension picks the container):
  python zip2video.py pack   my_archive.zip                (-> my_archive.mkv)
  python zip2video.py pack   my_archive.zip  hidden.mp4    (-> MP4)
  python zip2video.py pack --mp4  my_archive.zip           (-> my_archive.mp4)

  # Extract the zip back (container is auto-detected):
  python zip2video.py unpack hidden.mp4      [my_archive.zip]

  # Show what is embedded (container is auto-detected):
  python zip2video.py info   hidden.mp4

If the output name is omitted, it is derived from the input name.
"""

import base64
import os
import struct
import sys

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
        elem(MUXING_APP,      b'zip2video') +
        elem(WRITING_APP,     b'zip2video'))


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


def _pack_mkv(payload, name):
    """Return the bytes of an MKV container embedding `payload` as attachment."""
    segment_body = (
        _build_info() +
        _build_tracks() +
        _build_cluster() +
        _build_attachments(payload, name)
    )
    return _build_ebml_header() + elem(SEGMENT, segment_body)


# ---------------------------------------------------------------------------
# EBML reading (extraction)
# ---------------------------------------------------------------------------
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


def _mkv_attachments(path):
    """Walk the MKV and return the list of attachments.

    Each attachment: dict(name, mime, offset, size) - offset/size point at the
    raw bytes of FILE_DATA (the content is not loaded into memory here)."""
    results = []

    with open(path, 'rb') as f:
        f.seek(0, os.SEEK_END)
        filesize = f.tell()

        def _read_attached_file(start, end):
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

        def walk(start, end):
            f.seek(start)
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
                    results.append(_read_attached_file(data_start, data_end))
                # (other top-level elements are ignored)
                f.seek(data_end)

        walk(0, filesize)

    return results


# ---------------------------------------------------------------------------
# MP4 / ISOBMFF
# ---------------------------------------------------------------------------
# ISOBMFF has no equivalent of a Matroska attachment, so the payload is stored
# in a top-level 'free' box. ISO/IEC 14496-12 states that the contents of a
# 'free' box are irrelevant and may be ignored, so every conformant parser
# skips over it without complaining.
#
# Layout of the 'free' box payload:
#     magic       8 bytes   b'ZIP2VID\x00'
#     version     1 byte    format version (currently 1)
#     name_len    2 bytes   big endian, then the UTF-8 file name
#     mime_len    2 bytes   big endian, then the UTF-8 MIME type
#     data_len    8 bytes   big endian
#     data        the embedded file, verbatim
MP4_MAGIC = b'ZIP2VID\x00'
MP4_PAYLOAD_VERSION = 1

# A single 320x240 H.264 (Constrained Baseline) keyframe, in AVCC form
# (4-byte length prefix + NAL unit), plus its matching AVCDecoderConfiguration
# record. Embedded inline so the script keeps having no dependency.
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

# Unity transformation matrix (16.16 fixed point, last row 2.30).
_MP4_MATRIX = struct.pack('>9i', 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)


def _box(box_type, payload):
    """Build an ISOBMFF box: size + type + payload (64-bit size if needed)."""
    size = len(payload) + 8
    if size <= 0xFFFFFFFF:
        return struct.pack('>I', size) + box_type + payload
    # Oversized box: size field is 1 and a 64-bit largesize follows the type.
    return struct.pack('>I', 1) + box_type + struct.pack('>Q', size + 8) + payload


def _fullbox(box_type, version, flags, payload):
    """Build an ISOBMFF FullBox (a box prefixed with version + 24-bit flags)."""
    return _box(box_type, struct.pack('>BBBB', version,
                                      (flags >> 16) & 0xFF,
                                      (flags >> 8) & 0xFF,
                                      flags & 0xFF) + payload)


def _build_mp4_payload_box(payload, name, mime):
    """Build the top-level 'free' box holding the embedded file."""
    name_b = name.encode('utf-8')
    mime_b = mime.encode('utf-8')
    body = (MP4_MAGIC +
            bytes([MP4_PAYLOAD_VERSION]) +
            struct.pack('>H', len(name_b)) + name_b +
            struct.pack('>H', len(mime_b)) + mime_b +
            struct.pack('>Q', len(payload)) +
            payload)
    return _box(b'free', body)


def _build_mp4_stbl(chunk_offset):
    """Build the sample table. chunk_offset is the absolute mdat data offset."""
    # VisualSampleEntry: 78 bytes of fixed fields, then the avcC extension.
    compressor = b'\x00' * 32          # length-prefixed string, left empty
    avc1_body = (
        b'\x00' * 6 +                          # reserved
        struct.pack('>H', 1) +                 # data_reference_index
        b'\x00' * 2 + b'\x00' * 2 + b'\x00' * 12 +   # pre_defined / reserved
        struct.pack('>HH', _MP4_WIDTH, _MP4_HEIGHT) +
        struct.pack('>II', 0x00480000, 0x00480000) +  # 72 dpi h/v resolution
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
                    struct.pack('>I', 1) +                  # entry_count
                    struct.pack('>II', 1, _MP4_DURATION))   # count, delta
    stsc = _fullbox(b'stsc', 0, 0,
                    struct.pack('>I', 1) +
                    struct.pack('>III', 1, 1, 1))  # first_chunk, spc, desc_idx
    stsz = _fullbox(b'stsz', 0, 0,
                    struct.pack('>II', len(_FRAME_H264), 1))  # size, count
    stco = _fullbox(b'stco', 0, 0,
                    struct.pack('>I', 1) + struct.pack('>I', chunk_offset))
    return _box(b'stbl', stsd + stts + stsc + stsz + stco)


def _build_mp4_moov(chunk_offset):
    """Build the moov box describing the single-frame video track."""
    mvhd = _fullbox(b'mvhd', 0, 0,
                    struct.pack('>IIII', 0, 0, _MP4_TIMESCALE, _MP4_DURATION) +
                    struct.pack('>I', 0x00010000) +   # rate 1.0
                    struct.pack('>H', 0x0100) +       # volume 1.0
                    b'\x00' * 2 + b'\x00' * 8 +       # reserved
                    _MP4_MATRIX +
                    b'\x00' * 24 +                    # pre_defined
                    struct.pack('>I', 2))             # next_track_ID

    tkhd = _fullbox(b'tkhd', 0, 0x000003,             # enabled | in movie
                    struct.pack('>IIII', 0, 0, 1, 0) +   # times, track_ID, rsvd
                    struct.pack('>I', _MP4_DURATION) +
                    b'\x00' * 8 +                     # reserved
                    struct.pack('>hhh', 0, 0, 0) +    # layer, altgroup, volume
                    b'\x00' * 2 +                     # reserved
                    _MP4_MATRIX +
                    struct.pack('>II', _MP4_WIDTH << 16, _MP4_HEIGHT << 16))

    mdhd = _fullbox(b'mdhd', 0, 0,
                    struct.pack('>IIII', 0, 0, _MP4_TIMESCALE, _MP4_DURATION) +
                    struct.pack('>H', 0x55C4) +       # language 'und'
                    struct.pack('>H', 0))             # pre_defined

    hdlr = _fullbox(b'hdlr', 0, 0,
                    struct.pack('>I', 0) + b'vide' + b'\x00' * 12 +
                    b'VideoHandler\x00')

    vmhd = _fullbox(b'vmhd', 0, 0x000001,
                    struct.pack('>HHHH', 0, 0, 0, 0))  # graphicsmode, opcolor

    dref = _fullbox(b'dref', 0, 0,
                    struct.pack('>I', 1) +
                    _fullbox(b'url ', 0, 0x000001, b''))   # self-contained
    dinf = _box(b'dinf', dref)

    minf = _box(b'minf', vmhd + dinf + _build_mp4_stbl(chunk_offset))
    mdia = _box(b'mdia', mdhd + hdlr + minf)
    trak = _box(b'trak', tkhd + mdia)
    return _box(b'moov', mvhd + trak)


def _pack_mp4(payload, name, mime='application/zip'):
    """Return the bytes of an MP4 container embedding `payload` in a free box."""
    ftyp = _box(b'ftyp',
                b'isom' + struct.pack('>I', 0x200) +
                b'isom' + b'iso2' + b'avc1' + b'mp41')
    free = _build_mp4_payload_box(payload, name, mime)
    mdat = _box(b'mdat', _FRAME_H264)

    # stco stores an absolute file offset, so moov must be built last - once the
    # size of everything preceding the mdat payload is known.
    chunk_offset = len(ftyp) + len(free) + 8
    return ftyp + free + mdat + _build_mp4_moov(chunk_offset)


def _mp4_attachments(path):
    """Walk the top-level MP4 boxes and return the embedded payloads.

    Same dict shape as _mkv_attachments: name, mime, offset, size - where
    offset/size point at the raw embedded bytes (nothing is loaded here)."""
    results = []
    with open(path, 'rb') as f:
        f.seek(0, os.SEEK_END)
        filesize = f.tell()
        f.seek(0)

        offset = 0
        while offset < filesize - 7:
            f.seek(offset)
            header = f.read(8)
            if len(header) < 8:
                break
            size = struct.unpack('>I', header[0:4])[0]
            box_type = header[4:8]
            body = offset + 8
            if size == 1:                       # 64-bit largesize
                size = struct.unpack('>Q', f.read(8))[0]
                body = offset + 16
            elif size == 0:                     # extends to end of file
                size = filesize - offset
            if size < 8 or offset + size > filesize:
                break

            if box_type in (b'free', b'skip'):
                f.seek(body)
                head = f.read(len(MP4_MAGIC) + 1 + 2)
                if head.startswith(MP4_MAGIC):
                    name_len = struct.unpack('>H', head[-2:])[0]
                    name = f.read(name_len).decode('utf-8', 'replace')
                    mime_len = struct.unpack('>H', f.read(2))[0]
                    mime = f.read(mime_len).decode('utf-8', 'replace')
                    data_len = struct.unpack('>Q', f.read(8))[0]
                    results.append({'name': name, 'mime': mime,
                                    'offset': f.tell(), 'size': data_len})

            offset += size

    return results


# ---------------------------------------------------------------------------
# Container dispatch
# ---------------------------------------------------------------------------
def detect_container(path):
    """Return 'mkv' or 'mp4' by sniffing the file signature."""
    with open(path, 'rb') as f:
        head = f.read(12)
    if head.startswith(EBML):
        return 'mkv'
    if len(head) >= 8 and head[4:8] in (b'ftyp', b'moov', b'mdat', b'free'):
        return 'mp4'
    raise SystemExit(
        "[ERROR] {} is neither a Matroska nor an MP4 file.".format(path))


def _attachments(path):
    """Return the embedded payloads, whatever the container."""
    if detect_container(path) == 'mkv':
        return _mkv_attachments(path)
    return _mp4_attachments(path)


def pack(src_path, out_path):
    """Wrap the file at src_path into a valid video container (out_path).

    The output extension selects the container: .mp4/.m4v give an MP4,
    anything else gives an MKV."""
    with open(src_path, 'rb') as f:
        payload = f.read()

    name = os.path.basename(src_path)
    if os.path.splitext(out_path)[1].lower() in ('.mp4', '.m4v'):
        kind, data = 'MP4', _pack_mp4(payload, name)
    else:
        kind, data = 'MKV', _pack_mkv(payload, name)

    with open(out_path, 'wb') as f:
        f.write(data)

    print("[OK] {} created: {}".format(kind, out_path))
    print("     embedded file: {} ({} bytes)".format(name, len(payload)))
    print("     final size:    {} bytes".format(len(data)))


def unpack(video_path, out_path=None):
    """Extract the embedded file from an MKV or MP4 created by this script."""
    atts = _attachments(video_path)
    if not atts:
        raise SystemExit(
            "[ERROR] No embedded file found in {}\n"
            "        (if it went through a video host, it was most likely\n"
            "         re-encoded, which discards the payload)".format(video_path))

    # Prefer a zip payload, otherwise take the first one available.
    chosen = next((a for a in atts if a['mime'] == 'application/zip'), atts[0])
    if chosen['offset'] is None:
        raise SystemExit("[ERROR] The embedded file has no data.")

    if out_path is None:
        out_path = chosen['name'] or 'output.zip'

    with open(video_path, 'rb') as f:
        f.seek(chosen['offset'])
        data = f.read(chosen['size'])
    if len(data) != chosen['size']:
        raise SystemExit(
            "[ERROR] Truncated file: expected {} bytes, got {}.".format(
                chosen['size'], len(data)))
    with open(out_path, 'wb') as f:
        f.write(data)

    print("[OK] extracted: {} ({} bytes)".format(out_path, len(data)))


def info(video_path):
    """List what is embedded in the MKV or MP4."""
    kind = detect_container(video_path)
    atts = _attachments(video_path)
    if not atts:
        print("{}: {} container, nothing embedded.".format(video_path, kind.upper()))
        return
    print("Embedded in {} ({}):".format(video_path, kind.upper()))
    for i, a in enumerate(atts, 1):
        print("  {}. {}  [{}]  {} bytes".format(
            i, a['name'], a['mime'], a['size']))


# ---------------------------------------------------------------------------
# Command line
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
        src_path = rest[0]
        out_path = rest[1] if len(rest) > 1 else \
            os.path.splitext(src_path)[0] + default_ext
        pack(src_path, out_path)

    elif cmd == 'unpack':
        if len(argv) < 3:
            _usage(); return 1
        video_path = argv[2]
        out_path = argv[3] if len(argv) > 3 else None
        unpack(video_path, out_path)

    elif cmd == 'info':
        if len(argv) < 3:
            _usage(); return 1
        info(argv[2])

    else:
        print("Unknown command: {}\n".format(cmd))
        _usage()
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
