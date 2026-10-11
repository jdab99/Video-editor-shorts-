#!/usr/bin/env python3
"""JCube Shorts auto editor.

Raw talking clip in, finished 9:16 short out:
  1. Transcribe with word timestamps (faster-whisper)
  2. Cut dead air, filler words and long pauses (tight jump cuts)
  3. Reframe to 1080x1920, zoom-out opener, punch zooms on key words
  4. Burn in styled captions with one highlighted keyword per line
  5. Big hook text over the first few seconds
  6. Music bed that ducks under your voice, plus riser / whoosh / boom on the opener
  7. Loudness normalized to -14 LUFS for TikTok / Shorts / Reels

Usage:
  python edit.py raw.mp4
  python edit.py raw.mp4 --hook "THIS CUBE IS ILLEGAL" --music Assets/music/Hackbeat.mp3
"""

import argparse
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "Assets"
FONTS = ROOT / "fonts"
FPS = 30
CAPTION_FS = 92
HOOK_FS = 116
W, H = 1080, 1920

FILLERS = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "hmm", "mm", "mhm"}

NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "fifteen", "twenty", "thirty", "forty", "fifty", "hundred", "thousand",
    "million", "first", "second", "third", "record", "records", "seconds", "sub", "average", "wr",
}
NEGATIVE_WORDS = {
    "failed", "fail", "fails", "impossible", "banned", "wrong", "worst", "bad", "dead", "lost",
    "lose", "hate", "quit", "broke", "broken", "mistake", "mistakes", "dnf", "illegal", "scam",
    "terrible", "trash", "ruined", "stop", "dont", "don't", "can't", "cant", "never",
}
EMPHASIS_WORDS = {
    "insane", "crazy", "ever", "best", "fastest", "actually", "literally", "huge", "secret",
    "only", "everyone", "nobody", "always", "easiest", "hardest", "perfect", "unbelievable",
    "wild", "need", "must", "free", "easy", "fast", "faster", "biggest", "new", "magic",
    "trick", "hack", "every", "insanely", "legendary", "goat", "world",
}
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "at", "for", "is", "it",
    "this", "that", "i", "you", "we", "they", "he", "she", "my", "your", "so", "just", "like",
    "with", "was", "are", "be", "have", "has", "do", "if", "then", "what", "how", "its", "it's",
    "i'm", "im", "me", "all", "can", "get", "got", "about", "really", "out", "up", "there",
}

# ASS colours are &HAABBGGRR
BLUE = "&H00FFBF00&"    # #00BFFF
YELLOW = "&H0000E6FF&"  # #FFE600
RED = "&H003B3BFF&"     # #FF3B3B


def run(cmd, quiet=True):
    print("  $", " ".join(str(c) for c in cmd[:6]), "..." if len(cmd) > 6 else "")
    res = subprocess.run([str(c) for c in cmd], capture_output=quiet, text=True)
    if res.returncode != 0:
        if quiet:
            sys.stderr.write(res.stderr[-4000:])
        sys.exit(f"command failed: {cmd[0]}")
    return res


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    info = json.loads(out)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if not v or not a:
        sys.exit("input needs both a video and an audio track")
    return float(info["format"]["duration"])


# ---------------------------------------------------------------- transcription

def transcribe(src, model_name, cache):
    if cache.exists():
        print(f"[1/5] using cached transcript {cache.name}")
        return json.loads(cache.read_text())
    print(f"[1/5] transcribing with whisper '{model_name}' (first run downloads the model)")
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="auto", compute_type="int8")
    # This prompt nudges whisper into writing out fillers instead of silently dropping them
    segments, _ = model.transcribe(
        str(src),
        language="en",
        word_timestamps=True,
        vad_filter=False,
        initial_prompt="Umm, so like, uh, I was, hmm, thinking about it, you know.",
    )
    words = []
    for seg in segments:
        for w in seg.words:
            words.append({"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)})
    cache.write_text(json.dumps(words, indent=1))
    return words


def norm(word):
    return re.sub(r"[^a-z0-9']", "", word.lower())


# ---------------------------------------------------------------- cutting

def plan_cuts(words, duration, max_gap, pad_in, pad_out):
    """Keep only stretches of real speech. Fillers and long pauses get cut out."""
    speech = [w for w in words if norm(w["word"]) not in FILLERS and w["end"] > w["start"]]
    if not speech:
        sys.exit("no speech found in the clip")

    groups = [[speech[0]]]
    for w in speech[1:]:
        if w["start"] - groups[-1][-1]["end"] <= max_gap:
            groups[-1].append(w)
        else:
            groups.append([w])

    segs = []
    for i, g in enumerate(groups):
        s = max(0.0, g[0]["start"] - pad_in)
        e = min(duration, g[-1]["end"] + pad_out)
        if segs and s < segs[-1][1]:  # pads overlap, split the difference
            mid = (segs[-1][1] + s) / 2
            segs[-1][1], s = mid, mid
        segs.append([s, e])

    # snap to the frame grid so audio and video cut on the same sample
    snapped = []
    for s, e in segs:
        s, e = round(s * FPS) / FPS, round(e * FPS) / FPS
        if e - s >= 2 / FPS:
            snapped.append((s, e))
    return snapped, speech


def remap(words, segs):
    """Move word timings from the raw clip onto the cut timeline."""
    out, offset = [], 0.0
    for s, e in segs:
        for w in words:
            if w["start"] >= s - 0.05 and w["end"] <= e + 0.05:
                out.append({
                    "word": w["word"],
                    "start": max(0.0, offset + w["start"] - s),
                    "end": offset + min(w["end"], e) - s,
                })
        offset += e - s
    return out, offset


# ---------------------------------------------------------------- captions

def keyword_kind(word):
    n = norm(word)
    if re.search(r"\d", n) or n in NUMBER_WORDS:
        return "num"
    if n in NEGATIVE_WORDS:
        return "neg"
    if n in EMPHASIS_WORDS:
        return "emph"
    return None


def group_lines(words, max_words):
    lines, cur = [], []
    for w in words:
        if cur and (
            len(cur) >= max_words
            or w["start"] - cur[-1]["end"] > 0.4
            or w["end"] - cur[0]["start"] > 2.2
        ):
            lines.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.?!,]$", w["word"]):
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    return lines


def pick_keyword(line):
    """One highlight per line. Stats beat shock words beat hype words beat the longest noun."""
    for kind in ("num", "neg", "emph"):
        for i, w in enumerate(line):
            if keyword_kind(w["word"]) == kind:
                return i, kind
    content = [(len(norm(w["word"])), i) for i, w in enumerate(line) if norm(w["word"]) not in STOPWORDS]
    if content:
        length, i = max(content)
        if length >= 4:
            return i, "noun"
    return None, None


def ass_time(t):
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def clean(text):
    return re.sub(r"[.,;:]+$", "", re.sub(r"[{}\\]", "", text)).upper()


def color_word(text, kind, base_fs):
    if kind == "num":
        return f"{{\\c{BLUE}}}{text}{{\\c&H00FFFFFF&}}"
    if kind == "neg":
        return f"{{\\c{RED}}}{text}{{\\c&H00FFFFFF&}}"
    if kind == "emph":
        return f"{{\\c{YELLOW}}}{text}{{\\c&H00FFFFFF&}}"
    return f"{{\\fs{int(base_fs * 1.15)}}}{text}{{\\fs{base_fs}}}"


def build_ass(words, total, hook_text, hook_dur, max_words, path):
    lines = group_lines(words, max_words)
    events, punches = [], []

    for idx, line in enumerate(lines):
        start = line[0]["start"]
        nxt = lines[idx + 1][0]["start"] if idx + 1 < len(lines) else total
        end = min(nxt, line[-1]["end"] + 0.35)
        k, kind = pick_keyword(line)
        parts = []
        for i, w in enumerate(line):
            t = clean(w["word"])
            if i == k:
                t = color_word(t, kind, CAPTION_FS)
                if kind in ("num", "neg", "emph"):
                    punches.append(line[i]["start"])
            parts.append(t)
        # hook window captions run at 80% so they don't fight the hook text
        size = 80 if start < hook_dur else 100
        pop = f"{{\\fad(100,80)\\fscx{size * 0.85:.0f}\\fscy{size * 0.85:.0f}\\t(0,120,\\fscx{size}\\fscy{size})}}"
        events.append(f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{pop}{' '.join(parts)}")

    if hook_text:
        hw = [clean(w) for w in hook_text.split()]
        hk, hkind = pick_keyword([{"word": w} for w in hw])
        if hk is not None:  # hook always gets a pop colour, default yellow
            hw[hk] = color_word(hw[hk], hkind if hkind in ("num", "neg") else "emph", HOOK_FS)
        hook = " ".join(hw)
        if len(hw) > 3:  # wrap long hooks onto two lines
            mid = (len(hw) + 1) // 2
            hook = " ".join(hw[:mid]) + "\\N" + " ".join(hw[mid:])
        anim = "{\\fad(0,200)\\fscx40\\fscy40\\t(0,180,\\fscx112\\fscy112)\\t(180,300,\\fscx100\\fscy100)}"
        events.insert(0, f"Dialogue: 2,{ass_time(0)},{ass_time(hook_dur)},Hook,,0,0,0,,{anim}{hook}")

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,DM Sans ExtraBold,{CAPTION_FS},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,7,4,2,80,80,{int(H * 0.27)},1
Style: Hook,DM Sans ExtraBold,{HOOK_FS},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,2,-2,1,9,5,8,70,70,{int(H * 0.16)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    path.write_text(header + "\n".join(events) + "\n")

    # SRT too, handy for uploading as a separate caption track
    srt = []
    for i, line in enumerate(lines, 1):
        a, b = line[0]["start"], line[-1]["end"]
        fmt = lambda t: f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d},{int(t * 1000 % 1000):03d}"
        srt.append(f"{i}\n{fmt(a)} --> {fmt(b)}\n{' '.join(w['word'] for w in line)}\n")
    path.with_suffix(".srt").write_text("\n".join(srt))
    return punches


# ---------------------------------------------------------------- zoom plan

def zoom_expr(segs_out, punches, hook_dur):
    """zoompan expression: 1.25x -> 1.0x opener, fake 'cuts' in the hook, punch zoom on keywords."""
    terms = [f"if(lte(on,{int(1.5 * FPS)}),0.25-0.25*on/{int(1.5 * FPS)},0)"]

    # hook window: alternate framing at every jump cut (and at least every 1.5s) so no shot sits >2s
    hook_cuts = sorted({round(t, 2) for t in segs_out if 1.4 < t < hook_dur})
    t, filled = 1.5, []
    for c in hook_cuts + [hook_dur]:
        while c - t > 1.6:
            t += 1.5
            filled.append(t)
        filled.append(c)
        t = c
    filled = sorted(set(round(x, 2) for x in filled if x < hook_dur))
    marks = [1.5] + filled + [hook_dur]
    for i in range(len(marks) - 1):
        if i % 2 == 0:
            a, b = int(marks[i] * FPS), int(marks[i + 1] * FPS)
            terms.append(f"if(between(on,{a},{b - 1}),0.12,0)")

    # punch zoom on stat / shock / hype words after the hook, spaced out so it doesn't get seasick
    last = -10.0
    for p in sorted(punches):
        if p < hook_dur or p - last < 2.5:
            continue
        f = int(p * FPS)
        terms.append(f"if(between(on,{f},{f + 3}),0.05,if(between(on,{f + 4},{f + 7}),0.05-0.0125*(on-{f + 3}),0))")
        last = p
        if len(terms) > 60:
            break

    hook_marks = marks[1:-1]
    return "1+" + "+".join(terms), hook_marks


# ---------------------------------------------------------------- sfx

def synth_sfx(cache):
    """Fallback SFX made with ffmpeg if Assets/sfx doesn't have them."""
    cache.mkdir(parents=True, exist_ok=True)
    specs = {
        "whoosh.wav": ["-f", "lavfi", "-i", "anoisesrc=d=0.6:c=pink:a=0.9",
                       "-af", "bandpass=f=1200:width_type=o:w=2,afade=t=in:d=0.25,afade=t=out:st=0.3:d=0.3,volume=2"],
        "riser.wav": ["-f", "lavfi", "-i",
                      "aevalsrc='0.4*sin(2*PI*(150*t+220*t*t))+0.15*(random(0)*2-1)*t':d=1.5:s=44100",
                      "-af", "afade=t=in:d=1.4:curve=exp,highpass=f=120"],
        "boom.wav": ["-f", "lavfi", "-i",
                     "aevalsrc='0.9*sin(2*PI*(45+90*exp(-t*18))*t)*exp(-t*3.5)':d=1.2:s=44100",
                     "-af", "lowpass=f=400,volume=2"],
    }
    out = {}
    for name, args in specs.items():
        p = cache / name
        if not p.exists():
            run(["ffmpeg", "-y", *args, p])
        out[name.split(".")[0]] = p
    return out


def find_asset(folder, names):
    d = ASSETS / folder
    for n in names:
        if (d / n).exists():
            return d / n
    return None


def pick_music(arg):
    if arg == "none":
        return None
    if arg:
        p = Path(arg)
        if not p.exists():
            sys.exit(f"music not found: {arg}")
        return p
    d = ASSETS / "music"
    tracks = sorted(p for p in d.glob("*") if p.suffix.lower() in {".mp3", ".wav", ".m4a", ".aac", ".ogg"}) if d.exists() else []
    if not tracks:
        return None
    default = d / "Deliberate-Thought.mp3"
    return default if default.exists() else random.choice(tracks)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Auto edit a raw clip into a finished short")
    ap.add_argument("input")
    ap.add_argument("-o", "--output", help="output path (default: output/<name>_edit.mp4)")
    ap.add_argument("--hook", help="hook text for the first seconds (default: first sentence you say)")
    ap.add_argument("--no-hook", action="store_true")
    ap.add_argument("--hook-duration", type=float, default=3.0)
    ap.add_argument("--music", help="music file, or 'none' (default: Assets/music)")
    ap.add_argument("--music-volume", type=float, default=0.22)
    ap.add_argument("--max-gap", type=float, default=0.30, help="pauses longer than this get cut (sec)")
    ap.add_argument("--pad", type=float, default=0.08, help="breathing room kept around speech (sec)")
    ap.add_argument("--words-per-line", type=int, default=4)
    ap.add_argument("--fit", choices=["crop", "blur"], default="crop", help="how to make horizontal footage vertical")
    ap.add_argument("--model", default="small", help="whisper model: tiny, base, small, medium, large-v3")
    ap.add_argument("--keep-temp", action="store_true")
    args = ap.parse_args()

    src = Path(args.input).resolve()
    if not src.exists():
        sys.exit(f"not found: {src}")
    out = Path(args.output) if args.output else ROOT / "output" / f"{src.stem}_edit.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="jcube_"))

    duration = probe(src)
    words = transcribe(src, args.model, out.with_suffix(".words.json"))

    print("[2/5] planning cuts")
    segs, speech = plan_cuts(words, duration, args.max_gap, args.pad, args.pad + 0.04)
    out_words, total = remap(speech, segs)
    print(f"      {duration:.1f}s raw -> {total:.1f}s cut ({len(segs)} segments, {duration - total:.1f}s removed)")

    hook = None
    if not args.no_hook:
        if args.hook:
            hook = args.hook
        else:
            first = []
            for w in out_words:
                first.append(w["word"])
                if re.search(r"[.?!]$", w["word"]) or len(first) >= 7:
                    break
            hook = re.sub(r"[.,]$", "", " ".join(first))
        print(f"      hook: {hook.upper()}")

    hook_dur = min(args.hook_duration, total)
    ass = out.with_suffix(".ass")
    punches = build_ass(out_words, total, hook, hook_dur, args.words_per_line, ass)

    cut_points, acc = [], 0.0
    for s, e in segs[:-1]:
        acc += e - s
        cut_points.append(acc)
    zexpr, hook_marks = zoom_expr(cut_points, punches, min(5.0, total))

    # ---- pass 1: cut + reframe + zooms
    print("[3/5] cutting and reframing")
    sel = "+".join(f"between(t,{s:.4f},{e - 0.5 / FPS:.4f})" for s, e in segs)
    asel = "+".join(f"between(t,{s:.4f},{e:.4f})" for s, e in segs)
    if args.fit == "blur":
        frame = (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:5[bg];"
                 f"[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2")
    else:
        frame = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    graph = (
        f"[0:v]fps={FPS},select='{sel}',setpts=N/FRAME_RATE/TB,{frame},setsar=1,"
        f"zoompan=z='{zexpr}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS}[v];"
        f"[0:a]aresample=48000,aselect='{asel}',asetpts=N/SR/TB[a]"
    )
    (tmp / "pass1.txt").write_text(graph)
    cut = tmp / "cut.mp4"
    run(["ffmpeg", "-y", "-i", src, "-filter_complex_script", tmp / "pass1.txt", "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-c:a", "pcm_s16le", cut.with_suffix(".mov")])
    cut = cut.with_suffix(".mov")

    # ---- pass 2: captions, hook, music, sfx, loudness
    print("[4/5] captions, music and sound design")
    sfx = synth_sfx(tmp / "sfx")
    whoosh = find_asset("sfx", ["whoosh-sfx.mp3"]) or sfx["whoosh"]
    riser = find_asset("sfx", ["riser.mp3", "riser.wav"]) or sfx["riser"]
    boom = find_asset("sfx", ["vine-boom.mp3", "impact-cinematic-boom.mp3"]) or sfx["boom"]
    music = pick_music(args.music)
    overlay = find_asset("overlays", ["jessecubez_sub_like_overlay.webm"])

    inputs = ["-i", cut]
    mix, labels, n = [], [], 1

    voice = "[0:a]highpass=f=80,acompressor=threshold=-20dB:ratio=3:attack=5:release=100:makeup=2,asplit=2[voice][sc]"
    mix.append(voice)
    labels.append("[voice]")

    if music:
        print(f"      music: {music.name}")
        inputs += ["-stream_loop", "-1", "-i", music]
        fo = max(0.0, total - 1.5)
        mix.append(f"[{n}:a]aresample=48000,atrim=0:{total:.3f},asetpts=PTS-STARTPTS,volume={args.music_volume},"
                   f"afade=t=in:st=0:d=1,afade=t=out:st={fo:.3f}:d=1.5[mus]")
        mix.append("[mus][sc]sidechaincompress=threshold=0.04:ratio=6:attack=15:release=350[duck]")
        labels.append("[duck]")
        n += 1
    else:
        print("      music: none (drop tracks in Assets/music or pass --music)")
        mix.append("[sc]anullsink")

    first_cut = hook_marks[0] if hook_marks else 1.5
    cues = [(whoosh, 0.0, 0.7), (riser, max(0.0, first_cut - 1.5), 0.5), (boom, first_cut, 0.6)]
    cues += [(whoosh, t, 0.4) for t in hook_marks[1:]]
    for i, (f, at, vol) in enumerate(cues):
        if at >= total:
            continue
        inputs += ["-i", f]
        ms = int(at * 1000)
        mix.append(f"[{n}:a]aresample=48000,volume={vol},adelay={ms}|{ms}[s{i}]")
        labels.append(f"[s{i}]")
        n += 1

    mix.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0,"
               f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")

    vchain = "[0:v]"
    if overlay:
        inputs += ["-c:v", "libvpx-vp9", "-i", overlay]
        half = total / 2
        mix.append(f"[{n}:v]setpts=PTS-STARTPTS+{half:.3f}/TB,scale={W}:-2[ov]")
        mix.append(f"[0:v][ov]overlay=(W-w)/2:(H-h)/2:eof_action=pass[vo]")
        vchain = "[vo]"
        n += 1

    ass_esc = str(ass).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    fonts_esc = str(FONTS).replace(":", "\\:")
    mix.append(f"{vchain}ass='{ass_esc}':fontsdir='{fonts_esc}'[vout]")
    (tmp / "pass2.txt").write_text(";\n".join(mix))

    print("[5/5] rendering final")
    run(["ffmpeg", "-y", *inputs, "-filter_complex_script", tmp / "pass2.txt",
         "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out])

    (out.with_suffix(".edl.json")).write_text(json.dumps({
        "source": str(src), "segments": segs, "output_duration": round(total, 3),
        "hook": hook, "music": str(music) if music else None,
    }, indent=1))
    if not args.keep_temp:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\ndone -> {out}")


if __name__ == "__main__":
    main()
