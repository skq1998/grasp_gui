#!/bin/sh
set -x

export PATH=/opt/grasp/bin:$PATH
cd .

export MPI_TMP="$PWD"/tmp_mpi
mkdir -p "$MPI_TMP"
# hwloc's GL plugin makes mpirun hang under WSLg
export HWLOC_COMPONENTS="${HWLOC_COMPONENTS:--gl}"

# 1. rcsfgenerate -> section51.c
rcsfgenerate <<'EOF'
*
0
1s(2,*)2s(2,*)
1s(2,*)2p(2,*)

4s,4p,4d,4f
0,0
2
n
EOF
cp rcsf.out section51.c

# 2. rcsfgenerate -> section52.c
rcsfgenerate <<'EOF'
*
1
2s(2,1)2p(6,i)3s(1,*)3p(1,*)
2s(2,i)2p(6,5)3s(1,*)3p(1,*)
2s(2,1)2p(6,i)3p(1,*)3d(1,*)
2s(2,i)2p(6,5)3p(1,*)3d(1,*)

5s,5p,5d,5f,5g
0,4
2
n
EOF
cp rcsf.out section52.c

# 3. rcsfgenerate -> section53.c
rcsfgenerate <<'EOF'
*
1
2s(2,1)2p(6,5)3s(1,*)3p(1,*)
2s(2,1)2p(6,5)3p(1,*)3d(1,*)

5s,5p,5d,5f,5g
0,4
2
n
EOF
cp rcsf.out section53.c

# 4. rcsfgenerate -> section54.c
rcsfgenerate <<'EOF'
*
0
1s(2,1)2s(2,*)2p(2,*)
1s(2,1)2p(4,*)
1s(2,1)2s(2,*)2p(1,*)3p(1,*)
1s(2,1)2s(1,*)2p(2,*)3s(1,*)
1s(2,1)2s(1,*)2p(2,*)3d(1,*)

5s,5p,5d,5f,5g
0,10
2
n
EOF
cp rcsf.out section54.c

# 5. rcsfgenerate -> section55_mr.c
rcsfgenerate <<'EOF'
*
0
1s(2,i)2s(2,i)2p(2,i)
1s(2,i)2p(4,i)
1s(2,i)2s(2,i)2p(1,i)3p(1,i)
1s(2,i)2s(1,i)2p(2,i)3s(1,i)
1s(2,i)2s(1,i)2p(2,i)3d(1,i)

3s,3p,3d
0,10
0
n
EOF
cp rcsf.out section55_mr.c

# 6. rcsfinteract -> section55.c
cp section55_mr.c rcsfmr.inp
cp section54.c rcsf.inp
rcsfinteract <<'EOF'
1
EOF
cp rcsf.out section55.c

# 7. rcsfgenerate -> section56.c
rcsfgenerate <<'EOF'
*
1
2s(2,i)2p(6,5)3s(1,*)3p(1,*)
2s(2,1)2p(6,i)3s(1,*)3p(1,*)
2s(2,i)2p(6,5)3p(1,*)3d(1,*)
2s(2,1)2p(6,i)3p(1,*)3d(1,*)

8s,8p,8d,8f,8g,8h
0,4
2
y
2s(2,*)2p(6,*)3s(1,*)3p(1,*)
2s(2,*)2p(6,*)3p(1,*)3d(1,*)

8s,8p,8d,8f,8g,8h
0,4
-2
n
EOF
cp rcsf.out section56.c

# 8. rcsfgenerate -> section57.c
rcsfgenerate <<'EOF'
*
0
1s(2,*)2p(1,*)

5s,5p,5d,5f,5g
1,3
3
y
1s(2,*)2p(1,*)

7s,7p,7d,7f,7g,7h,7i
1,3
2
n
EOF
cp rcsf.out section57.c

# 9. rcsfgenerate -> section58.c
rcsfgenerate <<'EOF'
*
0
1s(2,*)2p(1,*)

5s,5p,5d,5f,5g
1,3
3
y
1s(2,*)2s(1,*)

5s,5p,5d,5f,5g
1,1
3
n
EOF
cp rcsf.out section58.c

