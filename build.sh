#!/bin/bash
# Build a standalone executable of the GRASP GUI with PyInstaller; it runs
# without Python or PySide6 installed.
#
#   ./build.sh            -> dist/grasp_gui    single file
#   ./build.sh --onedir   -> dist/grasp_gui/   folder (starts faster: no unpacking on each start)
#
# The build environment is created in build/venv (or $GRASP_BUILD_ENV) on first use.
set -e
cd "$(dirname "$0")"

ENV=${GRASP_BUILD_ENV:-build/venv}
[ -x "$ENV/bin/pip" ] || python3 -m venv "$ENV"
# Only the essential Qt modules: keeps the executable much smaller than the full PySide6.
# Qt 6.8 is a long-term support release; 6.8.0-6.8.2 write PDF files with a broken document information
# dictionary (exported level diagrams), hence >= 6.8.3. Checked on every build, so that an existing
# environment is brought to this version; nothing is downloaded when it is already satisfied.
"$ENV/bin/pip" install -q "pyside6-essentials>=6.8.3,<6.9" "pyinstaller>=6"

MODE=--onefile
SERVER_BUNDLE=${GRASP_SERVER_BUNDLE:-}
while [ "$#" -gt 0 ]; do
    case "$1" in
        --onedir) MODE=--onedir ;;
        --server-bundle) shift; SERVER_BUNDLE=${1:?Missing server archive path} ;;
        *) echo "Usage: $0 [--onedir] [--server-bundle ARCHIVE.tar.gz]" >&2; exit 2 ;;
    esac
    shift
done
SERVER_DATA=()
if [ -n "$SERVER_BUNDLE" ]; then
    # 先核对压缩包和其中的版本记录，避免捆绑过期的服务器程序。
    "$ENV/bin/python" -c 'import grasp_install,sys; grasp_install.bundle(sys.argv[1])' "$SERVER_BUNDLE"
    mkdir -p build/server
    cp "$SERVER_BUNDLE" build/server/grasp_gui-server-linux-x86_64.tar.gz
    cp "$SERVER_BUNDLE.sha256" build/server/grasp_gui-server-linux-x86_64.tar.gz.sha256
    SERVER_DATA=(--add-data "$PWD/build/server:server")
fi

# Fonts bundled into the executable, so that machines without (Chinese) fonts can display the GUI:
# Noto Sans CJK (SIL Open Font License) and DejaVu Sans / Sans Mono (free licence), taken from this
# machine via fontconfig (Debian/Ubuntu: apt install fonts-noto-cjk fonts-dejavu-core), with their
# licence files.
FONTS=build/fonts
rm -rf "$FONTS"
mkdir -p "$FONTS"
for pattern in "Noto Sans CJK SC:style=Regular" "DejaVu Sans:style=Book" "DejaVu Sans:style=Bold" \
               "DejaVu Sans Mono:style=Book" "DejaVu Sans Mono:style=Bold"; do
    file=$(fc-match -f '%{file}' "$pattern" 2>/dev/null || true)
    family=${pattern%%:*}
    if [ -f "$file" ] && fc-scan -f '%{family}\n' "$file" 2>/dev/null | tr ',' '\n' | grep -qx "$family"; then
        cp "$file" "$FONTS/"
    else
        echo "warning: font '$pattern' not found; it will not be bundled" >&2
    fi
done
for doc in fonts-noto-cjk fonts-dejavu-core; do
    [ -f "/usr/share/doc/$doc/copyright" ] && cp "/usr/share/doc/$doc/copyright" "$FONTS/LICENSE-$doc"
done

# __future__ is imported by shiboken's signature bootstrap, which PyInstaller does not detect
"$ENV/bin/pyinstaller" --noconfirm --clean --log-level WARN "$MODE" --name grasp_gui \
    --distpath dist --workpath build/pyinstaller --specpath build \
    --hidden-import __future__ "${SERVER_DATA[@]}" \
    --add-data "$PWD/$FONTS:fonts" \
    --add-data "$PWD/demos/*.json:demos" \
    grasp_gui.py

echo
echo "Built: $(ls -d dist/grasp_gui)"
