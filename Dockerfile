FROM ubuntu:24.04

# Needed for docker, but not for podman
ARG GROUP_ID=''
ARG USER_ID=''

RUN apt-get update \
	&& apt-get install -y git unzip \
	&& rm -rf /var/lib/apt/lists/*

RUN if [ -n "$GROUP_ID" ]; then getent group $GROUP_ID || groupadd -g $GROUP_ID patchburner; fi
RUN if [ -n "$USER_ID" ]; then id $USER_ID || useradd -m -u $USER_ID -g $GROUP_ID -s /bin/bash patchburner; fi

COPY apply-patches.sh /usr/local/bin/apply-patches.sh
