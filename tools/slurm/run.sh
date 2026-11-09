#!/usr/bin/env bash
# 用同一份代码分别验收无记账和 slurmdbd；不修改 GRASP 源码或宿主系统。
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
grasp_source=${1:?Usage: bash tools/slurm/run.sh GRASP_SOURCE REPORT_DIRECTORY}
report_directory=${2:?Provide an empty directory for logs and JUnit reports}
grasp_source="$(cd -- "$grasp_source" && pwd)"
test -f "$grasp_source/CMakeLists.txt"
mkdir -p "$report_directory"
report_directory="$(cd -- "$report_directory" && pwd)"
docker info > /dev/null
docker build -t grasp-gui-slurm-fixture "$repo_root/tools/slurm"
if ! docker network inspect grasp-slurm-internal > /dev/null 2>&1; then
    docker network create --internal grasp-slurm-internal
fi
if [ "$(docker network inspect --format '{{.Internal}}' grasp-slurm-internal)" != true ]; then
    echo 'grasp-slurm-internal must be an internal network; stopped.' >&2
    exit 1
fi
failed=0
for mode in 0 1; do
    if ! docker run --rm --hostname slurmfixture --network grasp-slurm-internal --ulimit memlock=-1:-1 \
        --mount "type=bind,source=$repo_root,target=/app,readonly" \
        --mount "type=bind,source=$grasp_source,target=/grasp-source,readonly" \
        --mount "type=bind,source=$report_directory,target=/reports" \
        -e "GRASP_SLURM_TEST_ACCOUNTING=$mode" -e "GRASP_BUILD_JOBS=${GRASP_BUILD_JOBS:-2}" \
        grasp-gui-slurm-fixture 2>&1 | tee "$report_directory/slurm-${mode}.log"; then
        failed=1
    fi
done
exit "$failed"
