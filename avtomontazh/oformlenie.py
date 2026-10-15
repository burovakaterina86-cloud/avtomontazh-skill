#!/usr/bin/env python3
"""Second pass of the reel: animated design over the clean cut, sound effects and music.

Runs after `montage.py cut` and `montage.py subs` (needs WORK/clean.mp4 and WORK/clean_transcript.json).
The look is described in WORK/design.json; the video is drawn by HyperFrames (HTML -> video, Apache 2.0),
so Node.js (npx) is required. GSAP and the Golos Text font are downloaded once into the skill's .cache folder.

Commands:
  check                                   - verify Node.js/npx and HyperFrames
  build    --work DIR [--step 3|4]        - DIR/hf/index.html from design.json (no rendering)
  snapshot --work DIR --at 1,5,9 [--step] - frames with Instagram safe zones -> DIR/preview.jpg
  render   --work DIR --out FILE [--step] - video + music + sound effects + -14 LUFS -> FILE
  remix    --work DIR --out FILE [--step] - redo music and sound effects over the last render (seconds, no re-render)

Step 3 = windows, animation on spoken words, captions. Step 4 = plus frame changes and transitions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"
GSAP_URL = "https://cdn.jsdelivr.net/npm/gsap@3.15.0/dist/gsap.min.js"
FONT_URL = "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/golostext/GolosText%5Bwght%5D.ttf"
FPS, W, H = 30, 1080, 1920
TARGET_LUFS, PEAK = -14.0, -1.0
DURATION_TOL = 0.05
WINDOW_GAP = 0.06
SUB_Y = 1730              # bottom edge of captions: above Instagram's caption and buttons
SUB_MAX_CHARS = 24
HIGHLIGHT = "#ffd34d"
LAYOUTS = ("split", "visual", "speaker")
VISUAL = {"y": 560, "scale": 1.06}
PUSH_IN = 1.1
SR = 48000


def die(msg: str) -> None:
    print("ОШИБКА: " + msg, file=sys.stderr)
    sys.exit(1)


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    if r.returncode != 0:
        die(f"{Path(cmd[0]).name} завершился с ошибкой:\n{(r.stdout + r.stderr)[-1500:]}")
    return r


def q(t: float) -> float:
    return round(round(float(t) * FPS) / FPS, 4)


def npx() -> str:
    exe = shutil.which("npx") or shutil.which("npx.cmd")
    if not exe:
        die("не найден Node.js (npx). Windows: winget install OpenJS.NodeJS.LTS · Mac: brew install node")
    return exe


def duration(path: Path) -> float:
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]).stdout.strip())


def fetch(url: str, name: str) -> Path:
    """Download once into the skill's .cache folder."""
    out = CACHE / name
    if out.exists() and out.stat().st_size > 1000:
        return out
    CACHE.mkdir(exist_ok=True)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "avtomontazh/2.0"}), timeout=60) as r:
            out.write_bytes(r.read())
    except Exception as e:  # noqa: BLE001 - any network problem means the same advice
        die(f"не скачался {name} ({e}). Проверьте интернет или VPN и повторите.")
    return out


# ---------- design.json ----------

def norm_word(w: str) -> str:
    return re.sub(r"[^\w]", "", w.lower().replace("ё", "е"))


def find_word(words: list[dict], stem: str, after: float = 0.0) -> float | None:
    stem = norm_word(stem)
    for w in words:
        if norm_word(w["word"]).startswith(stem) and float(w["start"]) >= after - 1e-9:
            return round(float(w["start"]), 2)
    return None


def resolve(anchor, words: list[dict], what: str) -> float:
    """Seconds of the clean cut, or {"word": "стем", "after": <time or anchor>, "offset": 0.4}."""
    if isinstance(anchor, bool):
        die(f"{what}: время — число или {{\"word\": …}}")
    if isinstance(anchor, (int, float)):
        return float(anchor)
    if isinstance(anchor, dict) and isinstance(anchor.get("word"), str):
        after = anchor.get("after", 0.0)
        after = resolve(after, words, what) if isinstance(after, dict) else float(after)
        t = find_word(words, anchor["word"], after)
        if t is None:
            die(f"{what}: в речи нет слова «{anchor['word']}»" + (f" после {after:.2f} с" if after else "")
                + " — поправьте якорь в design.json")
        return t + float(anchor.get("offset", 0.0))
    die(f"{what}: время — число или {{\"word\": …}}, а не {anchor!r}")
    return 0.0


ANIMS = {
    "pulse": "tl.fromTo({s}, {{scale:1}}, {{scale:1.35, duration:0.16, yoyo:true, repeat:1, ease:'power2.out'}}, {t});",
    "pop": "tl.fromTo({s}, {{opacity:0, scale:0.6}}, {{opacity:1, scale:1, duration:0.32, ease:'back.out(1.8)'}}, {t});",
    "drop": "tl.fromTo({s}, {{opacity:0, y:-60}}, {{opacity:1, y:0, duration:0.22, ease:'power2.out'}}, {t});",
    "stamp": "tl.fromTo({s}, {{opacity:0, scale:1.8}}, {{opacity:1, scale:1, duration:0.26, ease:'power3.out'}}, {t});",
    "slide": "tl.fromTo({s}, {{opacity:0, x:-50}}, {{opacity:1, x:0, duration:0.3, ease:'power3.out'}}, {t});",
    "fade": "tl.fromTo({s}, {{opacity:0}}, {{opacity:1, duration:0.35, ease:'power2.out'}}, {t});",
    "type": "tl.fromTo({s}, {{clipPath:'inset(0 100% 0 0)'}}, {{clipPath:'inset(0 0% 0 0)', duration:0.6, ease:'none'}}, {t});",
    "fill": "tl.fromTo({s}, {{width:'0%'}}, {{width:'100%', duration:0.6, ease:'power2.out'}}, {t});",
    "typechars": "tl.from({s}, {{opacity:0, y:20, duration:0.12, stagger:0.07, ease:'power2.out'}}, {t});",
    "blink": "tl.to({s}, {{opacity:0, duration:0.25, yoyo:true, repeat:{n}, ease:'steps(1)'}}, {t});",
}
# Hand-drawn marks on the spoken word: marker behind the word or a double underline.
MARKS = {"highlight": {"behind": True, "width": 0.62, "alpha": 0.5, "dur": 0.45},
         "underline": {"behind": False, "width": 0.1, "alpha": 1.0, "dur": 0.4}}
MARK_COLOR = "#4ade80"


def load_spec(work: Path) -> dict:
    p = work / "design.json"
    if not p.exists():
        die(f"нет {p} — опишите оформление (образец в SKILL.md)")
    try:
        sp = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"design.json не читается: {e}")
    if not isinstance(sp, dict) or not sp.get("windows"):
        die("в design.json нет окон (windows)")
    for w in sp["windows"]:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", str(w.get("id", ""))):
            die(f"окно {w.get('id')!r}: id — латиница, цифры, дефис")
        for a in w.get("anims", []):
            if a.get("kind") not in ANIMS and a.get("kind") not in MARKS:
                die(f"окно {w['id']}: неизвестная анимация {a.get('kind')!r} (есть: {', '.join([*ANIMS, *MARKS])})")
            if not re.fullmatch(r"[#.a-z0-9 _-]+", str(a.get("sel", ""))):
                die(f"окно {w['id']}: селектор {a.get('sel')!r} — только #id и .класс")
            if "color" in a and not re.fullmatch(r"#[0-9a-fA-F]{6}", str(a["color"])):
                die(f"окно {w['id']}: цвет — #rrggbb, а не {a['color']!r}")
    for s in (sp.get("dynamics") or {}).get("schedule", []):
        if s.get("layout") not in LAYOUTS:
            die(f"dynamics: неизвестная раскладка {s.get('layout')!r} (есть: {', '.join(LAYOUTS)})")
    sfx_options(sp)
    music_options(sp)
    return sp


def load_words(work: Path) -> list[dict]:
    p = work / "clean_transcript.json"
    if not p.exists():
        die(f"нет {p} — сначала команда montage.py subs")
    words = json.loads(p.read_text(encoding="utf-8")).get("words") or []
    if not words:
        die("в расшифровке нет слов — запустите montage.py subs --retranscribe")
    return [{"word": str(w["word"]).strip(), "start": float(w["start"]), "end": float(w["end"])} for w in words if str(w["word"]).strip()]


# ---------- composition ----------

def apply_fixes(text: str, fixes: dict) -> str:
    for old, new in fixes.items():
        text = re.sub(rf"(?<!\w){re.escape(old)}(?!\w)", new, text, flags=re.IGNORECASE)
    return text


def caption_groups(words: list[dict], max_chars: int = SUB_MAX_CHARS) -> list[list[dict]]:
    groups, cur = [], []
    for w in words:
        cand = " ".join([x["word"] for x in cur] + [w["word"]])
        if cur and (len(cand) > max_chars or w["start"] - cur[-1]["end"] > 0.6):
            groups.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.!?…]$", w["word"]):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def captions(words: list[dict], fixes: dict, dur: float) -> list[str]:
    """One clip per spoken word: its group is shown, the word itself highlighted. Clips never overlap."""
    events = []
    for g in caption_groups(words):
        shown = [apply_fixes(re.sub(r"[,;:]+$", "", w["word"]), fixes) for w in g]
        for i, w in enumerate(g):
            txt = " ".join(f'<span class="hl">{x}</span>' if j == i else x for j, x in enumerate(shown))
            events.append([q(w["start"]), q(w["end"]), txt])
    for k in range(len(events) - 1):
        nxt = events[k + 1][0]
        events[k][1] = nxt if nxt - events[k][1] < 0.3 else events[k][1]
        events[k][1] = min(events[k][1], nxt)
    return [f'<div class="cap clip" id="cap-{n}" data-start="{a}" data-duration="{q(min(b, dur) - a)}" data-track-index="4">{t}</div>'
            for n, (a, b, t) in enumerate(events) if min(b, dur) > a]


def window_html(work: Path, w: dict) -> str:
    html = str(w.get("html", ""))
    for name in re.findall(r"\{\{logo:([a-z0-9\-]+)\}\}", html) + ([w["logo"]] if w.get("logo") else []):
        if not (work / "logos" / f"{name}.svg").exists():
            die(f"окно {w['id']}: нет логотипа {work / 'logos' / (name + '.svg')} — скачайте настоящий SVG (см. SKILL.md)")
    html = re.sub(r"\{\{logo:([a-z0-9\-]+)\}\}", r"logos/\1.svg", html)

    def img(m):
        found = sorted((work / "img").glob(m.group(1) + ".*")) if (work / "img").exists() else []
        if not found:
            die(f"окно {w['id']}: нет картинки {work / 'img' / m.group(1)}.*")
        return f"img/{found[0].name}"
    html = re.sub(r"\{\{img:([a-z0-9\-]+)\}\}", img, html)
    dots = '<i style="background:#ff5f57"></i><i style="background:#febc2e"></i><i style="background:#28c840"></i>'
    logo = f'<img src="logos/{w["logo"]}.svg">' if w.get("logo") else ""
    return (f'<div class="root"><div class="win" id="{w["id"]}-win"><div class="bar">{dots}<span>{w.get("title", "")}</span>'
            f'{logo}</div><div class="body">{html}</div></div></div>')


def windows_timing(sp: dict, words: list[dict], dur: float) -> list[tuple[dict, float, float]]:
    ws = sp["windows"]
    starts = [resolve(w.get("start", 0), words, f"окно {w['id']}: start") for w in ws]
    for i in range(1, len(starts)):
        if starts[i] <= starts[i - 1]:
            die(f"окно {ws[i]['id']} начинается в {starts[i]:.2f} с — не позже предыдущего ({starts[i - 1]:.2f} с)")
    out = []
    for i, w in enumerate(ws):
        end = resolve(w["end"], words, f"окно {w['id']}: end") if "end" in w else \
            (starts[i + 1] - WINDOW_GAP if i + 1 < len(ws) else dur)
        out.append((w, q(starts[i]), q(min(end, dur))))
    return out


def mark_path(kind: str, seed: int) -> str:
    """Hand-drawn line in fractions of the word box (with margins); same seed — same line."""
    rnd = random.Random(seed)
    j = lambda a: rnd.uniform(-a, a)  # noqa: E731
    if kind == "highlight":
        y0, y1 = 0.56 + j(0.03), 0.52 + j(0.03)
        return f"M {0.06 + j(0.01):.3f},{y0:.3f} Q 0.5,{(y0 + y1) / 2 + j(0.03):.3f} {0.94 + j(0.01):.3f},{y1:.3f}"
    a = f"M {0.07 + j(0.01):.3f},{0.8 + j(0.01):.3f} Q 0.5,{0.84 + j(0.01):.3f} {0.95 + j(0.01):.3f},{0.79 + j(0.01):.3f}"
    b = f"M {0.2 + j(0.02):.3f},{0.87 + j(0.01):.3f} Q 0.55,{0.89 + j(0.01):.3f} {0.86 + j(0.02):.3f},{0.86 + j(0.01):.3f}"
    return f"{a} {b}"


# The line is converted to pixels after the font loads: a stretched SVG breaks the dash animation.
MARK_JS = r"""function hfMark(id, sel, d, o) {
  const el = document.querySelector(sel); if (!el) return;
  el.style.position = 'relative'; if (getComputedStyle(el).display === 'inline') el.style.display = 'inline-block';
  if (o.behind) el.style.isolation = 'isolate';
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
  svg.id = id; svg.setAttribute('style', 'position:absolute;left:-10%;top:-30%;width:120%;height:160%;overflow:visible;pointer-events:none;opacity:0;'
    + (o.behind ? 'z-index:-1;' : ''));
  const path = document.createElementNS(ns, 'path');
  const at = {'fill': 'none', 'stroke': o.color, 'stroke-opacity': o.alpha, 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
              'pathLength': '1', 'stroke-dasharray': '1 1', 'stroke-dashoffset': '1'};
  for (const k in at) path.setAttribute(k, at[k]);
  svg.appendChild(path); el.appendChild(svg);
  const fit = () => {
    const w = el.offsetWidth * 1.2, h = el.offsetHeight * 1.6, fs = parseFloat(getComputedStyle(el).fontSize);
    svg.setAttribute('viewBox', '0 0 ' + w + ' ' + h);
    path.setAttribute('d', d.replace(/(-?[\d.]+),(-?[\d.]+)/g, (m, x, y) => (x * w).toFixed(1) + ',' + (y * h).toFixed(1)));
    path.setAttribute('stroke-width', (fs * o.width).toFixed(1));
  };
  fit(); if (document.fonts) document.fonts.ready.then(fit);
}"""

BASE_CSS = """
@font-face { font-family: "Golos Text"; src: url("fonts/GolosText.ttf"); font-weight: 400 900; font-display: block; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; width: 1080px; height: 1920px; overflow: hidden; background: #0c0e12; font-family: "Golos Text", sans-serif; }
#stage { position: relative; width: 1080px; height: 1920px; overflow: hidden; background: radial-gradient(120% 40% at 50% 0%, #1b2130 0%, #0c0e12 70%); }
.video-wrapper { position: absolute; left: 0; top: 0; width: 1080px; height: 1920px; overflow: hidden; }
.video-wrapper video { width: 100%; height: 100%; object-fit: cover; }
.fade-top { position: absolute; left: 0; top: 280px; width: 1080px; height: 140px; background: linear-gradient(#0c0e12, rgba(12,14,18,0)); }
#cards { position: absolute; left: 0; top: 0; width: 1080px; height: 1920px; transform-origin: 50% 0; }
.card-host { position: absolute; left: 0; top: 0; width: 1080px; height: 1248px; pointer-events: none; overflow: hidden; }
.card-host .card { position: relative; width: 100%; height: 100%; }
.cap { position: absolute; left: 64px; right: 64px; bottom: CAP_BOTTOMpx; text-align: center; font: 700 64px/1.15 "Golos Text"; color: #fff;
  -webkit-text-stroke: 10px #000; paint-order: stroke fill; }
.cap .hl { color: HIGHLIGHT; }
#fx { position: absolute; left: 0; top: 0; width: 1080px; height: 1920px; pointer-events: none; overflow: hidden; }
#curtain { position: absolute; top: -200px; left: 0; width: 1500px; height: 2320px; background: #0c0e12; border-right: 18px solid #4ade80;
  box-shadow: 30px 0 80px rgba(74,222,128,.45); transform: skewX(-12deg); }
#curtain .p { position: absolute; left: 520px; top: 1080px; transform: skewX(12deg); color: #4ade80; font: 900 120px/1 "Golos Text"; }
.zones { position: absolute; inset: 0; pointer-events: none; }
.zones div { position: absolute; }
"""

ZONES_HTML = ('<div class="zones">'
              '<div style="left:0;right:0;top:0;height:115px;background:rgba(255,0,0,.35)"></div>'
              '<div style="left:0;right:0;top:1248px;height:2px;background:#ff0"></div>'
              '<div style="left:0;right:0;top:1760px;height:80px;background:rgba(255,0,0,.35)"></div>'
              '<div style="left:64px;top:0;bottom:0;width:2px;background:#0ff"></div>'
              '<div style="right:64px;top:0;bottom:0;width:2px;background:#0ff"></div></div>')


def curtain_js(cuts: list[float]) -> list[str]:
    js = ["tl.set('#curtain', {x:-1700}, 0);"]
    for t in cuts:
        js += [f"tl.to('#curtain', {{x:-180, duration:0.24, ease:'power3.in'}}, {q(t - 0.24)});",
               f"tl.to('#curtain', {{x:1400, duration:0.3, ease:'power3.out'}}, {q(t + 0.06)});",
               f"tl.set('#curtain', {{x:-1700}}, {q(t + 0.4)});"]
    return js


def layout_js(schedule: list[tuple], dur: float, video_y: int) -> list[str]:
    js = []
    for i, (t, kind, vy) in enumerate(schedule):
        vy = VISUAL["y"] if vy is None else vy
        t, end = q(t), (q(schedule[i + 1][0]) if i + 1 < len(schedule) else dur)
        if kind == "split":
            js += [f"tl.set('#video-wrap', {{y:{video_y}, scale:1, opacity:1}}, {t});",
                   f"tl.set('#cards', {{y:0, scale:1, opacity:1}}, {t});", f"tl.set('#fade-top', {{opacity:1}}, {t});"]
        elif kind == "visual":
            js += [f"tl.set('#video-wrap', {{opacity:0}}, {t});", f"tl.set('#cards', {{y:{vy}, scale:{VISUAL['scale']}, opacity:1}}, {t});",
                   f"tl.set('#fade-top', {{opacity:0}}, {t});"]
        else:
            js += [f"tl.set('#cards', {{opacity:0}}, {t});", f"tl.set('#fade-top', {{opacity:0}}, {t});",
                   f"tl.fromTo('#video-wrap', {{y:0, scale:1, opacity:1}}, {{y:0, scale:{PUSH_IN}, opacity:1, duration:{q(end - t)}, ease:'none'}}, {t});"]
    return js


def compose_html(work: Path, sp: dict, words: list[dict], dur: float, step: int, zones: bool = False) -> str:
    if step not in (3, 4):
        die("шаг — 3 (оформление) или 4 (динамика)")
    video_y = int(sp.get("video_y", 300))
    hosts, js = [], []
    for i, (w, a, b) in enumerate(windows_timing(sp, words, dur)):
        hosts.append(f'<div class="card-host clip" id="{w["id"]}-host" data-start="{a}" data-duration="{q(b - a)}" '
                     f'data-track-index="2"><div class="card">{window_html(work, w)}</div></div>')
        host = f"'#{w['id']}-host'"
        js.append(f"tl.set({host}, {{opacity:1}}, 0);" if i == 0 or a <= 0 else
                  f"tl.fromTo({host}, {{opacity:0, y:-40}}, {{opacity:1, y:0, duration:0.3, ease:'power3.out'}}, {a});")
        if b < dur:
            js.append(f"tl.to({host}, {{opacity:0, duration:0.18, ease:'power2.in'}}, {q(b - 0.18)});")
        for an in w.get("anims", []):
            t = resolve(an["at"], words, f"окно {w['id']}: анимация {an['sel']}")
            if an["kind"] in MARKS:
                m, mid = MARKS[an["kind"]], f"mk-{len(js)}"
                o = {"behind": m["behind"], "width": m["width"], "alpha": m["alpha"],
                     "color": an.get("color", sp.get("mark_color", MARK_COLOR))}
                js += [f"hfMark('{mid}', '{an['sel']}', '{mark_path(an['kind'], len(js))}', {json.dumps(o)});",
                       f"tl.set('#{mid}', {{opacity:1}}, {q(t)});",
                       f"tl.fromTo('#{mid} path', {{strokeDashoffset:1}}, {{strokeDashoffset:0, duration:{m['dur']}, ease:'power2.inOut'}}, {q(t)});"]
                continue
            n = max(1, int((dur - t) / 0.5) * 2 - 1)
            js.append(ANIMS[an["kind"]].format(s=f"'{an['sel']}'", t=q(t), n=n))
    fx = ""
    if step == 4:
        dyn = sp.get("dynamics") or {}
        if not dyn.get("schedule"):
            die("для шага 4 в design.json нужен dynamics.schedule (где какой кадр)")
        sched = sorted(((resolve(s["at"], words, "dynamics.schedule"), s["layout"], s.get("y")) for s in dyn["schedule"]),
                       key=lambda x: x[0])
        js += layout_js(sched, dur, video_y)
        cuts = [t for t in (resolve(c, words, "dynamics.at") for c in dyn.get("at", [])) if 0 < t < dur]
        if cuts:
            fx = (f'<div id="fx" class="clip" data-start="0" data-duration="{q(dur)}" data-track-index="5">'
                  '<div id="curtain"><div class="p">&gt;_</div></div></div>')
            js += curtain_js(cuts)
    css = (BASE_CSS.replace("CAP_BOTTOM", str(H - int(sp.get("sub_y", SUB_Y)))).replace("HIGHLIGHT", HIGHLIGHT)
           + (HERE / "terminal.css").read_text(encoding="utf-8"))
    caps = captions(words, sp.get("fixes", {}), dur)
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><style>{css}</style></head>
<body><div id="stage" data-composition-id="reel" data-start="0" data-duration="{q(dur)}" data-fps="{FPS}" data-width="{W}" data-height="{H}">
<div class="video-wrapper" id="video-wrap" style="transform-origin:50% 35%"><video id="bg-video" src="input-video.mp4" playsinline data-has-audio="true" data-start="0" data-duration="{q(dur)}" data-track-index="1"></video></div>
<div class="fade-top" id="fade-top"></div>
<div id="cards">
{chr(10).join(hosts)}
</div>
{chr(10).join(caps)}
{fx}
{ZONES_HTML if zones else ""}
<script src="vendor/gsap.min.js"></script>
<script>{MARK_JS}
(function(){{const tl = window.gsap.timeline({{paused:true}});
tl.set('#video-wrap', {{y:{video_y}}}, 0);
{chr(10).join(js)}
window.__timelines = window.__timelines || {{}}; window.__timelines["reel"] = tl;}})();</script>
</div></body></html>"""


def stage(work: Path) -> Path:
    """HyperFrames project in WORK/hf: video with dense keyframes (a sparse GOP freezes on seek), fonts, GSAP, logos, images."""
    pub = work / "hf"
    for d in ("fonts", "vendor"):
        (pub / d).mkdir(parents=True, exist_ok=True)
    shutil.copy2(fetch(FONT_URL, "GolosText.ttf"), pub / "fonts" / "GolosText.ttf")
    shutil.copy2(fetch(GSAP_URL, "gsap.min.js"), pub / "vendor" / "gsap.min.js")
    for d in ("logos", "img"):
        if (work / d).exists():
            shutil.copytree(work / d, pub / d, dirs_exist_ok=True)
    clip, video = work / "clean.mp4", pub / "input-video.mp4"
    if not clip.exists():
        die(f"нет {clip} — сначала команда montage.py cut")
    if not video.exists() or video.stat().st_mtime < clip.stat().st_mtime:
        run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1",
             "-c:v", "libx264", "-crf", "18", "-g", str(FPS), "-keyint_min", str(FPS), "-r", str(FPS), "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", "-c:a", "aac", "-b:a", "192k", str(video)])
    return pub


def build(work: Path, step: int, zones: bool = False) -> tuple[Path, dict, list[dict], float]:
    sp, words = load_spec(work), load_words(work)
    pub = stage(work)
    dur = duration(pub / "input-video.mp4")
    (pub / "index.html").write_text(compose_html(work, sp, words, dur, step, zones), encoding="utf-8")
    return pub, sp, words, dur


# ---------- sound effects ----------

SOUNDS = {  # formula or noise, length s, gain dB, where the sound peaks from its start, s
    "whoosh": ("noise", 0.5, -8.0, 0.32),
    "click": ("0.9*sin(2*PI*2400*t)*exp(-t*160)+0.4*sin(2*PI*1200*t)*exp(-t*90)", 0.06, -17.0, 0.0),
    "pop": ("0.9*sin(2*PI*(380+900*exp(-t*55))*t)*exp(-t*38)", 0.14, -18.0, 0.0),
    "ding": ("0.6*sin(2*PI*1318*t)*exp(-t*5)+0.25*sin(2*PI*2636*t)*exp(-t*8)+0.12*sin(2*PI*3954*t)*exp(-t*12)", 1.2, -15.0, 0.0),
}
SFX_GAP = 0.45


def sfx_options(sp: dict) -> dict | None:
    raw = sp.get("sfx")
    if raw in (None, False):
        return None
    raw = {} if raw is True else raw
    if not isinstance(raw, dict):
        die("sfx: true или {volume, off, events}")
    vol = raw.get("volume", 0)
    if isinstance(vol, bool) or not isinstance(vol, (int, float)) or not -30 <= vol <= 10:
        die("sfx.volume — число дБ от −30 до 10")
    if [s for s in raw.get("off", []) if s not in SOUNDS]:
        die(f"sfx.off: звуки только из {', '.join(SOUNDS)}")
    for e in raw.get("events", []):
        if not isinstance(e, dict) or e.get("sound") not in SOUNDS or "at" not in e:
            die(f"sfx.events: {{\"at\": время, \"sound\": одно из {', '.join(SOUNDS)}}}")
        ev = e.get("volume", 0)
        if isinstance(ev, bool) or not isinstance(ev, (int, float)) or not -30 <= ev <= 20:
            die("sfx.events: volume — число дБ от −30 до 20")
    return {"volume": float(vol), "off": set(raw.get("off", [])), "events": raw.get("events", [])}


def sfx_events(sp: dict, words: list[dict], dur: float, step: int) -> list[tuple[float, str]]:
    """Whoosh on a transition, click on a window, pop on an element, ding on the last window (call to action); hook silent."""
    opt = sfx_options(sp)
    if not opt:
        return []
    ev = []
    wins = windows_timing(sp, words, dur)
    for i, (_, a, _) in enumerate(wins):
        if i and a > 0:
            ev.append((a, "ding" if i == len(wins) - 1 else "click"))
    for w in sp["windows"]:
        for an in w.get("anims", []):
            if an["kind"] in ("pop", "stamp", "drop"):
                ev.append((resolve(an["at"], words, "sfx"), "pop"))
    if step == 4:
        ev += [(resolve(c, words, "dynamics.at"), "whoosh") for c in (sp.get("dynamics") or {}).get("at", [])]
    # a sound the speaker names aloud ("whoosh!") is her own event: may be louder, never merged away by an auto one
    own = [(round(resolve(e["at"], words, "sfx.events"), 3), e["sound"], float(e.get("volume", 0))) for e in opt["events"]]
    auto = [(round(t, 3), s, None) for t, s in ev]
    out: list[tuple] = []
    for t, s, v in own + sorted(auto):
        if s in opt["off"] or not 0 <= t < dur or any(s == o[1] and abs(t - o[0]) < SFX_GAP for o in out):
            continue
        out.append((t, s, v))
    wh = [o[0] for o in out if o[1] == "whoosh"]
    keep = [o for o in out if o[1] in ("whoosh", "ding") or o[2] is not None or not any(abs(o[0] - x) < 0.3 for x in wh)]
    return [(t, s, v or 0.0) for t, s, v in sorted(keep, key=lambda o: o[0])]


def sound_file(name: str) -> Path:
    expr, d, _, _ = SOUNDS[name]
    out = CACHE / f"{name}-{hashlib.sha1(f'{expr}|{d}'.encode()).hexdigest()[:8]}.wav"
    if out.exists():
        return out
    CACHE.mkdir(exist_ok=True)
    if expr == "noise":
        src = f"anoisesrc=color=pink:duration={d}:sample_rate={SR}:seed=7"
        af = f"highpass=f=350,lowpass=f=5200,afade=t=in:d=0.3:curve=exp,afade=t=out:st=0.32:d={d - 0.32}:curve=log,volume=0.9"
    else:
        src, af = f"aevalsrc='{expr}':d={d}:s={SR}", f"afade=t=out:st={max(0.0, d - 0.02)}:d=0.02"
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-af", af, "-ac", "1", "-ar", str(SR), str(out)])
    return out


def mix_sfx(src: Path, out: Path, events: list[tuple[float, str]], volume: float) -> Path:
    inputs, chains = [], []
    for i, (t, s, extra) in enumerate(events, start=1):
        inputs += ["-i", str(sound_file(s))]
        chains.append(f"[{i}:a]aresample={SR},adelay={max(0, int(round((t - SOUNDS[s][3]) * 1000)))}:all=1,"
                      f"volume={SOUNDS[s][2] + volume + extra}dB[s{i}]")
    labels = "".join(f"[s{i}]" for i in range(1, len(events) + 1))
    graph = ";".join([f"[0:a]aresample={SR}[v]", *chains,
                      f"[v]{labels}amix=inputs={len(events) + 1}:duration=first:normalize=0:dropout_transition=0[a]"])
    run(["ffmpeg", "-v", "error", "-y", "-i", str(src), *inputs, "-filter_complex", graph, "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", str(out)])
    return out


# ---------- music ----------

MUSIC_DEFAULTS = {"level": -16.0, "start": 0.0, "fade_in": 0.6, "fade_out": 2.0}
AUDIO_EXT = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")


def music_options(sp: dict) -> dict | None:
    raw = sp.get("music")
    if raw in (None, False):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("file"), str):
        die("music: {\"file\": \"music/трек.mp3\"} — файл трека в рабочей папке")
    opt = {"file": raw["file"], "duck": bool(raw.get("duck", True))}
    for k, (lo, hi) in {"level": (-40, -6), "start": (0, 600), "fade_in": (0, 5), "fade_out": (0, 10)}.items():
        v = raw.get(k, MUSIC_DEFAULTS[k])
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            die(f"music.{k} — число от {lo} до {hi}")
        opt[k] = float(v)
    return opt


def measure(path: Path, extra: str = "") -> dict:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-vn", "-af",
                        f"{extra}loudnorm=I={TARGET_LUFS}:TP={PEAK}:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
    if not m:
        die("не удалось измерить громкость:\n" + r.stderr[-800:])
    return json.loads(m[-1])


def mix_music(src: Path, out: Path, work: Path, opt: dict) -> dict:
    """Music under the voice: `level` dB under it, ducked while she speaks, fades in and out; a short track loops."""
    base = work.resolve()
    track = (base / opt["file"]).resolve()
    if base not in track.parents or track.suffix.lower() not in AUDIO_EXT or not track.exists():
        die(f"music.file: аудиофайл ({', '.join(AUDIO_EXT)}) внутри рабочей папки, а не {opt['file']!r}")
    voice_i, music_i = float(measure(src)["input_i"]), float(measure(track)["input_i"])
    gain = round(voice_i + opt["level"] - music_i, 2)
    dur = duration(src)
    fo = min(opt["fade_out"], dur / 2)
    m = (f"[1:a]atrim=start={opt['start']},asetpts=PTS-STARTPTS,aresample={SR},aformat=channel_layouts=stereo,volume={gain}dB[m0]")
    if opt["duck"]:
        voice = f"[0:a]aresample={SR},aformat=channel_layouts=stereo,asplit=2[v][key]"
        duck = "[m0][key]sidechaincompress=threshold=0.05:ratio=2.5:attack=30:release=450[m1]"
    else:
        voice, duck = f"[0:a]aresample={SR},aformat=channel_layouts=stereo[v]", "[m0]anull[m1]"
    graph = ";".join([m, voice, duck, f"[m1]afade=t=in:d={opt['fade_in']},afade=t=out:st={round(dur - fo, 3)}:d={fo}[mf]",
                      "[v][mf]amix=inputs=2:duration=first:normalize=0:dropout_transition=0[a]"])
    run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-stream_loop", "-1", "-i", str(track), "-filter_complex", graph,
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-t", str(dur), str(out)])
    return {"file": track.name, "gain_db": gain}


# ---------- loudness and commands ----------

def normalize(src: Path, out: Path) -> dict:
    """Two passes, linear: the voice is not pumped. A peaky mix is gently limited first so linear mode fits."""
    pre = ""
    m = measure(src)
    if float(m["input_tp"]) - float(m["input_i"]) > (PEAK - TARGET_LUFS) - 0.3:
        lim = 10 ** ((float(m["input_i"]) + (PEAK - TARGET_LUFS) - 0.8) / 20)
        if lim >= 0.0625:
            pre = f"alimiter=limit={lim:.4f}:attack=3:release=60:level=disabled,"
            m = measure(src, pre)
    af = (f"{pre}loudnorm=I={TARGET_LUFS}:TP={PEAK}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true:print_format=json")
    r = subprocess.run(["ffmpeg", "-hide_banner", "-y", "-i", str(src), "-c:v", "copy", "-af", af, "-c:a", "aac", "-b:a", "192k",
                        "-ar", str(SR), "-shortest", "-movflags", "+faststart", str(out)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        die("громкость не применилась:\n" + r.stderr[-800:])
    mode = json.loads(re.findall(r"\{[^{}]*\"normalization_type\"[^{}]*\}", r.stderr)[-1]).get("normalization_type", "?")
    return {"lufs": float(measure(out)["input_i"]), "mode": mode}


def finish(work: Path, raw: Path, out: Path, step: int) -> None:
    sp, words = load_spec(work), load_words(work)
    dur = duration(work / "hf" / "input-video.mp4")
    report = {}
    mopt = music_options(sp)
    if mopt:
        report["музыка"] = mix_music(raw, work / "hf" / "with-music.mp4", work, mopt)
        raw = work / "hf" / "with-music.mp4"
    events = sfx_events(sp, words, dur, step)
    if events:
        raw = mix_sfx(raw, work / "hf" / "with-sfx.mp4", events, sfx_options(sp)["volume"])
        report["звуки"] = len(events)
    out.parent.mkdir(parents=True, exist_ok=True)
    loud = normalize(raw, out)
    got = duration(out)
    if abs(got - dur) > DURATION_TOL:
        die(f"длительность разошлась с чистовиком: {got:.2f} с против {dur:.2f} с")
    print(f"Готово: {out}")
    print(f"длительность {got:.1f} с, громкость {loud['lufs']:.1f} LUFS ({loud['mode']})"
          + "".join(f", {k}: {v}" for k, v in report.items()))
    if loud["mode"] != "linear":
        print("ВНИМАНИЕ: громкость выровнялась в режиме dynamic — может «дышать». Сделайте музыку тише (music.level).")


def cmd_check(_a) -> None:
    ok = True
    for tool in ("ffmpeg", "ffprobe"):
        print(("✓ " if shutil.which(tool) else "✗ нет ") + tool)
        ok &= bool(shutil.which(tool))
    exe = shutil.which("npx") or shutil.which("npx.cmd")
    if not exe:
        ok = False
        print("✗ нет Node.js (npx). Windows: winget install OpenJS.NodeJS.LTS · Mac: brew install node")
    else:
        r = subprocess.run([exe, "--yes", "hyperframes", "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        ok &= r.returncode == 0
        print(f"✓ HyperFrames {r.stdout.strip()}" if r.returncode == 0 else "✗ HyperFrames не запустился: " + (r.stderr or r.stdout)[-300:])
    print("Готово к оформлению." if ok else "Чего-то не хватает - см. строки с ✗.")
    sys.exit(0 if ok else 1)


def cmd_build(a) -> None:
    pub, *_ = build(Path(a.work), a.step)
    print(f"Собрано: {pub / 'index.html'}")


def cmd_snapshot(a) -> None:
    work = Path(a.work)
    pub, *_ = build(work, a.step, zones=True)
    shutil.rmtree(pub / "snapshots", ignore_errors=True)
    run([npx(), "--yes", "hyperframes", "snapshot", str(pub), "--at", a.at])
    sheets = sorted((pub / "snapshots").glob("contact-sheet*.jpg"))
    if not sheets:
        die("HyperFrames не собрал лист кадров")
    shutil.copy2(sheets[0], work / "preview.jpg")
    build(work, a.step)                       # safe zones only on the check sheet
    print(f"Кадры: {work / 'preview.jpg'} (красное — шапка и кнопки Instagram, жёлтая линия — край подписи, голубое — поля)")


def cmd_render(a) -> None:
    work = Path(a.work)
    pub, *_ = build(work, a.step)
    lint = subprocess.run([npx(), "--yes", "hyperframes", "lint", str(pub)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    errs = re.search(r"(\d+) error\(s\)", lint.stdout + lint.stderr)
    if errs and int(errs.group(1)) > 0:
        die("HyperFrames нашёл ошибки в оформлении:\n" + (lint.stdout + lint.stderr)[-1500:])
    raw = work / "hf" / "render.mp4"
    print("Рендер идёт несколько минут…", flush=True)
    run([npx(), "--yes", "hyperframes", "render", str(pub), "-o", str(raw), "--fps", str(FPS)])
    finish(work, raw, Path(a.out).resolve(), a.step)


def cmd_remix(a) -> None:
    work = Path(a.work)
    raw = work / "hf" / "render.mp4"
    if not raw.exists():
        die(f"нет {raw} — сначала render")
    finish(work, raw, Path(a.out).resolve(), a.step)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    for name, fn in (("build", cmd_build), ("snapshot", cmd_snapshot), ("render", cmd_render), ("remix", cmd_remix)):
        c = sub.add_parser(name)
        c.add_argument("--work", required=True)
        c.add_argument("--step", type=int, choices=(3, 4), default=4)
        if name == "snapshot":
            c.add_argument("--at", required=True, help="секунды через запятую: 1,5.5,12")
        if name in ("render", "remix"):
            c.add_argument("--out", required=True)
        c.set_defaults(fn=fn)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
