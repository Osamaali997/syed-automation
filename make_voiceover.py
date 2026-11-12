"""Adds a voiceover to the promo video and makes several voice versions to choose from.
For every voice in script_<lang>.json: generate each line (Microsoft Edge neural TTS, free), polish it so it sounds
warmer and less 'flat', fit it into its scene window, duck the music under it, and write out/<name>.mp4.

REAL HUMAN VOICE (best): put your own recordings (or ElevenLabs downloads) next to this file, one file per line,
named en_1.mp3 ... en_6.mp3 (or ur_1.mp3 ... ur_6.mp3; .wav/.m4a also fine). They are used automatically as an
extra version called 'human'.

Usage:  python make_voiceover.py --lang all|en|ur   [--voice SomeVoiceName]   [--fake]"""
import argparse, asyncio, glob, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--lang", default="all")
ap.add_argument("--voice", default="")
ap.add_argument("--fake", action="store_true", help="test mode: no internet, synthetic placeholder speech")
a = ap.parse_args()
langs = ["en", "ur"] if a.lang.strip().lower() in ("", "all") else [a.lang.strip().lower()]
OUT = os.path.join(HERE, "out"); os.makedirs(OUT, exist_ok=True)

# gentle "make it sound like a person in a room" polish: low-cut, warmth + presence, light compression, tiny room
POLISH = ("highpass=f=85,equalizer=f=180:t=q:w=1.0:g=1.5,equalizer=f=3200:t=q:w=1.1:g=2.5,"
          "acompressor=threshold=-20dB:ratio=3:attack=6:release=90:makeup=3,aecho=0.9:0.88:48:0.14")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-1200:])
    return r.stdout


def duration(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path]).strip())


async def tts(text, path, voice, cfg):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=cfg.get("rate", "+0%"), pitch=cfg.get("pitch", "+0Hz"),
                               volume=cfg.get("volume", "+0%")).save(path)
    if os.path.getsize(path) < 2000:
        raise RuntimeError("empty audio")


def fake(path, dur):
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=170:duration={dur}", "-af",
         "tremolo=f=6:d=0.9,volume=0.8", "-c:a", "libmp3lame", path])


def build(lang, tag, raw_files, cfg):
    tmp = os.path.join(OUT, f"tmp_{lang}_{tag}"); os.makedirs(tmp, exist_ok=True)
    inputs = ["-i", os.path.join(HERE, "video_only.mp4"), "-i", os.path.join(HERE, "music.wav")]
    parts, labels = [], []
    for i, (ln, raw) in enumerate(zip(cfg["lines"], raw_files)):
        d = duration(raw); win = ln["end"] - ln["start"]
        tempo = min(1.35, max(1.0, d / win))
        fit = os.path.join(tmp, f"v{i}.wav")
        af = POLISH + (f",atempo={tempo:.3f}" if tempo > 1.0 else "")
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-af", af, "-ar", "44100", "-ac", "2", fit])
        print(f"   line {i + 1}: {d:.2f}s in {win:.1f}s window, tempo {tempo:.2f}")
        inputs += ["-i", fit]
        parts.append(f"[{i + 2}:a]aresample=44100,aformat=channel_layouts=stereo,adelay={int(ln['start'] * 1000)}:all=1,volume=1.25[v{i}]")
        labels.append(f"[v{i}]")
    graph = ";".join(parts) + ";" + "".join(labels) + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0,asplit=2[vo1][vo2];" \
        "[1:a]aformat=channel_layouts=stereo,volume=0.85[m];" \
        "[m][vo1]sidechaincompress=threshold=0.012:ratio=12:attack=15:release=350:makeup=1[md];" \
        "[md][vo2]amix=inputs=2:normalize=0:dropout_transition=0[mix];[mix]alimiter=limit=0.7:attack=3:release=60:level=disabled[a]"
    dest = os.path.join(OUT, f"Syed_Automation_Promo_VO_{lang}_{tag}.mp4")
    run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "224k", "-t", "34", "-movflags", "+faststart", dest])
    print("   ->", os.path.basename(dest), os.path.getsize(dest) // 1024, "KB")
    return dest


made, failed = [], []
for lang in langs:
    cfg = json.load(open(os.path.join(HERE, f"script_{lang}.json"), encoding="utf-8"))
    n = len(cfg["lines"])
    # 1) your own recordings, if present
    human = []
    for i in range(1, n + 1):
        hit = sorted(glob.glob(os.path.join(HERE, f"{lang}_{i}.*")))
        if hit: human.append(hit[0])
    if len(human) == n:
        print(f"[{lang}] human recordings found")
        try: made.append(build(lang, "human", human, cfg))
        except Exception as e: failed.append((lang, "human", str(e))); print("   FAILED:", e)
    # 2) AI voices
    voices = [a.voice.strip()] if a.voice.strip() else cfg["voices"]
    for v in voices:
        tag = v.replace("Neural", "").replace("Multilingual", "").split("-")[-1]
        print(f"[{lang}] voice {v}")
        try:
            raws = []
            for i, ln in enumerate(cfg["lines"]):
                p = os.path.join(OUT, f"raw_{lang}_{tag}_{i}.mp3")
                if a.fake: fake(p, max(1.5, 0.38 * len(ln["text"].split())))
                else: asyncio.run(tts(ln["text"], p, v, cfg))
                raws.append(p)
            made.append(build(lang, tag, raws, cfg))
        except Exception as e:
            failed.append((lang, v, str(e))); print("   FAILED:", e)
print("\nMADE:", [os.path.basename(m) for m in made])
if failed: print("FAILED:", [(l, v) for l, v, _ in failed])
for f in glob.glob(os.path.join(OUT, "raw_*")) + glob.glob(os.path.join(OUT, "tmp_*", "*")):
    os.remove(f)
for d in glob.glob(os.path.join(OUT, "tmp_*")): os.rmdir(d)
sys.exit(0 if made else 1)
