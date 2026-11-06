#!/bin/sh
set -x

export PATH=/opt/grasp/bin:$PATH
command -v jj2lsj >/dev/null 2>&1 || jj2lsj() { jj2lsj_2025 "$@"; }
cd .

# 1. rnucleus
rnucleus <<'EOF'
14
28
n
27.9769271
1
1
1
EOF

# 2. rcsfgenerate -> 2s22p3_2p5_DF.c
rcsfgenerate <<'EOF'
*
0
1s(2,i)2s(2,i)2p(3,i)
1s(2,i)2p(5,i)

2s,2p
1,5
0
n
EOF
cp rcsf.out 2s22p3_2p5_DF.c

# 3. rangular (2s22p3_2p5_DF.c)
cp 2s22p3_2p5_DF.c rcsf.inp
rangular <<'EOF'
y
EOF

# 4. rwfnestimate
rwfnestimate <<'EOF'
y
3
*
EOF

# 5. rmcdhf -> 2s22p3_2p5_DF
rmcdhf <<'EOF'
y
1-2
1-4
1
5
*
*
100
EOF
rsave 2s22p3_2p5_DF

# 6. rcsfgenerate -> 2s22p3_2p5_3.c
rcsfgenerate <<'EOF'
*
0
1s(2,*)2s(2,*)2p(3,*)
1s(2,*)2p(5,*)

3s,3p,3d
1,5
2
n
EOF
cp rcsf.out 2s22p3_2p5_3.c

# 7. rcsfinteract -> 2s22p3_2p5_3.c
cp 2s22p3_2p5_DF.c rcsfmr.inp
cp 2s22p3_2p5_3.c rcsf.inp
rcsfinteract <<'EOF'
1
EOF
cp rcsf.out 2s22p3_2p5_3.c

# 8. rangular (2s22p3_2p5_3.c)
cp 2s22p3_2p5_3.c rcsf.inp
rangular <<'EOF'
y
EOF

# 9. rwfnestimate
rwfnestimate <<'EOF'
y
1
2s22p3_2p5_DF.w
*
3
*
EOF

# 10. rmcdhf -> 2s22p3_2p5_3
rmcdhf <<'EOF'
y
1-2
1-4
1
5
3*

100
EOF
rsave 2s22p3_2p5_3

# 11. rci 2s22p3_2p5_3
rci <<'EOF'
y
2s22p3_2p5_3
y
y
1.d-6
y
n
n
y
3
1-2
1-4
1
EOF

# 12. jj2lsj 2s22p3_2p5_3
jj2lsj <<'EOF'
2s22p3_2p5_3
y
y
y
EOF

# 13. rlevels
rlevels 2s22p3_2p5_3.cm > energy3_LSJ < /dev/null

# 14. cp
cp 2s22p3_2p5_3.w 2s22p3_2p5_3.bw < /dev/null

# 15. cp
cp 2s22p3_2p5_3.cm 2s22p3_2p5_3.cbm < /dev/null

# 16. rtransition
rtransition > out_transition <<'EOF'
y
y
2s22p3_2p5_3
2s22p3_2p5_3
M1
n
EOF

# 17. jj2lsj 2s22p3_2p5_3
cp 2s22p3_2p5_3.lsj.lbl 2s22p3_2p5_3.lsj.lbl_SAVE
jj2lsj <<'EOF'
2s22p3_2p5_3
y
n
n
n
1
1
y
2
1-3
y
3
1
n
0
0.01
n
n
1
EOF

# 18. Coupling
Coupling <<'EOF'
2s22p3_2p5_3
y
3
0
1s,2s,2p,3s,3p,3d
EOF

