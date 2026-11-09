#!/usr/bin/env bash
# 单容器、单节点；使用 Docker 内部网络，不挂载宿主套接字或 cgroup。
set -euo pipefail
export OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
mode=${GRASP_SLURM_TEST_ACCOUNTING:?Choose accounting mode 0 or 1}
if [ "$mode" != 0 ] && [ "$mode" != 1 ]; then exit 2; fi
mkdir -p /run/munge /run/slurm /var/spool/slurmctld /var/spool/slurmd /var/log/slurm
chown munge:munge /run/munge
chown slurm:slurm /run/slurm /var/spool/slurmctld /var/spool/slurmd /var/log/slurm
if [ ! -s /etc/munge/munge.key ]; then
    dd if=/dev/urandom of=/etc/munge/munge.key bs=1024 count=1 status=none
fi
chown munge:munge /etc/munge/munge.key
chmod 600 /etc/munge/munge.key
runuser -u munge -- munged
slurmctld -V
git -c safe.directory=/grasp-source -C /grasp-source rev-parse HEAD
git -c safe.directory=/grasp-source -C /grasp-source diff --stat
cmake -S /grasp-source -B /opt/grasp-build -DCMAKE_Fortran_FLAGS=-fallow-argument-mismatch
cmake --build /opt/grasp-build -j "${GRASP_BUILD_JOBS:-2}"
install -m 755 /grasp-source/src/tool/rsave /opt/grasp-build/bin/rsave
export GRASP_BINDIR=/opt/grasp-build/bin GRASP_REQUIRED=1 GRASP_SLURM_TEST=1

cat > /etc/slurm/slurm.conf <<'CONF'
ClusterName=fixture
SlurmctldHost=slurmfixture(127.0.0.1)
SlurmUser=slurm
AuthType=auth/munge
StateSaveLocation=/var/spool/slurmctld
SlurmdSpoolDir=/var/spool/slurmd
SlurmctldPidFile=/run/slurm/slurmctld.pid
SlurmdPidFile=/run/slurm/slurmd.pid
SlurmctldLogFile=/var/log/slurm/slurmctld.log
SlurmdLogFile=/var/log/slurm/slurmd.log
ProctrackType=proctrack/linuxproc
TaskPlugin=task/none
JobAcctGatherType=jobacct_gather/none
SelectType=select/cons_tres
SelectTypeParameters=CR_CPU
ReturnToService=2
MinJobAge=2
KillWait=2
CONF
printf 'CgroupPlugin=disabled\n' > /etc/slurm/cgroup.conf
slurmd -C | python3 -c '
import sys
fields = sys.stdin.readline().split()
allowed = {"CPUs", "Boards", "SocketsPerBoard", "CoresPerSocket", "ThreadsPerCore"}
print("NodeName=slurmfixture " + " ".join(f for f in fields if f.split("=")[0] in allowed)
      + " RealMemory=512 State=UNKNOWN")' >> /etc/slurm/slurm.conf
echo 'PartitionName=test Nodes=slurmfixture Default=YES MaxTime=INFINITE State=UP' >> /etc/slurm/slurm.conf

if [ "$mode" = 1 ]; then
    mkdir -p /run/mysqld
    chown mysql:mysql /run/mysqld
    if [ ! -d /var/lib/mysql/mysql ]; then mariadb-install-db --user=mysql > /tmp/mysql-init.log; fi
    mariadbd --user=mysql --bind-address=127.0.0.1 > /tmp/mysql.log 2>&1 &
    for attempt in $(seq 1 60); do
        if mariadb-admin ping --silent > /dev/null 2>&1; then break; fi
        sleep 1
    done
    mariadb <<'SQL'
CREATE DATABASE slurm_acct_db;
CREATE USER 'slurm'@'localhost' IDENTIFIED BY 'fixture-only';
GRANT ALL ON slurm_acct_db.* TO 'slurm'@'localhost';
SQL
    cat > /etc/slurm/slurmdbd.conf <<'CONF'
AuthType=auth/munge
DbdHost=localhost
SlurmUser=slurm
PidFile=/run/slurm/slurmdbd.pid
LogFile=/var/log/slurm/slurmdbd.log
StorageType=accounting_storage/mysql
StorageHost=localhost
StorageUser=slurm
StoragePass=fixture-only
StorageLoc=slurm_acct_db
CONF
    chown slurm:slurm /etc/slurm/slurmdbd.conf
    chmod 600 /etc/slurm/slurmdbd.conf
    slurmdbd -D > /tmp/slurmdbd.log 2>&1 &
    for attempt in $(seq 1 60); do
        if sacctmgr -i add cluster fixture > /tmp/sacctmgr.log 2>&1; then break; fi
        sleep 1
    done
    printf '%s\n' 'AccountingStorageType=accounting_storage/slurmdbd' \
        'AccountingStorageHost=localhost' >> /etc/slurm/slurm.conf
else
    echo 'AccountingStorageType=accounting_storage/none' >> /etc/slurm/slurm.conf
fi
slurmctld -D > /tmp/slurmctld.log 2>&1 &
slurmd -D -b > /tmp/slurmd.log 2>&1 &
ready=0
for attempt in $(seq 1 60); do
    if sinfo --noheader --format='%T' 2>/dev/null | grep -qx idle; then
        ready=1
        break
    fi
    sleep 1
done
if [ "$ready" != 1 ]; then
    cat /tmp/slurm*.log /var/log/slurm/*.log
    exit 1
fi
if [ "$mode" = 1 ]; then
    sacctmgr -i add account fixture
    sacctmgr -i add user root account=fixture
fi
cd /app
set +e
python3 -m pytest tests/test_slurm_live.py -v -s -ra --junitxml="/reports/slurm-${mode}.xml"
result=$?
set -e
if [ "$result" != 0 ]; then cat /tmp/slurm*.log /var/log/slurm/*.log; fi
exit "$result"
