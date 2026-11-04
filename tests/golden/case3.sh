#!/bin/sh
set -x

export PATH=/opt/grasp/bin:$PATH
command -v jj2lsj >/dev/null 2>&1 || jj2lsj() { jj2lsj_2025 "$@"; }
cd .

# 1. rnucleus
rnucleus <<'EOF'
26
56
n
55.845
1
1
1
EOF

# 2. rcsfgenerate -> DFeven.c
rcsfgenerate <<'EOF'
*
2
3s(2,i)
3p(2,i)
3d(2,i)
3s(1,i)3d(1,i)

3s,3p,3d
0,8
0
n
EOF
cp rcsf.out DFeven.c

# 3. rcsfgenerate -> DFodd.c
rcsfgenerate <<'EOF'
*
2
3s(1,i)3p(1,i)
3p(1,i)3d(1,i)

3s,3p,3d
0,8
0
n
EOF
cp rcsf.out DFodd.c

# 4. rcsfgenerate -> even.c
rcsfgenerate <<'EOF'
*
2
3s(2,*)

5s,5p,5d,5f,5g
0,8
2
n
EOF
cp rcsf.out even.c

# 5. rcsfsplit even
rcsfsplit <<'EOF'
even
2
4s,4p,4d,4f
4
5s,5p,5d,5f,5g
5
EOF

# 6. rcsfgenerate -> odd.c
rcsfgenerate <<'EOF'
*
2
3s(1,*)3p(1,*)

5s,5p,5d,5f,5g
0,8
2
n
EOF
cp rcsf.out odd.c

# 7. rcsfsplit odd
rcsfsplit <<'EOF'
odd
2
4s,4p,4d,4f
4
5s,5p,5d,5f,5g
5
EOF

# 8. rangular (DFeven.c)
cp DFeven.c rcsf.inp
rangular <<'EOF'
y
EOF

# 9. rwfnestimate
rwfnestimate <<'EOF'
y
2
*
EOF

# 10. rmcdhf -> DFeven
rmcdhf <<'EOF'
y
1-5
1-3
1-7
1-2
1-2
5
*
*
100
EOF
rsave DFeven

# 11. rangular (DFodd.c)
cp DFodd.c rcsf.inp
rangular <<'EOF'
y
EOF

# 12. rwfnestimate
rwfnestimate <<'EOF'
y
2
*
EOF

# 13. rmcdhf -> DFodd
rmcdhf <<'EOF'
y
1-2
1-5
1-5
1-3
1
5
*
*
100
EOF
rsave DFodd

# 14. rangular (even4.c)
cp even4.c rcsf.inp
rangular <<'EOF'
y
EOF

# 15. rwfnestimate
rwfnestimate <<'EOF'
y
1
DFeven.w
*
2
*
EOF

# 16. rmcdhf -> even4
rmcdhf <<'EOF'
y
1-5
1-3
1-7
1-2
1-2
5
4*

100
EOF
rsave even4

# 17. rangular (even5.c)
cp even5.c rcsf.inp
rangular <<'EOF'
y
EOF

# 18. rwfnestimate
rwfnestimate <<'EOF'
y
1
even4.w
*
2
*
EOF

# 19. rmcdhf -> even5
rmcdhf <<'EOF'
y
1-5
1-3
1-7
1-2
1-2
5
5*

100
EOF
rsave even5

# 20. rangular (odd4.c)
cp odd4.c rcsf.inp
rangular <<'EOF'
y
EOF

# 21. rwfnestimate
rwfnestimate <<'EOF'
y
1
DFodd.w
*
2
*
EOF

# 22. rmcdhf -> odd4
rmcdhf <<'EOF'
y
1-2
1-5
1-5
1-3
1
5
4*

100
EOF
rsave odd4

# 23. rangular (odd5.c)
cp odd5.c rcsf.inp
rangular <<'EOF'
y
EOF

# 24. rwfnestimate
rwfnestimate <<'EOF'
y
1
odd4.w
*
2
*
EOF

# 25. rmcdhf -> odd5
rmcdhf <<'EOF'
y
1-2
1-5
1-5
1-3
1
5
5*

100
EOF
rsave odd5

# 26. rci even5
rci <<'EOF'
y
even5
y
y
1.d-6
y
n
n
y
3
1-5
1-3
1-7
1-2
1-2
EOF

# 27. rci odd5
rci <<'EOF'
y
odd5
y
y
1.d-6
y
n
n
y
3
1-2
1-5
1-5
1-3
1
EOF

# 28. rhfs even5
rhfs <<'EOF'
y
even5
y
EOF

# 29. rhfs odd5
rhfs <<'EOF'
y
odd5
y
EOF

# 30. rbiotransform
rbiotransform > out_rbiotransform <<'EOF'
y
y
even5
odd5
y
EOF

# 31. rtransition
rtransition > out_transition <<'EOF'
y
y
even5
odd5
E1
EOF

# 32. jj2lsj even5
jj2lsj <<'EOF'
even5
y
y
y
EOF

# 33. jj2lsj odd5
jj2lsj <<'EOF'
odd5
y
y
y
EOF

# 34. rlevels
rlevels even5.cm odd5.cm > energy26 < /dev/null

