#!/usr/bin/env python3
"""Auto-montage of a vertical talking-head reel: cut pauses, subtitles, loudness.

Needs only Python 3.9+, ffmpeg/ffprobe on PATH and a free Groq API key in the
GROQ_API_KEY environment variable (used for speech recognition).

Commands (run them in this order):
  check                               - verify ffmpeg, ffprobe and the Groq key
  phrases VIDEO --work DIR            - transcribe the raw take by phrases (to find retakes)
  cut VIDEO --work DIR [--drop A-B]   - remove pauses (and the dropped ranges) -> DIR/clean.mp4
  subs --work DIR [--fix old=new]     - transcribe clean.mp4 -> DIR/subs.ass + DIR/subs.txt
  finish --work DIR --out FILE        - 1080x1920, burned subtitles, -14 LUFS -> FILE
"""
from __future__ import annotations

import argparse
import array
import json
import math
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"
RATE = 16000           # analysis sample rate
FRAME = 0.03           # energy frame, seconds
MIN_PAUSE = 0.40       # shorter pauses are kept (natural breathing)
PAD_BEFORE = 0.12      # keep a little air before a word
PAD_AFTER = 0.20       # and after it, so word endings are not clipped
MIN_PIECE = 0.25       # drop speech blips shorter than this
W, H = 1080, 1920
TARGET_LUFS = -14.0


def die(msg: str) -> None:
    print("ОШИБКА: " + msg, file=sys.stderr)
    sys.exit(1)


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    if r.returncode != 0:
        die(f"{cmd[0]} завершился с ошибкой:\n{r.stderr[-1500:]}")
    return r


def duration(path: Path) -> float:
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(r.stdout.strip())


def video_size(path: Path) -> tuple[int, int, int]:
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height:stream_side_data=rotation", "-of", "json", str(path)])
    s = json.loads(r.stdout)["streams"][0]
    rot = 0
    for sd in s.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    w, h = s["width"], s["height"]
    if abs(rot) in (90, 270):
        w, h = h, w
    return w, h, rot


# ---------- check ----------

def cmd_check(_a) -> None:
    ok = True
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool):
            print(f"✓ {tool}")
        else:
            ok = False
            print(f"✗ {tool} не найден. Windows: winget install Gyan.FFmpeg · Mac: brew install ffmpeg")
    if shutil.which("ffmpeg"):
        filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace").stdout
        if re.search(r"\bsubtitles\b", filters):
            print("✓ ffmpeg умеет накладывать субтитры")
        else:
            ok = False
            print("✗ эта сборка ffmpeg не умеет субтитры (нет libass). Поставьте полную сборку: winget install Gyan.FFmpeg")
    if os.environ.get("GROQ_API_KEY"):
        print("✓ ключ GROQ_API_KEY задан")
    else:
        ok = False
        print("✗ нет ключа GROQ_API_KEY. Бесплатно: console.groq.com → API Keys, затем задайте переменную окружения")
    print("Готово к работе." if ok else "Чего-то не хватает - см. строки с ✗.")
    sys.exit(0 if ok else 1)


# ---------- speech recognition ----------

def extract_audio(video: Path, out: Path) -> Path:
    run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "libmp3lame", "-b:a", "64k", str(out)])
    return out


def groq_transcribe(audio: Path, lang: str, prompt: str = "") -> dict:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        die("нет ключа GROQ_API_KEY (запустите check)")
    boundary = uuid.uuid4().hex
    fields = [("model", GROQ_MODEL), ("language", lang), ("response_format", "verbose_json"),
              ("temperature", "0"), ("timestamp_granularities[]", "word"),
              ("timestamp_granularities[]", "segment")]
    if prompt:
        fields.append(("prompt", prompt))
    body = bytearray()
    for name, value in fields:
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{audio.name}\"\r\n"
             f"Content-Type: audio/mpeg\r\n\r\n").encode()
    body += audio.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(GROQ_URL, data=bytes(body), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "avtomontazh/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        if e.code == 403:
            die("Groq ответил 403: сервис недоступен из вашей сети. Включите VPN и повторите.\n" + detail)
        if e.code == 401:
            die("Groq не принял ключ (401). Проверьте GROQ_API_KEY.")
        die(f"Groq ответил {e.code}: {detail}")
    except urllib.error.URLError as e:
        die(f"нет связи с Groq ({e.reason}). Проверьте интернет или VPN.")
    return {}


def fmt_t(t: float) -> str:
    return f"{t:6.2f}"


def cmd_phrases(a) -> None:
    video, work = Path(a.video), Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    data = groq_transcribe(extract_audio(video, work / "raw.mp3"), a.lang, a.prompt)
    (work / "raw_transcript.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"[{fmt_t(s['start'])} - {fmt_t(s['end'])}] {s['text'].strip()}" for s in data.get("segments", [])]
    (work / "phrases.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nФразы сохранены: {work / 'phrases.txt'}")


# ---------- cutting by audio energy ----------

def frame_levels(video: Path) -> list[float]:
    """RMS level in dBFS for each FRAME of the audio track."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(RATE),
                          "-f", "s16le", "-"], capture_output=True)
    if raw.returncode != 0 or not raw.stdout:
        die("не удалось прочитать звук из видео:\n" + raw.stderr.decode("utf-8", "replace")[-800:])
    pcm = array.array("h")
    pcm.frombytes(raw.stdout[: len(raw.stdout) // 2 * 2])
    n = int(RATE * FRAME)
    out = []
    for i in range(0, len(pcm) - n + 1, n):
        chunk = pcm[i:i + n]
        rms = math.sqrt(sum(x * x for x in chunk) / n) or 1e-9
        out.append(20 * math.log10(rms / 32768))
    return out


def auto_floor(levels: list[float]) -> tuple[float, float, float]:
    s = sorted(levels)
    noise = s[int(len(s) * 0.10)]
    voice = s[int(len(s) * 0.90)]
    # Threshold sits closer to the noise than to the voice, so quiet word endings survive.
    return noise + (voice - noise) * 0.30, noise, voice


def speech_pieces(levels: list[float], floor: float, total: float) -> list[list[float]]:
    pieces: list[list[float]] = []
    start = None
    for i, lv in enumerate(levels + [-200.0]):
        t = i * FRAME
        if lv >= floor and start is None:
            start = t
        elif lv < floor and start is not None:
            pieces.append([start, t])
            start = None
    merged: list[list[float]] = []
    for p in pieces:
        if merged and p[0] - merged[-1][1] < MIN_PAUSE:
            merged[-1][1] = p[1]
        else:
            merged.append(p)
    merged = [p for p in merged if p[1] - p[0] >= MIN_PIECE]
    keep = [[max(0.0, s - PAD_BEFORE), min(total, e + PAD_AFTER)] for s, e in merged]
    out: list[list[float]] = []
    for p in keep:
        if out and p[0] <= out[-1][1]:
            out[-1][1] = max(out[-1][1], p[1])
        else:
            out.append(p)
    return out


def parse_drop(s: str) -> tuple[float, float]:
    m = re.fullmatch(r"\s*([\d.]+)\s*-\s*([\d.]+)\s*", s)
    if not m or float(m.group(1)) >= float(m.group(2)):
        die(f"неверный --drop «{s}», нужно ОТ-ДО в секундах, например 12.4-15.9")
    return float(m.group(1)), float(m.group(2))


def subtract(keep: list[list[float]], drops: list[tuple[float, float]]) -> list[list[float]]:
    for d0, d1 in drops:
        nxt = []
        for s, e in keep:
            if e <= d0 or s >= d1:
                nxt.append([s, e])
                continue
            if s < d0:
                nxt.append([s, d0])
            if e > d1:
                nxt.append([d1, e])
        keep = nxt
    return [p for p in keep if p[1] - p[0] >= MIN_PIECE]


def cmd_cut(a) -> None:
    video, work = Path(a.video), Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    total = duration(video)
    levels = frame_levels(video)
    floor, noise, voice = auto_floor(levels)
    if a.floor is not None:
        floor = a.floor
    keep = speech_pieces(levels, floor, total)
    keep = subtract(keep, [parse_drop(d) for d in a.drop])
    if not keep:
        die("после реза ничего не осталось - проверьте --floor и --drop")
    kept = sum(e - s for s, e in keep)
    print(f"фон {noise:.1f} дБ, голос {voice:.1f} дБ, порог тишины {floor:.1f} дБ")
    print(f"было {total:.1f} с → станет {kept:.1f} с (убрано {total - kept:.1f} с, кусков {len(keep)})")
    if voice - noise < 15:
        print("ВНИМАНИЕ: голос мало громче фона (меньше 15 дБ) - рез может задеть слова. "
              "Проверьте результат или задайте --floor вручную.")
    if a.dry_run:
        for s, e in keep:
            print(f"  оставляю {s:6.2f} - {e:6.2f}")
        return
    parts = []
    for i, (s, e) in enumerate(keep):
        parts.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}];"
                     f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d=0.01,afade=t=out:st={max(0.0, e - s - 0.01):.3f}:d=0.01[a{i}];")
    concat = "".join(f"[v{i}][a{i}]" for i in range(len(keep))) + f"concat=n={len(keep)}:v=1:a=1[v][a]"
    script = work / "cut_filter.txt"
    script.write_text("".join(parts) + concat, encoding="utf-8")
    out = work / "clean.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-filter_complex_script", str(script),
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "16", "-preset", "medium",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(out)])
    (work / "cut.json").write_text(json.dumps({"floor_db": round(floor, 1), "keep": keep, "drops": a.drop},
                                              indent=1), encoding="utf-8")
    print(f"Чистовик: {out}")


# ---------- subtitles ----------

def ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def chunk_words(words: list[dict], max_chars: int, max_dur: float) -> list[dict]:
    lines, cur = [], []
    for w in words:
        text = w["word"].strip()
        if not text:
            continue
        cand = " ".join([x["word"].strip() for x in cur] + [text])
        if cur and (len(cand) > max_chars or w["end"] - cur[0]["start"] > max_dur
                    or w["start"] - cur[-1]["end"] > 0.6):
            lines.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.!?…]$", text):
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    out = []
    for ln in lines:
        out.append({"start": ln[0]["start"], "end": ln[-1]["end"],
                    "text": " ".join(x["word"].strip() for x in ln)})
    for i in range(len(out) - 1):  # no flicker between neighbours
        if out[i + 1]["start"] - out[i]["end"] < 0.3:
            out[i]["end"] = out[i + 1]["start"]
    return out


def apply_fixes(text: str, fixes: dict[str, str]) -> str:
    for old, new in fixes.items():
        text = re.sub(rf"(?<!\w){re.escape(old)}(?!\w)", new, text, flags=re.IGNORECASE)
    return text


def cmd_subs(a) -> None:
    work = Path(a.work)
    clean = work / "clean.mp4"
    if not clean.exists():
        die(f"нет {clean} - сначала команда cut")
    tr_path = work / "clean_transcript.json"
    if tr_path.exists() and not a.retranscribe:
        data = json.loads(tr_path.read_text(encoding="utf-8"))
    else:
        data = groq_transcribe(extract_audio(clean, work / "clean.mp3"), a.lang, a.prompt)
        tr_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    words = data.get("words") or []
    if not words:
        die("распознаватель не вернул слова - проверьте, что в видео есть речь")
    fixes = dict(f.split("=", 1) for f in a.fix if "=" in f)
    lines = chunk_words(words, a.max_chars, 2.4)
    for ln in lines:
        ln["text"] = apply_fixes(ln["text"], fixes)
        if not a.keep_punct:
            ln["text"] = re.sub(r"[.,;:]+(?=\s|$)", "", ln["text"])
    margin = H - a.bottom
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Main,{a.font},{a.size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,"
        f"-1,0,0,0,100,100,0,0,1,7,2,2,90,90,{margin},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    events = "".join(f"Dialogue: 0,{ass_time(l['start'])},{ass_time(l['end'])},Main,,0,0,0,,{l['text']}\n"
                     for l in lines)
    (work / "subs.ass").write_text(header + events, encoding="utf-8-sig")
    (work / "subs.txt").write_text("\n".join(f"{l['start']:6.2f}  {l['text']}" for l in lines) + "\n",
                                   encoding="utf-8")
    print("\n".join(f"{l['start']:6.2f}  {l['text']}" for l in lines))
    print(f"\nСубтитры: {work / 'subs.ass'} (строк {len(lines)}). Ошибки распознавания правьте через --fix старое=новое")


# ---------- final render ----------

def measure_loudness(video: Path, pre: str) -> dict:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(video), "-vn", "-af",
                        f"{pre}loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
    if not m:
        die("не удалось измерить громкость:\n" + r.stderr[-800:])
    return json.loads(m.group(0))


def cmd_finish(a) -> None:
    work, out = Path(a.work), Path(a.out).resolve()
    clean, subs = work / "clean.mp4", work / "subs.ass"
    for p in (clean, subs):
        if not p.exists():
            die(f"нет {p} - сначала команды cut и subs")
    w, h, _ = video_size(clean)
    if w >= h:
        die(f"видео {w}x{h} не вертикальное - рилс снимают вертикально (9:16)")
    vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,subtitles=subs.ass"
    m = measure_loudness(clean, "")
    af = (f"loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:"
          f"linear=true,aresample=48000")
    out.parent.mkdir(parents=True, exist_ok=True)
    # Run inside the work folder: the subtitles filter breaks on Windows drive letters in paths.
    run(["ffmpeg", "-v", "error", "-y", "-i", "clean.mp4", "-vf", vf, "-af", af,
         "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)], cwd=work)
    final = measure_loudness(out, "")
    print(f"Готово: {out}")
    print(f"длительность {duration(out):.1f} с, громкость {float(final['input_i']):.1f} LUFS (цель {TARGET_LUFS})")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(fn=cmd_check)

    ph = sub.add_parser("phrases")
    ph.add_argument("video")
    ph.add_argument("--work", required=True)
    ph.add_argument("--lang", default="ru")
    ph.add_argument("--prompt", default="", help="words the recognizer should know, e.g. product names")
    ph.set_defaults(fn=cmd_phrases)

    c = sub.add_parser("cut")
    c.add_argument("video")
    c.add_argument("--work", required=True)
    c.add_argument("--drop", action="append", default=[], help="range to throw away, seconds of the raw take: 12.4-15.9")
    c.add_argument("--floor", type=float, default=None, help="silence threshold in dB (default: measured automatically)")
    c.add_argument("--dry-run", action="store_true")
    c.set_defaults(fn=cmd_cut)

    s = sub.add_parser("subs")
    s.add_argument("--work", required=True)
    s.add_argument("--lang", default="ru")
    s.add_argument("--prompt", default="")
    s.add_argument("--fix", action="append", default=[], help="fix a misheard word: старое=новое")
    s.add_argument("--max-chars", type=int, default=24)
    s.add_argument("--font", default="Arial")
    s.add_argument("--size", type=int, default=74)
    s.add_argument("--bottom", type=int, default=1560, help="y of the subtitle bottom edge on a 1080x1920 frame")
    s.add_argument("--keep-punct", action="store_true")
    s.add_argument("--retranscribe", action="store_true")
    s.set_defaults(fn=cmd_subs)

    f = sub.add_parser("finish")
    f.add_argument("--work", required=True)
    f.add_argument("--out", required=True)
    f.set_defaults(fn=cmd_finish)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
