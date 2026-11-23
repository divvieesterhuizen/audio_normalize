#!/usr/bin/env python3
"""
normalize.py - even out the volume of movies and TV shows.

Quiet dialogue gets louder, explosions and music get quieter, and the whole
file ends up at a consistent, comfortable loudness. Video and subtitles are
copied untouched; only the audio track(s) are re-encoded. The original file
is replaced once the new one has been written and checked.

Run with no arguments for a small window to pick a file or folder, or:
    python3 normalize.py movie.mkv
    python3 normalize.py "/path/to/Season 1"
    python3 normalize.py ~/Videos --recursive --strength strong --stereo

Requires Python 3.8+. On first run, a private copy of ffmpeg/ffprobe for your
OS is downloaded into ./bin/ next to this script (nothing is installed
system-wide). The launchers (normalize.command / .sh / .bat) also fetch a
private Python into ./runtime/ if the computer doesn't have one.

How it works (per audio track):
    1. Dynamic levelling (ffmpeg `acompressor` + `dynaudnorm`) shrinks the
       gap between quiet and loud passages.
    2. Two-pass EBU R128 loudness normalisation (ffmpeg `loudnorm`) measures
       the result and applies one exact gain so the track lands on the
       target loudness (default -16 LUFS) with peaks kept below -1.5 dBTP.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".m4v", ".mov", ".avi", ".wmv", ".ts", ".m2ts",
    ".mts", ".webm", ".flv", ".mpg", ".mpeg",
}

# Tag written into every processed file so it is never processed twice.
MARKER_TAG = "AUDIO_NORMALIZED"

# How hard to squeeze the quiet/loud difference.
#   dynaudnorm: f = frame length (ms), g = smoothing window (frames, odd),
#               m = max gain boost, p = peak target
#   acompressor: catches short, sudden peaks (gunshots, slams)
PRESETS = {
    # Gentle: only evens out scene-to-scene differences (~15 s window).
    "light": "dynaudnorm=f=500:g=31:p=0.9:m=5",
    # Default: evens out within scenes (~5 s window) and tames sharp peaks.
    "medium": (
        "acompressor=threshold=-24dB:ratio=2.5:attack=10:release=300:makeup=2,"
        "dynaudnorm=f=250:g=21:p=0.9:m=10"
    ),
    # Night mode: very flat, whispers and explosions end up close together.
    "strong": (
        "acompressor=threshold=-30dB:ratio=4:attack=5:release=200:makeup=4,"
        "dynaudnorm=f=150:g=15:p=0.9:m=20"
    ),
}


class Fatal(Exception):
    """An error that stops the whole run (shown to the user, no traceback)."""


# --------------------------------------------------------------------------
# Output: works for both the terminal and the GUI log window
# --------------------------------------------------------------------------

class TerminalLog:
    """`temp` lines (progress) are overwritten by whatever is logged next."""

    def __init__(self):
        self.temp_len = 0

    def __call__(self, msg, temp=False):
        if self.temp_len:
            msg = "\r" + msg.ljust(self.temp_len)
        print(msg, end="" if temp else "\n", flush=True)
        self.temp_len = len(msg.lstrip("\r")) if temp else 0


log = TerminalLog()


def fmt_time(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


# --------------------------------------------------------------------------
# Self-contained ffmpeg: downloaded once into ./bin/<os>-<arch>/
# --------------------------------------------------------------------------

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

BIN_ROOT = Path(__file__).resolve().parent / "bin"

_RIEDL = "https://ffmpeg.martin-riedl.de/redirect/latest"
_BTBN = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"

# For each platform: list of (archive URL, checksum URL, checksum filename).
# Martin Riedl's builds ship ffmpeg and ffprobe as separate zips with a
# "<zip>.sha256" next to each; BtbN's Windows zip holds both, and the release
# has one shared checksums file.
DOWNLOADS = {
    "macos-arm64": [
        (f"{_RIEDL}/macos/arm64/snapshot/ffmpeg.zip", None, None),
        (f"{_RIEDL}/macos/arm64/snapshot/ffprobe.zip", None, None),
    ],
    "macos-x64": [
        (f"{_RIEDL}/macos/amd64/release/ffmpeg.zip", None, None),
        (f"{_RIEDL}/macos/amd64/release/ffprobe.zip", None, None),
    ],
    "linux-x64": [
        (f"{_RIEDL}/linux/amd64/release/ffmpeg.zip", None, None),
        (f"{_RIEDL}/linux/amd64/release/ffprobe.zip", None, None),
    ],
    "linux-arm64": [
        (f"{_RIEDL}/linux/arm64/release/ffmpeg.zip", None, None),
        (f"{_RIEDL}/linux/arm64/release/ffprobe.zip", None, None),
    ],
    "windows-x64": [
        (f"{_BTBN}/ffmpeg-master-latest-win64-gpl.zip",
         f"{_BTBN}/checksums.sha256", "ffmpeg-master-latest-win64-gpl.zip"),
    ],
    "windows-arm64": [
        (f"{_BTBN}/ffmpeg-master-latest-winarm64-gpl.zip",
         f"{_BTBN}/checksums.sha256", "ffmpeg-master-latest-winarm64-gpl.zip"),
    ],
}


def platform_key():
    import platform
    os_name = {"Darwin": "macos", "Linux": "linux",
               "Windows": "windows"}.get(platform.system())
    machine = platform.machine().lower()
    arch = ("arm64" if machine in ("arm64", "aarch64")
            else "x64" if machine in ("x86_64", "amd64") else None)
    if not os_name or not arch:
        return None
    return f"{os_name}-{arch}"


def _fetch_once(url, dest, label):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "audio-normalize"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        final_url = resp.geturl()
        if dest is None:
            return resp.read(), final_url
        total = int(resp.headers.get("Content-Length") or 0)
        done, shown = 0, -1
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                pct = int(done * 100 / total) if total else 0
                if label and total and pct != shown:
                    shown = pct
                    log(f"    {label}: {pct:3d}%  "
                        f"({done >> 20} / {total >> 20} MB)", temp=True)
        if total and done != total:
            raise RuntimeError("connection closed before download finished")
        if label:
            log(f"    {label}: done")
        return None, final_url


def _fetch(url, dest=None, label=None, attempts=3):
    """Download url to dest (or return bytes), retrying on network errors.
    Returns (data_or_None, final_url)."""
    for attempt in range(1, attempts + 1):
        try:
            return _fetch_once(url, dest, label)
        except Exception as e:
            if attempt == attempts or "CERTIFICATE_VERIFY_FAILED" in str(e):
                raise
            log(f"    network problem ({e}); retrying "
                f"({attempt + 1}/{attempts})...")


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_ffmpeg(key, bin_dir):
    import tempfile
    import zipfile

    exe = ".exe" if key.startswith("windows") else ""
    wanted = {f"ffmpeg{exe}", f"ffprobe{exe}"}
    log(f"First run: downloading ffmpeg for {key} (one time only)...")
    bin_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        for url, sum_url, sum_name in DOWNLOADS[key]:
            archive = Path(tmp) / "download.zip"
            name = url.rsplit("/", 1)[-1]
            _, final_url = _fetch(url, archive, f"downloading {name}")

            # Verify the download against the publisher's SHA-256 checksum.
            if sum_url is None:
                sum_url, sum_name = final_url + ".sha256", name
            sums, _ = _fetch(sum_url)
            expected = None
            for line in sums.decode().splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[-1].lstrip("*") == sum_name:
                    expected = parts[0].lower()
            if expected is None:
                raise RuntimeError(f"no checksum published for {sum_name}")
            if _sha256(archive) != expected:
                raise RuntimeError(f"checksum mismatch for {name}; "
                                   "download corrupted, try again")

            with zipfile.ZipFile(archive) as z:
                for member in z.infolist():
                    base = member.filename.rsplit("/", 1)[-1]
                    if base in wanted:
                        target = bin_dir / base
                        with z.open(member) as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        target.chmod(0o755)

    missing = [w for w in wanted if not (bin_dir / w).exists()]
    if missing:
        raise RuntimeError(f"download did not contain {', '.join(missing)}")


def setup_tools():
    """Use ./bin/<platform>/ffmpeg, downloading it the first time."""
    global FFMPEG, FFPROBE
    key = platform_key()
    if key is None or key not in DOWNLOADS:
        if shutil.which("ffmpeg") and shutil.which("ffprobe"):
            return  # unsupported platform for auto-download; use system copy
        raise Fatal("no automatic ffmpeg download for this platform; please "
                    "install ffmpeg yourself and make sure it is on your PATH")

    bin_dir = BIN_ROOT / key
    exe = ".exe" if key.startswith("windows") else ""
    ffmpeg, ffprobe = bin_dir / f"ffmpeg{exe}", bin_dir / f"ffprobe{exe}"

    if not (ffmpeg.exists() and ffprobe.exists()):
        try:
            download_ffmpeg(key, bin_dir)
        except Exception as e:  # network, SSL, checksum, ...
            for f in (ffmpeg, ffprobe):   # never leave a half-finished pair
                if f.exists():
                    f.unlink()
            hint = ""
            if "CERTIFICATE_VERIFY_FAILED" in str(e):
                hint = ("\n  (macOS python.org Python: run 'Install "
                        "Certificates.command' from your Python folder)")
            raise Fatal(f"could not download ffmpeg: {e}{hint}")

    FFMPEG, FFPROBE = str(ffmpeg), str(ffprobe)


# --------------------------------------------------------------------------
# ffmpeg / ffprobe wrappers
# --------------------------------------------------------------------------

def _no_console():
    """On Windows, stop each ffmpeg call flashing up a console window."""
    if os.name == "nt":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


def probe(path):
    """Return ffprobe's view of the file (streams + format)."""
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        **_no_console(),
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "ffprobe failed")
    return json.loads(out.stdout)


def run_ffmpeg(args, duration, label):
    """
    Run ffmpeg showing a one-line progress indicator.
    Returns ffmpeg's stderr (needed to read loudnorm's measurement).
    """
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-progress", "pipe:1",
           "-nostats"] + args
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", **_no_console(),
    )

    # Read stderr on a thread so the pipe can't fill up and block ffmpeg.
    err_chunks = []
    t = threading.Thread(target=lambda: err_chunks.append(proc.stderr.read()))
    t.daemon = True
    t.start()

    shown = -1
    for line in proc.stdout:
        if line.startswith("out_time_us="):
            try:
                pos = int(line.split("=", 1)[1]) / 1_000_000
            except ValueError:
                continue
            pct = int(min(100.0, pos / duration * 100)) if duration else 0
            if duration and pct != shown:
                shown = pct
                log(f"    {label}: {pct:3d}%  ({fmt_time(pos)} / "
                    f"{fmt_time(duration)})", temp=True)
    proc.wait()
    t.join()

    stderr = "".join(err_chunks)
    if proc.returncode != 0:
        log(f"    {label}: failed")
        tail = "\n".join(stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"ffmpeg failed during {label}:\n{tail}")
    log(f"    {label}: done")
    return stderr


def parse_loudnorm_json(stderr):
    """loudnorm prints a JSON block at the end of stderr; grab the last one."""
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, re.S)
    if not blocks:
        raise RuntimeError("could not read loudness measurement from ffmpeg")
    return json.loads(blocks[-1])


# --------------------------------------------------------------------------
# Core processing
# --------------------------------------------------------------------------

def build_chain(stream, opts):
    """Filters applied before loudnorm for one audio stream."""
    parts = []
    channels = int(stream.get("channels") or 2)
    if opts.stereo and channels > 2:
        # Downmix surround to stereo with the centre (dialogue) channel
        # weighted up, which is what usually makes dialogue hard to hear
        # on TV speakers / headphones.
        parts.append(
            "aresample=out_chlayout=stereo:center_mix_level=1.0:"
            "surround_mix_level=0.5:lfe_mix_level=0"
        )
    parts.append(PRESETS[opts.strength])
    return ",".join(parts)


def pick_codec(ext, channels):
    """Audio encoder + bitrate that suit the output container."""
    if ext == ".webm":
        return "libopus", f"{max(128, 64 * channels)}k"
    return "aac", ("192k" if channels <= 2 else f"{64 * channels}k")


def output_ext(src):
    ext = src.suffix.lower()
    if ext in (".mkv", ".mp4", ".m4v", ".mov", ".webm", ".ts", ".m2ts"):
        return src.suffix
    return ".mkv"   # safest container for anything unusual (avi, wmv, ...)


def already_normalized(info):
    tags = (info.get("format") or {}).get("tags") or {}
    return any(k.upper() == MARKER_TAG for k in tags)


def verify_output(path, src_info):
    """Make sure the new file is complete before the original is touched."""
    info = probe(path)

    def count(i, kind):
        return sum(1 for s in i["streams"] if s.get("codec_type") == kind)

    for kind in ("video", "audio"):
        if count(info, kind) != count(src_info, kind):
            raise RuntimeError(f"check failed: {kind} track count changed")
    src_dur = float((src_info.get("format") or {}).get("duration") or 0)
    new_dur = float((info.get("format") or {}).get("duration") or 0)
    if src_dur and abs(new_dur - src_dur) > max(2.0, src_dur * 0.01):
        raise RuntimeError(f"check failed: length changed from "
                           f"{fmt_time(src_dur)} to {fmt_time(new_dur)}")


def process_file(src, opts):
    log(f"\n==> {src.name}")

    info = probe(src)
    if already_normalized(info) and not opts.force:
        log("    skipped: already normalized (use --force to redo)")
        return "skipped"
    audio = [s for s in info["streams"] if s.get("codec_type") == "audio"]
    if not audio:
        log("    skipped: no audio tracks")
        return "skipped"
    duration = float(info.get("format", {}).get("duration") or 0)

    ext = output_ext(src)
    if opts.keep_original:
        dst = src.with_name(src.stem + ".normalized" + ext)
        if dst.exists() and not opts.force:
            log(f"    skipped: {dst.name} already exists")
            return "skipped"
    else:
        dst = src.with_suffix(ext)

    target = f"I={opts.target}:TP={opts.true_peak}:LRA={opts.lra}"
    log(f"    {len(audio)} audio track(s), {fmt_time(duration)} long, "
        f"strength={opts.strength}, target={opts.target} LUFS")

    # ---- Pass 1: measure each audio track after levelling ----------------
    measurements = []
    for i, s in enumerate(audio):
        stderr = run_ffmpeg(
            ["-i", str(src), "-map", f"0:a:{i}", "-vn", "-sn", "-dn",
             "-af", f"{build_chain(s, opts)},loudnorm={target}"
                    ":print_format=json",
             "-f", "null", "-"],
            duration, f"analysing track {i + 1}/{len(audio)}",
        )
        m = parse_loudnorm_json(stderr)
        measurements.append(m)
        log(f"      measured {float(m['input_i']):.1f} LUFS, "
            f"peak {float(m['input_tp']):.1f} dBTP, "
            f"range {float(m['input_lra']):.1f} LU")

    # ---- Pass 2: apply and write a temporary output ----------------------
    graphs, maps, codec_args = [], [], []
    for i, (s, m) in enumerate(zip(audio, measurements)):
        rate = s.get("sample_rate") or "48000"
        ln = (f"loudnorm={target}"
              f":measured_I={m['input_i']}:measured_TP={m['input_tp']}"
              f":measured_LRA={m['input_lra']}"
              f":measured_thresh={m['input_thresh']}"
              f":offset={m['target_offset']}:linear=true")
        # loudnorm works internally at 192 kHz; resample back afterwards.
        graphs.append(f"[0:a:{i}]{build_chain(s, opts)},{ln},"
                      f"aresample={rate}[a{i}]")
        maps += ["-map", f"[a{i}]"]

        channels = int(s.get("channels") or 2)
        if opts.stereo and channels > 2:
            channels = 2
        codec, bitrate = pick_codec(ext.lower(), channels)
        is_default = (s.get("disposition") or {}).get("default") == 1
        codec_args += [f"-c:a:{i}", codec, f"-b:a:{i}", bitrate,
                       f"-map_metadata:s:a:{i}", f"0:s:a:{i}",
                       f"-disposition:a:{i}", "default" if is_default else "0"]

    tmp = src.with_name(src.stem + ".partial" + ext)
    args = (
        ["-y", "-i", str(src), "-filter_complex", ";".join(graphs),
         "-map", "0:v?"] + maps +
        ["-map", "0:s?", "-map", "0:t?",
         "-map_metadata", "0", "-map_chapters", "0",
         "-metadata", f"{MARKER_TAG}={opts.strength} {opts.target} LUFS",
         "-c:v", "copy", "-c:s", "copy", "-c:t", "copy"] +
        codec_args
    )
    if ext.lower() in (".mp4", ".m4v", ".mov"):
        # MP4 carries text subtitles as mov_text; use_metadata_tags lets the
        # "already normalized" marker be stored; faststart helps streaming.
        args += ["-c:s", "mov_text",
                 "-movflags", "+faststart+use_metadata_tags"]
    args.append(str(tmp))

    try:
        run_ffmpeg(args, duration, "writing")
        verify_output(tmp, info)
        os.replace(tmp, dst)           # atomic swap over the original
    finally:
        if tmp.exists():
            tmp.unlink()

    if not opts.keep_original and dst != src:
        src.unlink()                   # container changed, e.g. .avi -> .mkv
        log(f"    replaced: {src.name} -> {dst.name}")
    elif opts.keep_original:
        log(f"    saved: {dst.name}")
    else:
        log("    replaced original")
    return "done"


def collect_files(target, recursive):
    if target.is_file():
        return [target]
    pattern = "**/*" if recursive else "*"
    return [
        p for p in sorted(target.glob(pattern))
        if p.is_file()
        and p.suffix.lower() in VIDEO_EXTENSIONS
        and not p.stem.endswith(".partial")
        and not p.name.startswith("._")      # macOS metadata files
    ]


def run_batch(target, opts):
    """Process a file or folder. Returns (done, skipped, failed)."""
    setup_tools()
    target = Path(target).expanduser()
    if not target.exists():
        raise Fatal(f"{target} does not exist")
    files = collect_files(target, opts.recursive)
    if not files:
        raise Fatal(f"no video files found in {target}")

    log(f"Found {len(files)} video file(s).")
    results = {"done": 0, "skipped": 0, "failed": 0}
    for f in files:
        try:
            results[process_file(f, opts)] += 1
        except RuntimeError as e:
            results["failed"] += 1
            log(f"    FAILED (original left untouched): {e}")

    log(f"\nFinished: {results['done']} normalized, "
        f"{results['skipped']} skipped, {results['failed']} failed.")
    return results


# --------------------------------------------------------------------------
# GUI (used when the program is started without a file/folder argument)
# --------------------------------------------------------------------------

def run_gui(opts):
    global log
    import queue
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext

    root = tk.Tk()
    root.title("Audio Normalize")
    root.geometry("640x420")
    root.minsize(420, 260)

    bar = tk.Frame(root, padx=8, pady=8)
    bar.pack(fill="x")
    tk.Label(bar, text="Even out loud/quiet audio. Originals are replaced."
             ).pack(side="left")
    btn_folder = tk.Button(bar, text="Choose folder...")
    btn_folder.pack(side="right")
    btn_file = tk.Button(bar, text="Choose file...")
    btn_file.pack(side="right", padx=(0, 6))

    text = scrolledtext.ScrolledText(root, height=15, state="disabled",
                                     font="TkFixedFont")
    text.pack(fill="both", expand=True, padx=8, pady=(0, 8))
    text.mark_set("temp_start", "end-1c")
    text.mark_gravity("temp_start", "left")

    q = queue.Queue()
    state = {"busy": False, "temp": False}
    log = lambda msg, temp=False: q.put(("log", msg, temp))  # noqa: E731

    def pump():
        while True:
            try:
                kind, *rest = q.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                msg, temp = rest
                text.configure(state="normal")
                if state["temp"]:
                    text.delete("temp_start", "end-1c")
                text.mark_set("temp_start", "end-1c")
                text.insert("end", msg + "\n")
                text.see("end")
                text.configure(state="disabled")
                state["temp"] = temp
            elif kind == "finished":
                state["busy"] = False
                btn_file.configure(state="normal")
                btn_folder.configure(state="normal")
                if rest[0]:
                    messagebox.showerror("Audio Normalize", rest[0])
        root.after(100, pump)

    def start(target):
        if not target or state["busy"]:
            return
        state["busy"] = True
        btn_file.configure(state="disabled")
        btn_folder.configure(state="disabled")

        def work():
            err = None
            try:
                run_batch(target, opts)
            except Fatal as e:
                err = str(e)
                log(f"error: {e}")
            except Exception as e:  # keep the window alive on anything else
                err = f"Unexpected error: {e}"
                log(err)
            q.put(("finished", err))

        threading.Thread(target=work, daemon=True).start()

    types = [("Video files", " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))),
             ("All files", "*")]
    btn_file.configure(command=lambda: start(
        filedialog.askopenfilename(title="Choose a video", filetypes=types)))
    btn_folder.configure(command=lambda: start(
        filedialog.askdirectory(title="Choose a folder of videos")))

    def on_close():
        if state["busy"] and not messagebox.askokcancel(
                "Audio Normalize",
                "Still working. Quit anyway? (The file being processed is "
                "left unchanged.)"):
            return
        root.destroy()
        os._exit(0)   # stop the worker thread and any running ffmpeg

    root.protocol("WM_DELETE_WINDOW", on_close)
    log("Choose a video file or a folder of videos to begin.")
    pump()
    root.mainloop()


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Even out loud/quiet audio in video files so you don't "
                    "have to keep reaching for the remote. Run without a "
                    "path to open a window instead.",
    )
    ap.add_argument("path", nargs="?",
                    help="a video file, or a folder of video files "
                         "(omit to open the window)")
    ap.add_argument("-s", "--strength", choices=PRESETS, default="medium",
                    help="how much to even out loud/quiet parts "
                         "(default: medium; 'strong' = night mode)")
    ap.add_argument("-t", "--target", type=float, default=-16.0,
                    help="target loudness in LUFS (default: -16, typical for "
                         "streaming/laptops; -23 or -24 = broadcast TV level)")
    ap.add_argument("--true-peak", type=float, default=-1.5,
                    help="maximum peak level in dBTP (default: -1.5)")
    ap.add_argument("--lra", type=float, default=11.0,
                    help="loudnorm loudness-range target in LU (default: 11)")
    ap.add_argument("--stereo", action="store_true",
                    help="downmix surround tracks to stereo with boosted "
                         "dialogue (good for TV speakers / headphones)")
    ap.add_argument("-r", "--recursive", action="store_true",
                    help="also process sub-folders (the window always does)")
    ap.add_argument("-k", "--keep-original", action="store_true",
                    help="don't replace originals; write NAME.normalized.EXT")
    ap.add_argument("--force", action="store_true",
                    help="process files even if already normalized")
    opts = ap.parse_args()

    if opts.path is None:
        opts.recursive = True
        try:
            run_gui(opts)
        except ImportError:
            sys.exit("error: this Python has no tkinter (GUI) support. Use "
                     "the normalize launcher, or pass a file/folder path.")
        return

    try:
        results = run_batch(opts.path, opts)
    except Fatal as e:
        sys.exit(f"error: {e}")
    except KeyboardInterrupt:
        print("\nStopped. The file being processed was left unchanged.")
        sys.exit(130)
    sys.exit(1 if results["failed"] else 0)


if __name__ == "__main__":
    main()
