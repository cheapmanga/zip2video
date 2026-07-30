zip2mkv - wrap a .zip inside a valid video file (MKV or MP4), and get it back
=============================================================================

WHAT IT DOES
------------
Takes a .zip file and stores it inside a valid video file. The video is
recognized as real (it has a 1-second video track), and the .zip rides along
untouched. Extracting gives you back the exact same .zip, byte for byte
(no re-compression).

Two containers are supported:
  .mkv   the .zip is a Matroska attachment (the official Matroska mechanism
         for embedding an arbitrary file). Video track: one MJPEG frame.
  .mp4   MP4 has no attachment concept, so the .zip goes into a top-level
         'free' box - a box the MP4 spec explicitly defines as "to be
         ignored", so every player skips over it. Video track: one H.264
         frame.

This is encapsulation, NOT encryption: anyone with this tool (or MKVToolNix,
for the .mkv) can pull the .zip back out. Do not treat it as protection.

Despite the name, it is not limited to .zip files - any file works. Only the
label stored alongside it says "zip".


FILES IN THIS FOLDER
--------------------
  zip2mkv.py   The actual program (pure Python, no dependencies).
  pack.bat       Drag a .zip onto it -> creates the .mkv next to it.
  pack-mp4.bat   Drag a .zip onto it -> creates the .mp4 next to it.
  unpack.bat     Drag a .mkv or .mp4 onto it -> extracts the .zip next to it.
  README.txt     This file.

Keep all files together in the same folder. The .bat files call zip2mkv.py
that sits next to them.


REQUIREMENTS
------------
Python 3 must be installed and on PATH.
Get it from the Microsoft Store ("Python 3") or from python.org.
During install from python.org, tick "Add Python to PATH".


ATTACHING TO A VIDEO YOU ALREADY HAVE
-------------------------------------
pack.bat builds a throwaway 1-second carrier. If you would rather ride along
with a real video, so the result plays normally:

  python zip2mkv.py attach holiday.mkv my_archive.zip

The video and its audio come out bit-identical to the original - nothing that
was already in the file moves. Only this script can find an attachment added
this way; if you need mkvextract to see it too, use pack instead.


HOW TO USE (easy way: drag and drop)
------------------------------------
  Pack to mkv:  drag your .zip file onto  pack.bat
  Pack to mp4:  drag your .zip file onto  pack-mp4.bat
  Unpack:       drag your .mkv or .mp4 onto  unpack.bat

Unpacking detects the container on its own - you do not have to say which
one it is.

A black window opens, shows the result, and waits for a key press.

If Windows shows a blue "Windows protected your PC" warning the first time,
click "More info" -> "Run anyway". It only appears because the .bat is not
signed; it is your own file.


HOW TO USE (command line)
-------------------------
Open Command Prompt (Windows key -> type cmd -> Enter), go to this folder
(cd C:\path\to\this\folder), then:

  python zip2mkv.py attach      video.mkv my_archive.zip   (ride along)
  python zip2mkv.py pack        my_archive.zip     (-> my_archive.mkv)
  python zip2mkv.py pack --mp4  my_archive.zip     (-> my_archive.mp4)
  python zip2mkv.py info        my_archive.mp4     (show what is inside)
  python zip2mkv.py unpack      my_archive.mp4     (-> my_archive.zip)

You can add a second name to choose the output file. The extension you give
picks the container:
  python zip2mkv.py pack  my_archive.zip  hidden.mp4     (-> MP4)
  python zip2mkv.py pack  my_archive.zip  hidden.mkv     (-> MKV)


NOTES
-----
- Playing the video shows a still image for about 1 second. That is normal:
  the video is only there so the container counts as a real video. The zip is
  not "played" - it is a passenger you pull back out with unpack.
- IMPORTANT - re-encoding destroys the payload. If you send the file through
  anything that re-encodes or remuxes it (a video host, a messaging app that
  compresses videos, an editor), the attachment or the 'free' box is dropped
  and the zip is gone. Only the untouched original file can be unpacked.
- MKV or MP4? MP4 is accepted in more places and looks more ordinary. MKV
  uses a documented attachment mechanism, so MKVToolNix and mkvextract can (verified)
  pull the file out even without this script. Both round-trip identically.
- Verified: for both containers, the extracted zip is identical to the
  original (same SHA-256), and the video decodes correctly (ffprobe reports a
  valid 320x240 stream, and the frame decodes to a real image).
