# Use the pre-built base image for AGIX (upstream: agi-experimental)
# FROM agi-experimental-base:local
FROM agi-experimental/agi-experimental-base:latest

# Check if the argument is provided, else throw an error
ARG BRANCH
RUN if [ -z "$BRANCH" ]; then echo "ERROR: BRANCH is not set!" >&2; exit 1; fi
ENV BRANCH=$BRANCH
# ── RCA-3 (2026-04-25): Force headless mode for ALL npm/npx commands ──
# Without these, `npx create-next-app`, `npx next-sitemap`, and any tool
# that needs to install packages will prompt "Ok to proceed? (y)" and
# block the agent forever. CI=1 is the universal Node.js headless flag.
ENV CI=1
ENV npm_config_yes=true
ENV DEBIAN_FRONTEND=noninteractive
# RCA Phase 2 (P2-1): Redis pool scaled for 50-agent parallel swarms
ENV REDIS_MAX_CONNECTIONS=100

# Copy filesystem files to root
COPY ./docker/run/fs/ /

# pre installation steps
RUN bash /ins/pre_install.sh $BRANCH

# Copy repo into /git/agix for local builds
COPY . /git/agix

# install AGIX
RUN bash /ins/install_agix.sh $BRANCH

# install additional software
RUN bash /ins/install_additional.sh $BRANCH

# cleanup repo and install AGIX without caching, this speeds up builds
ARG CACHE_DATE=none
RUN echo "cache buster $CACHE_DATE" && bash /ins/install_agix2.sh $BRANCH

# post installation steps
RUN bash /ins/post_install.sh $BRANCH

# Expose ports
EXPOSE 22 80 9000-9009

RUN chmod +x /exe/initialize.sh /exe/run_agix.sh /exe/run_searxng.sh /exe/run_tunnel_api.sh

# initialize runtime and switch to supervisord
CMD ["/exe/initialize.sh", "$BRANCH"]
