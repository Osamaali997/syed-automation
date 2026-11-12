"""Adds a voiceover to the promo video: generates each line with Microsoft Edge TTS (free), fits it into its
time window, ducks the music under the voice, and writes voiceover/out/Syed_Automation_Promo_VO_<lang>.mp4
Usage:  python voiceover/make_voiceover.py --lang en   (or ur)   [--voice en-US-GuyNeural]   [--fake]"""
import argparse, asyncio, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--lang", default="en")
ap.add_argument("--voice", default="")
ap.add_argument("--fake", action="store_true", help="test mode: no internet, synthetic placeholder 'speech'")
a = ap.parse_args()
lang = (a.lang or "en").strip().lower()
cfg = json.load(open(os.path.join(HERE, f"script_{lang}.json"), encoding="utf-8"))
voice = a.voice.strip() or cfg["voice"]
out_dir = os.path.join(HERE, "out"); os.makedirs(out_dir, exist_ok=True)
tmp = os.path.join(out_dir, "tmp"); os.makedirs(tmp, exist_ok=True)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1500:]); sys.exit(1)
    return r.stdout


def duration(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path]).strip())


async def tts(text, path):
    import edge_tts
    last = None
    for v in [voice] + cfg.get("fallback_voices", []):
        try:
            await edge_tts.Communicate(text, v, rate=cfg.get("rate", "+0%"), pitch=cfg.get("pitch", "+0Hz")).save(path)
            if os.path.getsize(path) > 2000:
                print("voice used:", v); return
        except Exception as e:
            last = e; print("voice failed:", v, e)
    raise RuntimeError(f"all voices failed: {last}")


def fake(text, path, dur):
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=170:duration={dur}", "-af",
         "tremolo=f=6:d=0.9,volume=0.8", "-c:a", "libmp3lame", path])


files = []
for i, ln in enumerate(cfg["lines"]):
    raw = os.path.join(tmp, f"vo{i}.mp3")
    if a.fake: fake(ln["text"], raw, max(1.5, 0.38 * len(ln["text"].split())))
    else: asyncio.run(tts(ln["text"], raw))
    d = duration(raw); win = ln["end"] - ln["start"]; fit = os.path.join(tmp, f"vo{i}_fit.mp3")
    tempo = min(1.35, max(1.0, d / win))
    if tempo > 1.0:
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-filter:a", f"atempo={tempo:.3f}", fit])
    else:
        fit = raw
    print(f"line {i + 1}: {d:.2f}s in a {win:.1f}s window, tempo {tempo:.2f}")
    files.append((fit, ln["start"]))

inputs = ["-i", os.path.join(HERE, "video_only.mp4"), "-i", os.path.join(HERE, "music.wav")]
parts, labels = [], []
for k, (f, st) in enumerate(files):
    inputs += ["-i", f]
    parts.append(f"[{k + 2}:a]aresample=44100,aformat=channel_layouts=stereo,adelay={int(st * 1000)}:all=1,volume=1.2[v{k}]")
    labels.append(f"[v{k}]")
graph = ";".join(parts) + ";" + "".join(labels) + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0,asplit=2[vo1][vo2];" \
    "[1:a]aformat=channel_layouts=stereo,volume=0.9[m];" \
    "[m][vo1]sidechaincompress=threshold=0.012:ratio=12:attack=15:release=350:makeup=1[md];" \
    "[md][vo2]amix=inputs=2:normalize=0:dropout_transition=0[mix];[mix]alimiter=limit=0.7:attack=3:release=60:level=disabled[a]"
dest = os.path.join(out_dir, f"Syed_Automation_Promo_VO_{lang}.mp4")
run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "0:v", "-map", "[a]",
     "-c:v", "copy", "-c:a", "aac", "-b:a", "224k", "-t", "34", "-movflags", "+faststart", dest])
print("done:", dest, os.path.getsize(dest) // 1024, "KB")
