#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zip2mkv.py — Encapsule un fichier .zip dans un conteneur video MKV (Matroska) valide,
et permet de le re-extraire bit-pour-bit.

Aucune dependance externe : le conteneur Matroska est construit a la main en EBML
(le format binaire de MKV). Pas besoin de ffmpeg ni de MKVToolNix.

Comment ca marche
-----------------
Le fichier .mkv genere contient deux choses :
  1. Une vraie piste video (une image MJPEG) -> le fichier est reconnu comme une
     video valide par les lecteurs, mediainfo, ffprobe, la commande `file`, etc.
  2. Le .zip stocke comme "piece jointe" Matroska (element AttachedFile). C'est le
     mecanisme officiel de Matroska pour embarquer un fichier binaire arbitraire.
     Il ressort strictement identique a l'original (aucune recompression).

Usage
-----
  # Emballer un zip dans un mkv :
  python3 zip2mkv.py pack  mon_archive.zip  [sortie.mkv]

  # Re-extraire le zip depuis le mkv :
  python3 zip2mkv.py unpack sortie.mkv      [mon_archive.zip]

  # Afficher les pieces jointes contenues dans un mkv :
  python3 zip2mkv.py info   sortie.mkv

Si le nom de sortie est omis, il est deduit du nom d'entree.
"""

import base64
import os
import struct
import sys

# ---------------------------------------------------------------------------
# Identifiants des elements EBML/Matroska (octets bruts, marqueur VINT inclus)
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

# Une petite image JPEG 320x240 valide, utilisee comme unique frame de la piste
# video. Integree en dur pour que le script n'ait aucune dependance.
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
# Ecriture EBML
# ---------------------------------------------------------------------------
def encode_size(n):
    """Encode une taille en VINT (Variable-length INTeger) Matroska."""
    for length in range(1, 9):
        limit = (1 << (7 * length)) - 1          # la valeur "tout a 1" est reservee
        if n < limit:
            return (n | (1 << (7 * length))).to_bytes(length, 'big')
    raise ValueError("taille trop grande pour un VINT")


def elem(elem_id, data):
    """Construit un element EBML : identifiant + taille + donnees."""
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
    # SimpleBlock : numero de piste (VINT) + timecode int16 + flags + donnees
    block = b'\x81' + (0).to_bytes(2, 'big') + b'\x80' + _FRAME_JPEG   # 0x80 = keyframe
    return elem(CLUSTER,
        elem(TIMESTAMP, b'\x00') +
        elem(SIMPLE_BLOCK, block))


def _build_attachments(zip_bytes, filename):
    attached = elem(ATTACHED_FILE,
        elem(FILE_DESCRIPTION, "Archive zip embarquee".encode('utf-8')) +
        elem(FILE_NAME,        filename.encode('utf-8')) +
        elem(FILE_MIME_TYPE,   b'application/zip') +
        elem(FILE_DATA,        zip_bytes) +
        elem(FILE_UID,         b'\x00\x00\x00\x00\x00\x00\x00\x02'))
    return elem(ATTACHMENTS, attached)


def pack(zip_path, mkv_path):
    """Emballe le fichier zip_path dans un conteneur MKV valide (mkv_path)."""
    with open(zip_path, 'rb') as f:
        zip_bytes = f.read()

    segment_body = (
        _build_info() +
        _build_tracks() +
        _build_cluster() +
        _build_attachments(zip_bytes, os.path.basename(zip_path))
    )
    data = _build_ebml_header() + elem(SEGMENT, segment_body)

    with open(mkv_path, 'wb') as f:
        f.write(data)

    print("[OK] MKV cree : {}".format(mkv_path))
    print("     zip embarque : {} ({} octets)".format(
        os.path.basename(zip_path), len(zip_bytes)))
    print("     taille finale : {} octets".format(len(data)))


# ---------------------------------------------------------------------------
# Lecture EBML (extraction)
# ---------------------------------------------------------------------------
def _read_id(f):
    """Lit un identifiant d'element EBML (octets bruts). Retourne None a la fin."""
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
        raise ValueError("identifiant EBML invalide")
    return first + f.read(length - 1)


def _read_size(f):
    """Lit une taille EBML (VINT). Retourne None si taille inconnue."""
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
        return None                              # taille inconnue (non utilise ici)
    return value


# Elements "conteneurs" que l'on doit explorer recursivement pour trouver les PJ.
_MASTERS = {SEGMENT, ATTACHMENTS, ATTACHED_FILE}


def _iter_attachments(path):
    """Parcourt le MKV et renvoie la liste des pieces jointes.

    Chaque PJ : dict(name, mime, offset, size) — offset/size pointent sur les
    octets bruts de FILE_DATA (le contenu n'est pas charge en memoire ici)."""
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
                # (les autres elements de haut niveau sont ignores)
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


def unpack(mkv_path, out_path=None):
    """Extrait la (premiere) piece jointe zip depuis un MKV cree par ce script."""
    atts = _iter_attachments(mkv_path)
    if not atts:
        raise SystemExit("[ERREUR] Aucune piece jointe trouvee dans {}".format(mkv_path))

    # On privilegie une PJ de type zip, sinon la premiere disponible.
    chosen = next((a for a in atts if a['mime'] == 'application/zip'), atts[0])
    if chosen['offset'] is None:
        raise SystemExit("[ERREUR] La piece jointe ne contient pas de donnees.")

    if out_path is None:
        out_path = chosen['name'] or 'sortie.zip'

    with open(mkv_path, 'rb') as f:
        f.seek(chosen['offset'])
        data = f.read(chosen['size'])
    with open(out_path, 'wb') as f:
        f.write(data)

    print("[OK] zip extrait : {} ({} octets)".format(out_path, len(data)))


def info(mkv_path):
    """Liste les pieces jointes contenues dans le MKV."""
    atts = _iter_attachments(mkv_path)
    if not atts:
        print("Aucune piece jointe.")
        return
    print("Pieces jointes dans {} :".format(mkv_path))
    for i, a in enumerate(atts, 1):
        print("  {}. {}  [{}]  {} octets".format(
            i, a['name'], a['mime'], a['size']))


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
        if len(argv) < 3:
            _usage(); return 1
        zip_path = argv[2]
        mkv_path = argv[3] if len(argv) > 3 else os.path.splitext(zip_path)[0] + '.mkv'
        pack(zip_path, mkv_path)

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

    else:
        print("Commande inconnue : {}\n".format(cmd))
        _usage()
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
