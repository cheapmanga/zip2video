zip2mkv - wrap a .zip inside a valid MKV video, and get it back
==============================================================

WHAT IT DOES
------------
Takes a .zip file and stores it inside a valid .mkv video. The video is
recognized as real (it has a 1-second video track), and the .zip rides along
untouched as a Matroska attachment - the official Matroska mechanism for
embedding an arbitrary file. Extracting gives you back the exact same .zip,
byte for byte (no re-compression).

This is encapsulation, NOT encryption: anyone with this tool, or with
MKVToolNix, can pull the .zip back out. Do not treat it as protection.

Despite the name, it is not limited to .zip files - any file works. Only the
label stored alongside it says "zip".


FILES IN THIS FOLDER
--------------------
  zip2mkv.py   The actual program (pure Python, no dependencies).
  unpack.bat   Drag a .mkv onto it -> extracts the .zip next to it.
  README.txt   This file.

Keep all files together in the same folder. The .bat files call zip2mkv.py
that sits next to them.


REQUIREMENTS
------------
Python 3 must be installed and on PATH.
Get it from the Microsoft Store ("Python 3") or from python.org.
During install from python.org, tick "Add Python to PATH".


HOW TO USE (easy way: drag and drop)
------------------------------------
  Unpack:  drag the .mkv onto  unpack.bat

A black window opens, shows the result, and waits for a key press.

There is no pack.bat: packing is done from the command line, see below.

If Windows shows a blue "Windows protected your PC" warning the first time,
click "More info" -> "Run anyway". It only appears because the .bat is not
signed; it is your own file.


HOW TO USE (command line)
-------------------------
Open Command Prompt (Windows key -> type cmd -> Enter), go to this folder
(cd C:\path\to\this\folder), then:

  python zip2mkv.py pack   my_archive.zip              (-> my_archive.mkv)
  python zip2mkv.py attach video.mkv my_archive.zip    (ride along)
  python zip2mkv.py info   my_archive.mkv              (show what is inside)
  python zip2mkv.py unpack my_archive.mkv              (-> my_archive.zip)

You can add a last name to choose the output file:
  python zip2mkv.py pack my_archive.zip hidden.mkv

'attach' puts the zip into a video you already have, instead of building a
1-second one. The video keeps playing exactly as before - verified, its picture
and sound decode bit-identically. Only this script finds an attachment added
that way; use 'pack' if mkvextract must see it too.


NOTES
-----
- Playing the video shows a still image for about 1 second. That is normal:
  the video is only there so the container counts as a real video. The zip is
  not "played" - it is a passenger you pull back out with unpack. Use attach
  instead if you would rather carry it inside a real video of your own.
- IMPORTANT - re-encoding destroys the payload. If you send the file through
  anything that re-encodes or remuxes it (a video host, a messaging app that
  compresses videos, an editor), the attachment is dropped and the zip is gone.
  Only the untouched original file can be unpacked. A transcoded carrier is
  easy to spot: it collapses to a couple of kilobytes.
- MKVToolNix and mkvextract can pull the file out even without this script
  (verified with ffmpeg -dump_attachment). That only works because pack writes
  the attachment before the first Cluster; placed after it, no standard tool
  would ever reach it.
- Verified: the extracted zip is identical to the original (same SHA-256),
  and after attach the video and audio decode bit-identically to the source.
