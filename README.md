# JCube Shorts Editor

Drop in a raw talking clip, get a finished 9:16 short back.

What it does automatically:
* **Audio cutting**: transcribes with Whisper, cuts every pause over 0.3s plus ums and uhs. Tight jump cuts.
* **Captions**: DM Sans ExtraBold, all caps, 4 words a line, one keyword per line highlighted
  (blue = numbers/records, red = shock words, yellow = hype words, bigger = key noun).
* **Hook text**: big text up top for the first 3 seconds. Uses your first sentence unless you pass `--hook`.
* **Music**: bed fades in by 1s and auto ducks under your voice.
* **Opener**: riser, whoosh at 0s, boom on the first cut, 1.25x zoom out, fake cuts every ~1.5s in the hook, punch zooms on key words after that.
* **Export**: 1080x1920, 30fps, -14 LUFS. Ready for TikTok, Shorts, Reels.

## Setup (once)

1. Install [FFmpeg](https://ffmpeg.org/download.html) and Python 3.10+
2. `pip install -r requirements.txt`
3. Put music in `Assets/music/` (uses `Deliberate-Thought.mp3` if it's there, otherwise a random track)
4. Optional: `Assets/sfx/whoosh-sfx.mp3`, `riser.mp3`, `vine-boom.mp3`. If missing it makes its own.
5. Optional: `Assets/overlays/jessecubez_sub_like_overlay.webm` plays at the halfway point.

## Use

```
python edit.py raw.mp4
python edit.py raw.mp4 --hook "THIS CUBE IS ILLEGAL"
python edit.py raw.mp4 --music Assets/music/Hackbeat.mp3 --max-gap 0.25
```

Output lands in `output/`: the video, an `.srt` for uploading captions, the `.ass` caption file, and the transcript.

Handy flags:
| flag | what |
|---|---|
| `--hook "TEXT"` / `--no-hook` | set or kill the hook text |
| `--music file` / `--music none` | pick the track |
| `--music-volume 0.22` | music level before ducking |
| `--max-gap 0.3` | lower = tighter cuts |
| `--words-per-line 4` | caption chunk size |
| `--fit blur` | horizontal footage gets a blurred background instead of a crop |
| `--model medium` | more accurate transcript, slower |

Fixing a bad word in the captions: edit `output/<name>_edit.words.json` and rerun. It reuses that transcript instead of transcribing again.
