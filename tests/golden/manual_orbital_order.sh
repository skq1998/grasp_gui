#!/bin/sh
set -x

export PATH=/opt/grasp/bin:$PATH
cd .

# 1. rcsfgenerate
(
tmp=$(mktemp ./.grasp-write.XXXXXX) || exit 1
trap 'rm -f "$tmp"' EXIT
if [ -L ./clist.ref ] || { [ -e ./clist.ref ] && [ ! -f ./clist.ref ]; }; then
    echo 'Cannot write clist.ref: destination is not a regular file' >&2
    exit 1
fi
cat > "$tmp" <<'GRASP_WRITE'
1s
2s
2p
3s
3p
3d
4s
4p
4d
5s
5p
4f
5d
5f
6s
6p
GRASP_WRITE
[ "$?" -eq 0 ] && mv -f "$tmp" ./clist.ref
) || exit 1
rcsfgenerate <<'EOF'
u
3
3d(10,c)4s(2,*)4p(6,*)4d(10,*)5s(2,*)5p(6,*)4f(2,*)

6s,6p,5d,5f
0,12
2
n
EOF

