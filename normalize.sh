#!/bin/sh
# Launcher for macOS / Linux.
#   ./normalize.sh                 -> opens the window
#   ./normalize.sh movie.mkv ...   -> command-line mode (same options as normalize.py)
#
# Uses Python 3.8+ if the computer has it. Otherwise downloads a private,
# portable Python into ./runtime/ (nothing is installed system-wide).
set -e

DIR=$(cd "$(dirname "$0")" && pwd)
PY_VERSION=3.13.18
PY_BUILD=20261110
LOCAL_PY="$DIR/runtime/python/bin/python3"

# The window needs tkinter; command-line mode doesn't.
if [ $# -eq 0 ]; then
    CHECK='import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 8) else 1)'
else
    CHECK='import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'
fi

usable() { [ -n "$1" ] && "$1" -c "$CHECK" >/dev/null 2>&1; }

find_python() {
    if [ -x "$LOCAL_PY" ] && usable "$LOCAL_PY"; then echo "$LOCAL_PY"; return; fi
    for candidate in python3 python; do
        p=$(command -v "$candidate" 2>/dev/null) || continue
        # macOS: /usr/bin/python3 is only a stub until the Xcode command-line
        # tools are installed (running it pops up an installer), and its
        # built-in Tk is outdated, so skip it.
        if [ "$(uname -s)" = "Darwin" ] && [ "$p" = "/usr/bin/python3" ]; then
            continue
        fi
        if usable "$p"; then echo "$p"; return; fi
    done
}

download_python() {
    case "$(uname -s)-$(uname -m)" in
        Darwin-arm64)   TRIPLE=aarch64-apple-darwin
                        SHA=1afd3983237c2a10277345f235be730e9cb49a123e0574ed252dbf3a841a24e4 ;;
        Darwin-x86_64)  TRIPLE=x86_64-apple-darwin
                        SHA=a46bcdeecd28ba253522b321dcb3806da1b0ff581479fc1d27e9613e3ac44764 ;;
        Linux-x86_64)   TRIPLE=x86_64-unknown-linux-gnu
                        SHA=50c774f6674740739f791c05a9177ed03e2fe02abebda9bca3512eb7309b37cc ;;
        Linux-aarch64|Linux-arm64)
                        TRIPLE=aarch64-unknown-linux-gnu
                        SHA=d25a65036d5e149db902fa52ad65878d0c00e9301360ec197832f99063087e9e ;;
        *) echo "error: no portable Python for $(uname -s) $(uname -m)." >&2
           echo "Please install Python 3.8+ (with tkinter) yourself." >&2
           exit 1 ;;
    esac

    FILE="cpython-${PY_VERSION}+${PY_BUILD}-${TRIPLE}-install_only.tar.gz"
    URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_BUILD}/${FILE}"
    ARCHIVE="$DIR/runtime/$FILE"

    echo "Python 3.8+ not found - downloading a private copy (one time only)..."
    mkdir -p "$DIR/runtime"
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 3 --progress-bar -o "$ARCHIVE" "$URL"
    elif command -v wget >/dev/null 2>&1; then
        wget -q --show-progress -O "$ARCHIVE" "$URL"
    else
        echo "error: need curl or wget to download Python." >&2; exit 1
    fi

    if command -v sha256sum >/dev/null 2>&1; then
        GOT=$(sha256sum "$ARCHIVE" | cut -d' ' -f1)
    else
        GOT=$(shasum -a 256 "$ARCHIVE" | cut -d' ' -f1)
    fi
    if [ "$GOT" != "$SHA" ]; then
        rm -f "$ARCHIVE"
        echo "error: Python download is corrupted (checksum mismatch). Try again." >&2
        exit 1
    fi

    rm -rf "$DIR/runtime/python"
    tar -xzf "$ARCHIVE" -C "$DIR/runtime"
    rm -f "$ARCHIVE"
    echo "Python installed into $DIR/runtime/python"
}

PY=$(find_python)
if [ -z "$PY" ]; then
    download_python
    PY="$LOCAL_PY"
fi

exec "$PY" "$DIR/normalize.py" "$@"
