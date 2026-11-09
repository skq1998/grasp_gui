# 可选验收镜像，不进入产品、CI 或发布构建。GRASP 源码由运行者只读挂载。
FROM debian:trixie-slim
RUN printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d \
    && chmod +x /usr/sbin/policy-rc.d \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        slurm-wlm slurmdbd munge mariadb-server python3 python3-pytest \
        cmake make gcc g++ gfortran libblas-dev liblapack-dev libopenmpi-dev openmpi-bin git util-linux \
    && rm -rf /var/lib/apt/lists/*
COPY start.sh /fixture/start.sh
ENTRYPOINT ["bash", "/fixture/start.sh"]
