#!/bin/sh
set -x

export PATH=/opt/grasp/bin:$PATH
cd .

# 1. rnucleus
rnucleus <<'EOF'
6
12
n
12.0107
1
1
1
EOF

# 2. rcsfgenerate -> DF.c
rcsfgenerate <<'EOF'
*
0
1s(2,i)2s(1,i)

2s,2p
1,1
0
y
1s(2,i)2p(1,i)

2s,2p
1,3
0
n
EOF
cp rcsf.out DF.c

# 3. rcsfgenerate -> even.c
rcsfgenerate <<'EOF'
*
0
1s(2,*)2s(1,*)

5s,5p,5d,5f,5g
1,1
3
n
EOF
cp rcsf.out even.c

# 4. rcsfsplit even
rcsfsplit <<'EOF'
even
3
3s,3p,3d
3
4s,4p,4d,4f
4
5s,5p,5d,5f,5g
5
EOF

# 5. rcsfgenerate -> odd.c
rcsfgenerate <<'EOF'
*
0
1s(2,*)2p(1,*)

5s,5p,5d,5f,5g
1,3
3
n
EOF
cp rcsf.out odd.c

# 6. rcsfsplit odd
rcsfsplit <<'EOF'
odd
3
3s,3p,3d
3
4s,4p,4d,4f
4
5s,5p,5d,5f,5g
5
EOF

# 7. rangular (DF.c)
cp DF.c rcsf.inp
rangular <<'EOF'
y
EOF

# 8. rwfnestimate
rwfnestimate <<'EOF'
y
2
*
EOF

# 9. rmcdhf -> DF
rmcdhf <<'EOF'
y
1
1
1
5
*
*
100
EOF
rsave DF

# 10. rangular (even3.c)
cp even3.c rcsf.inp
rangular <<'EOF'
y
EOF

# 11. rwfnestimate
rwfnestimate <<'EOF'
y
1
DF.w
*
2
*
EOF

# 12. rmcdhf -> even3
rmcdhf <<'EOF'
y
1
3*

100
EOF
rsave even3

# 13. rangular (even4.c)
cp even4.c rcsf.inp
rangular <<'EOF'
y
EOF

# 14. rwfnestimate
rwfnestimate <<'EOF'
y
1
even3.w
*
2
*
EOF

# 15. rmcdhf -> even4
rmcdhf <<'EOF'
y
1
4*

100
EOF
rsave even4

# 16. rangular (even5.c)
cp even5.c rcsf.inp
rangular <<'EOF'
y
EOF

# 17. rwfnestimate
rwfnestimate <<'EOF'
y
1
even4.w
*
2
*
EOF

# 18. rmcdhf -> even5
rmcdhf <<'EOF'
y
1
5*

100
EOF
rsave even5

# 19. rangular (odd3.c)
cp odd3.c rcsf.inp
rangular <<'EOF'
y
EOF

# 20. rwfnestimate
rwfnestimate <<'EOF'
y
1
DF.w
*
2
*
EOF

# 21. rmcdhf -> odd3
rmcdhf <<'EOF'
y
1
1
5
3*

100
EOF
rsave odd3

# 22. rangular (odd4.c)
cp odd4.c rcsf.inp
rangular <<'EOF'
y
EOF

# 23. rwfnestimate
rwfnestimate <<'EOF'
y
1
odd3.w
*
2
*
EOF

# 24. rmcdhf -> odd4
rmcdhf <<'EOF'
y
1
1
5
4*

100
EOF
rsave odd4

# 25. rangular (odd5.c)
cp odd5.c rcsf.inp
rangular <<'EOF'
y
EOF

# 26. rwfnestimate
rwfnestimate <<'EOF'
y
1
odd4.w
*
2
*
EOF

# 27. rmcdhf -> odd5
rmcdhf <<'EOF'
y
1
1
5
5*

100
EOF
rsave odd5

# 28. rci even5
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
1
EOF

# 29. rci odd5
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
1
1
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

