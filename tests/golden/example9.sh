#!/bin/sh
set -x

export PATH=/opt/grasp/bin:$PATH
command -v jj2lsj >/dev/null 2>&1 || jj2lsj() { jj2lsj_2025 "$@"; }
cd .

# 1. rnucleus
rnucleus <<'EOF'
28
61
n
58.6934
1.5
-0.75002
0.162
EOF

# 2. rcsfgenerate -> mr.c
rcsfgenerate <<'EOF'
*
0
1s(2,i)2s(2,i)
1s(2,i)2p(2,i)

2s,2p
0,0
0
y
1s(2,i)2s(1,i)2p(1,i)

2s,2p
0,4
0
n
EOF
cp rcsf.out mr.c

# 3. rangular (mr.c)
cp mr.c rcsf.inp
rangular <<'EOF'
y
EOF

# 4. rwfnestimate
rwfnestimate <<'EOF'
y
2
*
EOF

# 5. rmcdhf_mem -> mr
rmcdhf_mem <<'EOF'
y
1
1
1,2
1
5
*
*
100
EOF
rsave mr

# 6. rcsfgenerate -> even_n3.c
rcsfgenerate <<'EOF'
*
0
1s(2,i)2s(2,*)

3s,3p,3d
0,0
2
n
EOF
cp rcsf.out even_n3.c

# 7. rangular (even_n3.c)
cp even_n3.c rcsf.inp
rangular <<'EOF'
y
EOF

# 8. rwfnestimate
rwfnestimate <<'EOF'
y
1
mr.w
*
2
*
EOF

# 9. rmcdhf_mem -> even_n3
rmcdhf_mem <<'EOF'
y
1
3*

100
EOF
rsave even_n3

# 10. rci even_n3
rci <<'EOF'
y
even_n3
y
y
1.d-6
y
n
n
y
3
1
EOF

# 11. jj2lsj even_n3
jj2lsj <<'EOF'
even_n3
y
y
y
EOF

# 12. rcsfgenerate -> odd_n3.c
rcsfgenerate <<'EOF'
*
0
1s(2,i)2s(1,*)2p(1,*)

3s,3p,3d
0,4
2
n
EOF
cp rcsf.out odd_n3.c

# 13. rangular (odd_n3.c)
cp odd_n3.c rcsf.inp
rangular <<'EOF'
y
EOF

# 14. rwfnestimate
rwfnestimate <<'EOF'
y
1
mr.w
*
2
*
EOF

# 15. rmcdhf_mem -> odd_n3
rmcdhf_mem <<'EOF'
y
1
1,2
1
5
3*

100
EOF
rsave odd_n3

# 16. rci odd_n3
rci <<'EOF'
y
odd_n3
y
y
1.d-6
y
n
n
y
3
1
1,2
1
EOF

# 17. jj2lsj odd_n3
jj2lsj <<'EOF'
odd_n3
y
y
y
EOF

# 18. rhfszeeman95 even_n3
rhfszeeman95 <<'EOF'
y
even_n3
y
y
EOF

# 19. rhfszeeman95 odd_n3
rhfszeeman95 <<'EOF'
y
odd_n3
y
y
EOF

# 20. rbiotransform
rbiotransform > out_rbiotransform <<'EOF'
y
y
even_n3
odd_n3
y
EOF

# 21. rtransition_phase
rtransition_phase > out_transition_phase <<'EOF'
y
y
even_n3
odd_n3
E1,M2
EOF

