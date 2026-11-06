#!/bin/sh
set -x

export PATH=/opt/grasp/bin:$PATH
command -v jj2lsj >/dev/null 2>&1 || jj2lsj() { jj2lsj_2025 "$@"; }
cd .

export MPI_TMP="$PWD"/tmp_mpi
mkdir -p "$MPI_TMP"
# hwloc's GL plugin makes mpirun hang under WSLg
export HWLOC_COMPONENTS="${HWLOC_COMPONENTS:--gl}"

# 1. rnucleus
rnucleus <<'EOF'
3
7
n
6.941
1.5
3.2564268
-0.040
EOF

# 2. rcsfgenerate -> 2s_2p_DF.c
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
cp rcsf.out 2s_2p_DF.c

# 3. rangular (2s_2p_DF.c)
cp 2s_2p_DF.c rcsf.inp
mpirun -np 4 rangular_mpi <<'EOF'
y
EOF

# 4. rwfnestimate
rwfnestimate <<'EOF'
y
2
*
EOF

# 5. rmcdhf_mem -> 2s_2p_DF
mpirun -np 4 rmcdhf_mem_mpi <<'EOF'
y
1
1
1
5
*
*
100
EOF
rsave 2s_2p_DF

# 6. rcsfgenerate -> 2s_3.c
rcsfgenerate <<'EOF'
*
0
1s(2,*)2s(1,*)

3s,3p,3d
1,1
3
n
EOF
cp rcsf.out 2s_3.c

# 7. rangular (2s_3.c)
cp 2s_3.c rcsf.inp
mpirun -np 4 rangular_mpi <<'EOF'
y
EOF

# 8. rwfnestimate
rwfnestimate <<'EOF'
y
1
2s_2p_DF.w
*
2
*
EOF

# 9. rmcdhf_mem -> 2s_3
mpirun -np 4 rmcdhf_mem_mpi <<'EOF'
y
1
3*

100
EOF
rsave 2s_3

# 10. rci 2s_3
mpirun -np 4 rci_mpi <<'EOF'
y
2s_3
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

# 11. jj2lsj 2s_3
jj2lsj <<'EOF'
2s_3
y
y
y
EOF

# 12. rcsfgenerate -> 2p_3.c
rcsfgenerate <<'EOF'
*
0
1s(2,*)2p(1,*)

3s,3p,3d
1,3
3
n
EOF
cp rcsf.out 2p_3.c

# 13. rangular (2p_3.c)
cp 2p_3.c rcsf.inp
mpirun -np 4 rangular_mpi <<'EOF'
y
EOF

# 14. rwfnestimate
rwfnestimate <<'EOF'
y
1
2s_2p_DF.w
*
2
*
EOF

# 15. rmcdhf_mem -> 2p_3
mpirun -np 4 rmcdhf_mem_mpi <<'EOF'
y
1
1
5
3*

100
EOF
rsave 2p_3

# 16. rci 2p_3
mpirun -np 4 rci_mpi <<'EOF'
y
2p_3
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

# 17. jj2lsj 2p_3
jj2lsj <<'EOF'
2p_3
y
y
y
EOF

# 18. rhfs 2s_3
rhfs <<'EOF'
y
2s_3
y
EOF
rhfs_lsj <<'EOF'
2s_3
y
y
EOF

# 19. rhfs 2p_3
rhfs <<'EOF'
y
2p_3
y
EOF
rhfs_lsj <<'EOF'
2p_3
y
y
EOF

# 20. ris4 2s_3
ris4 <<'EOF'
y
2s_3
y
y
y
EOF

# 21. ris4 2p_3
ris4 <<'EOF'
y
2p_3
y
y
y
EOF

# 22. rbiotransform
mpirun -np 4 rbiotransform_mpi > out_rbiotransform <<'EOF'
y
y
2s_3
2p_3
y
EOF

# 23. rtransition
mpirun -np 4 rtransition_mpi > out_transition <<'EOF'
y
y
2s_3
2p_3
E1
EOF

# 24. rlevels
rlevels 2s_3.cm 2p_3.cm > energy3 < /dev/null

# 25. rtablevels
rtablevels <<'EOF'
0
1
energy3
EOF

# 26. rtabtrans1
rtabtrans1 <<'EOF'
2s_3.cm
2p_3.cm

0
0
EOF

# 27. rtabtrans2
rtabtrans2 <<'EOF'
2s_3.2p_3.ct
0
0
0
y
3
EOF

# 28. rmixextract
rmixextract <<'EOF'
0
2p_3
y
0.01
y
EOF
cp rcsf.out extracted.c

# 29. rmixaccumulate
rmixaccumulate <<'EOF'
2p_3
y
0.9999
y
EOF
cp rcsf.out accumulated.c

# 30. rtabhfs
rtabhfs <<'EOF'
2
2s_3.chlsj
2p_3.chlsj
0
EOF

