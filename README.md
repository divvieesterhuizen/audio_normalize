# audio_normalize

Evens out the volume in movies and TV shows: quiet dialogue gets louder, and loud
action and music get softer. It works on macOS, Linux and Windows.

Video and subtitles are copied unchanged. Only the audio tracks are re-encoded, and
every audio track in the file is processed. **The original file is replaced** once
the new version has been written and checked.

## Everything stays in this folder

Nothing needs to be installed. The first time you run the program, it downloads what
it needs into this folder:

| Folder | What | When |
|---|---|---|
| `bin/<os>-<arch>/` | ffmpeg + ffprobe (≈ 60 MB) | First run, always |
| `runtime/python/` | Portable Python 3.13 (≈ 30 MB) | Only if the computer has no Python 3.8+ |

Each download is checked against the publisher's SHA-256 checksum. To uninstall,
delete the folder.

## Run it

| System | Window (pick a file or folder) | Terminal |
|---|---|---|
| macOS | Double-click `normalize.command` | `./normalize.sh "Movie.mkv"` |
| Linux | Run `./normalize.sh` | `./normalize.sh "Movie.mkv"` |
| Windows | Double-click `normalize.bat` | `normalize.bat "Movie.mkv"` |

The window has two buttons, **Choose file…** and **Choose folder…**. Choosing a
folder also processes its sub-folders.

> macOS may block `normalize.command` the first time because it was downloaded.
> If it does, right-click → **Open** → **Open**.

### Terminal examples

```bash
./normalize.sh "Movie.mkv"                       # one file
./normalize.sh "/Videos/Show/Season 1"           # every video in a folder
./normalize.sh ~/Videos -r                       # include sub-folders
./normalize.sh Movie.mkv --strength strong       # "night mode"
./normalize.sh Movie.mkv --stereo                # surround -> stereo, clearer dialogue
./normalize.sh Movie.mkv --keep-original         # write Movie.normalized.mkv instead
```

| Option | Meaning |
|---|---|
| `-s, --strength light\|medium\|strong` | How much to even out loud and quiet parts. The default is `medium`. |
| `-t, --target -16` | Target loudness in LUFS. The default is -16. Use `-23` or `-24` to match broadcast TV levels. |
| `--stereo` | Downmix 5.1/7.1 to stereo with the centre (dialogue) channel boosted. |
| `-r, --recursive` | Also process sub-folders. |
| `-k, --keep-original` | Don't replace the original. Writes `NAME.normalized.EXT` next to it instead. |
| `--force` | Process files that were already normalized. |

### Safety

- The new file is written as `NAME.partial.EXT`. The original is replaced only after
  the new file has been checked: same number of video and audio tracks, and the same
  length. If anything fails, the original is left untouched.
- Every processed file gets an `AUDIO_NORMALIZED` tag. Running the program on the same
  folder again skips those files, so the audio is never compressed twice. Re-running
  only processes new episodes. (`.ts`/`.m2ts` files can't store the tag.)
- `.avi`, `.wmv`, `.flv` and `.mpg` files are saved as `.mkv`, which supports the new
  audio better. The old file is then removed.

## Why not "0 dB"?

0 dB on a digital audio meter (**0 dBFS**) is the loudest value the file can hold.
Raising everything to that level would clip, which sounds harsh and distorted. Peak
level also says little about how loud audio *sounds*. Industry standards instead
measure **loudness** in **LUFS** (also written LKFS), a unit that follows human
hearing and averages over time.

| Standard | Target |
|---|---|
| EBU R128 (European TV) | -23 LUFS, peaks ≤ -1 dBTP |
| ATSC A/85 (US TV) | -24 LKFS, peaks ≤ -2 dBTP |
| Netflix (dialogue-gated) | -27 LKFS, peaks ≤ -2 dBTP |
| Streaming / laptops / phones | roughly -14 to -16 LUFS |

This tool uses **-16 LUFS with peaks kept below -1.5 dBTP** as the default. At that
level, normalized files play at a comfortable volume on laptops and TVs without
turning the volume up, and there is still headroom to prevent clipping.

Matching a loudness target alone would not solve the problem, though. A movie can
average -23 LUFS and still contain whispers at -40 and explosions at -10. The tool
therefore also **compresses the dynamic range** before setting the final level:

1. **Levelling** (`acompressor` + `dynaudnorm`) turns down the loud passages and
   brings up the quiet ones. The `--strength` setting controls how much.
2. **Loudness normalization** (two-pass `loudnorm`, EBU R128 / ITU-R BS.1770) measures
   the levelled audio and applies one exact gain so the track lands on the target.

## Credits

- ffmpeg builds: [Martin Riedl](https://ffmpeg.martin-riedl.de) (macOS, Linux) and
  [BtbN](https://github.com/BtbN/FFmpeg-Builds) (Windows)
- Portable Python: [python-build-standalone](https://github.com/astral-sh/python-build-standalone)
