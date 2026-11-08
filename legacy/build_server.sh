#!/bin/bash
# 构建无需 Qt 的服务器目录版；不使用单文件，避免 /tmp noexec 阻止载入库。
# legacy/build_server.sh -> dist/grasp_gui-server-linux-x86_64.tar.gz 和 .sha256
set -e -o pipefail
cd "$(dirname "$0")/.."
DOCKER=${DOCKER:-docker}

if [ ! -f grasp_server.py ]; then
    echo 'Server entry point missing: merge the issue #18 main PR before building.' >&2
    exit 1
fi
sources() {
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        git ls-files -z --cached --others --exclude-standard
    else
        find . -path ./build -prune -o -path ./dist -prune -o -type f -printf '%P\0'
    fi
}
mkdir -p build dist
OUTPUT=$(mktemp -d "$PWD/build/server.XXXXXX")
trap 'rm -rf "$OUTPUT"' EXIT
sources | tar --null -T - -c \
    | "$DOCKER" build -f legacy/Dockerfile.server -o - - \
    | tar -x -C "$OUTPUT"
tar -czf dist/grasp_gui-server-linux-x86_64.tar.gz -C "$OUTPUT" grasp_gui-server
(cd dist && sha256sum grasp_gui-server-linux-x86_64.tar.gz > grasp_gui-server-linux-x86_64.tar.gz.sha256)
echo 'Built: dist/grasp_gui-server-linux-x86_64.tar.gz (onedir)'
