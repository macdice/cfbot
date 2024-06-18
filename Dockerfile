FROM ubuntu:24.04

ARG GROUP_ID
ARG USER_ID

RUN <<'EOF'
# Install dependencies
set -eux
apt-get update
apt-get install -y git
EOF

RUN getent group $GROUP_ID || groupadd -g $GROUP_ID sysadmin2
RUN id $USER_ID || useradd -m -u $USER_ID -g $GROUP_ID -s /bin/bash patchburner

COPY apply-patches.sh /usr/local/bin/apply-patches.sh
