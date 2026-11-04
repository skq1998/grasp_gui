#!/bin/sh
set -x

export PATH=/opt/grasp/bin:$PATH
cd .

export MPI_TMP="$PWD"/tmp_mpi
mkdir -p "$MPI_TMP"
# hwloc's GL plugin makes mpirun hang under WSLg
export HWLOC_COMPONENTS="${HWLOC_COMPONENTS:--gl}"

# 1. rcsfggenerate_csfg -> n3.g
rcsfggenerate_csfg <<'EOF'
0
1s(2,i)2s(2,*)
1s(2,i)2p(2,*)
1s(2,i)2s(1,*)3s(1,*)
1s(2,i)2s(1,*)3d(1,*)

3s,3p,3d
0,6
0
y
1s(2,i)2s(1,*)2p(1,*)
1s(2,i)2s(1,*)3p(1,*)
1s(2,i)2p(1,*)3s(1,*)

3s,3p,3d
0,4
0
n
EOF
cp rcsfg.out n3.g
cp rlabel.out n3.l

# 2. rcsfggenerate_csfg -> raw_zero.g
rcsfggenerate_csfg <<'EOF'
0
1s(2,i)2s(2,*)
1s(2,i)2p(2,*)
1s(2,i)2s(1,*)3s(1,*)
1s(2,i)2s(1,*)3d(1,*)

3s,3p,3d
0,6
0
y
1s(2,i)2s(1,*)2p(1,*)
1s(2,i)2s(1,*)3p(1,*)
1s(2,i)2p(1,*)3s(1,*)

3s,3p,3d
0,4
0
y
1s(2,i)2s(2,*)
1s(2,i)2p(2,*)
1s(2,i)2s(1,*)3s(1,*)
1s(2,i)2s(1,*)3d(1,*)

9s,9p,9d,9f,9g
0,6
2
y
1s(2,i)2s(1,*)2p(1,*)
1s(2,i)2s(1,*)3p(1,*)
1s(2,i)2p(1,*)3s(1,*)

9s,9p,9d,9f,9g
0,4
2
n
EOF
cp rcsfg.out raw_zero.g
cp rlabel.out raw_zero.l

# 3. rcsfginteract_csfg -> Zero_n.g
cp n3.g rcsfgmr.inp
cp n3.l rcsfgmr.l
cp raw_zero.g rcsfg.inp
cp raw_zero.l rlabel.inp
rcsfginteract_csfg <<'EOF'
1
EOF
cp rcsfg.out Zero_n.g
cp rlabel.inp Zero_n.l

# 4. rcsfgsplit_csfg Zero_n
cp Zero_n.l rlabel.inp
rcsfgsplit_csfg <<'EOF'
Zero_n
1
4s,4p,4d,4f
4
EOF

# 5. rcsfggenerate_csfg -> raw_full.g
rcsfggenerate_csfg <<'EOF'
0
1s(2,i)2s(2,*)
1s(2,i)2p(2,*)
1s(2,i)2s(1,*)3s(1,*)
1s(2,i)2s(1,*)3d(1,*)

3s,3p,3d
0,6
0
y
1s(2,i)2s(1,*)2p(1,*)
1s(2,i)2s(1,*)3p(1,*)
1s(2,i)2p(1,*)3s(1,*)

3s,3p,3d
0,4
0
y
1s(2,*)2s(2,*)
1s(2,*)2p(2,*)
1s(2,*)2s(1,*)3s(1,*)
1s(2,*)2s(1,*)3d(1,*)

9s,9p,9d,9f,9g
0,6
2
y
1s(2,*)2s(1,*)2p(1,*)
1s(2,*)2s(1,*)3p(1,*)
1s(2,*)2p(1,*)3s(1,*)

9s,9p,9d,9f,9g
0,4
2
n
EOF
cp rcsfg.out raw_full.g
cp rlabel.out raw_full.l

# 6. rcsfginteract_csfg -> Full_n.g
cp n3.g rcsfgmr.inp
cp n3.l rcsfgmr.l
cp raw_full.g rcsfg.inp
cp raw_full.l rlabel.inp
rcsfginteract_csfg <<'EOF'
1
EOF
cp rcsfg.out Full_n.g
cp rlabel.inp Full_n.l

# 7. rcsfgsplit_csfg Full_n
cp Full_n.l rlabel.inp
rcsfgsplit_csfg <<'EOF'
Full_n
1
4s,4p,4d,4f
4
EOF

# 8. rcsfgzerofirst_csfg -> n4.g
rcsfgzerofirst_csfg <<'EOF'
Zero_n4.g
Full_n4.g
EOF
cp rcsfg.out n4.g
cp icut n4.icut
cp Full_n4.l n4.l

# 9. rnucleus
rnucleus <<'EOF'
6
12
n
12
0
0
0
EOF

# 10. rangular_csfg (n3)
cp n3.g rcsfg.inp
cp n3.l rlabel.inp
mpirun -np 4 rangular_csfg_mpi <<'EOF'
y
EOF

# 11. rwfnestimate_csfg
rwfnestimate_csfg <<'EOF'
y
3
*
EOF

# 12. rmcdhf_csfg -> n3
mpirun -np 4 rmcdhf_csfg_mpi > out_n3 <<'EOF'
y
1-4
1-3
1-3
1-6
1-4
1-3
1-1
5
*
*
100
EOF
rsave_csfg n3

# 13. rwfnestimate_csfg
rwfnestimate_csfg <<'EOF'
y
1
n3.w
*
3
*
EOF

# 14. rmcdhf_csfg -> n3
mpirun -np 4 rmcdhf_csfg_mpi > out_n3_repeat <<'EOF'
y
1-4
1-3
1-3
1-6
1-4
1-3
1-1
5
*
*
100
EOF
rsave_csfg n3

# 15. rangular_csfg (n4)
cp n4.g rcsfg.inp
cp n4.l rlabel.inp
grasp_iccut=$(awk '
function block() {
    if (row || n < 1) bad=1
    count[file,++blk]=n; symmetry[file,blk]=sym; n=0; row=0; sym=""
}
FNR==1 {
    if (file) {block(); blocks[file]=blk; if (!header) bad=1}
    file++; blk=0; n=0; row=0; active=0; header=0
}
/^CSF[(]s[)]:/ {active=1; header=1; next}
!active {next}
row==0 && /^ *[*] *$/ {block(); next}
row==0 && !NF {next}
{row++; if (row==3) {n++; if (n==1) sym=$NF; row=0}}
END {
    block(); blocks[file]=blk
    if (!header || file!=2 || blocks[1]!=blocks[2]) bad=1
    for (i=1;i<=blocks[1];i++)
        if (symmetry[1,i]!=symmetry[2,i] || count[1,i]>count[2,i]) bad=1
    if (bad) {print "Invalid CSFG ICCUT: check block symmetries and 1 <= zero count <= full count" > "/dev/stderr"; exit 1}
    for (i=1;i<=blocks[1];i++) print count[1,i]
}
' Zero_n4.g rcsfg.inp) || exit 1
mpirun -np 4 rangular_csfg_mpi <<EOF
n
n
$grasp_iccut
EOF

# 16. rwfnestimate_csfg
rwfnestimate_csfg <<'EOF'
y
1
n3.w
*
3
*
EOF

# 17. rmcdhf_csfg -> n4
mpirun -np 4 rmcdhf_csfg_mpi > out_n4 <<'EOF'
y
1-4
1-3
1-3
1-6
1-4
1-3
1-1
5
4*

200
EOF
rsave_csfg n4

# 18. rci_csfg n4CI
cp n4.g n4CI.g
cp n4.l n4CI.l
cp n4.w n4CI.w
grasp_iccut=$(awk '
function block() {
    if (row || n < 1) bad=1
    count[file,++blk]=n; symmetry[file,blk]=sym; n=0; row=0; sym=""
}
FNR==1 {
    if (file) {block(); blocks[file]=blk; if (!header) bad=1}
    file++; blk=0; n=0; row=0; active=0; header=0
}
/^CSF[(]s[)]:/ {active=1; header=1; next}
!active {next}
row==0 && /^ *[*] *$/ {block(); next}
row==0 && !NF {next}
{row++; if (row==3) {n++; if (n==1) sym=$NF; row=0}}
END {
    block(); blocks[file]=blk
    if (!header || file!=2 || blocks[1]!=blocks[2]) bad=1
    for (i=1;i<=blocks[1];i++)
        if (symmetry[1,i]!=symmetry[2,i] || count[1,i]>count[2,i]) bad=1
    if (bad) {print "Invalid CSFG ICCUT: check block symmetries and 1 <= zero count <= full count" > "/dev/stderr"; exit 1}
    for (i=1;i<=blocks[1];i++) print count[1,i]
}
' Zero_n4.g n4CI.g) || exit 1
mpirun -np 4 rci_csfg_mpi > out_n4CI <<EOF
n
n4CI
n
n
y
n
$grasp_iccut
y
y
1.d-6
n
15
n
14
y
n
n
y
3
4
1-4
1-3
1-3
1-6
1-4
1-3
1-1
EOF

