#!/bin/sh
set -x

export PATH=/opt/grasp/bin:$PATH
cd .

export MPI_TMP="$PWD"/tmp_mpi
mkdir -p "$MPI_TMP"
# hwloc's GL plugin makes mpirun hang under WSLg
export HWLOC_COMPONENTS="${HWLOC_COMPONENTS:--gl}"

# 1. rnucleus
rnucleus <<'EOF'
26
56
n
56
1
1
1
EOF

# 2. rcsfgenerate -> mr.c
rcsfgenerate <<'EOF'
*
1
2s(2,i)2p(6,i)3s(2,i)3p(2,i)
2s(2,i)2p(6,i)3s(1,i)3p(2,i)3d(1,i)
2s(2,i)2p(6,i)3p(4,i)

3s,3p,3d
0,4
0
n
EOF
cp rcsf.out mr.c

# 3. rangular (mr.c)
cp mr.c rcsf.inp
mpirun -np 4 rangular_mpi <<'EOF'
y
EOF

# 4. rwfnestimate
rwfnestimate <<'EOF'
y
2
*
EOF

# 5. rmcdhf -> mr
mpirun -np 4 rmcdhf_mpi <<'EOF'
y
1,2
1
1,2
5
*
*
900
EOF
rsave mr

# 6. rcsfgenerate -> full.c
rcsfgenerate <<'EOF'
*
1
2s(2,i)2p(6,5)3s(2,*)3p(2,*)
2s(2,i)2p(6,5)3s(1,*)3p(2,*)3d(1,*)
2s(2,i)2p(6,5)3p(4,*)

4s,4p,4d,4f
0,4
2
n
EOF
cp rcsf.out full.c

# 7. rcsfzerofirst -> zerofirst_n4.c
rcsfzerofirst <<'EOF'
mr.c
full.c
EOF
cp rcsf.out zerofirst_n4.c

# 8. rangular (zerofirst_n4.c)
cp zerofirst_n4.c rcsf.inp
mpirun -np 4 rangular_mpi <<EOF
n
$(awk 'f&&/^ *[*]/{print int(n/3);n=0;next} f&&NF{n++} /^CSF[(]s[)]:/{f=1} END{print int(n/3)}' mr.c)
EOF

# 9. rwfnestimate
rwfnestimate <<'EOF'
y
1
mr.w
*
2
*
EOF

# 10. rmcdhf -> zerofirst_n4
mpirun -np 4 rmcdhf_mpi <<'EOF'
y
1,2
1
1,2
5
4*

100
EOF
rsave zerofirst_n4

# 11. rci zerofirst_n4
mpirun -np 4 rci_mpi > outodd_rci <<EOF
n
zerofirst_n4
n
n
y
$(awk 'f&&/^ *[*]/{print int(n/3);n=0;next} f&&NF{n++} /^CSF[(]s[)]:/{f=1} END{print int(n/3)}' mr.c)
y
n
y
n
n
y
3
1,2
1
1,2
EOF

