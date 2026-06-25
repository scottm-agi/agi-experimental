# Use the pre-built base image for AGIX (upstream: agi-experimental)
# FROM agi-experimental-base:local
FROM agi-experimental/agi-experimental-base:latest

# Update and install critical system tools and dependencies early to bake them into the image
RUN apt-get update && apt-get install -y --no-install-recommends \
    ripgrep tree jq fd-find bat gosu rsync \
    fonts-unifont libasound2t64 libnss3 libatk1.0-0t64 libatk-bridge2.0-0t64 \
    libcups2t64 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxext6 \
    libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 fonts-ubuntu \
    libpq-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

ARG BRANCH=local
ENV BRANCH=$BRANCH
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

# Disable ALL third-party telemetry/phone-home behavior
ENV ANONYMIZED_TELEMETRY=false
ENV HF_HUB_DISABLE_TELEMETRY=1
ENV TRANSFORMERS_NO_ADVISORY_WARNINGS=1
ENV DO_NOT_TRACK=1
ENV LITELLM_TELEMETRY=False

# Prevent UnicodeEncodeError crashes system-wide (F-9 architectural fix)
# The :surrogateescape handler allows Python to handle invalid UTF-8 sequences
# by converting them to surrogate escapes instead of crashing
ENV PYTHONIOENCODING=utf-8:surrogateescape

# Ensure critical directories exist
RUN mkdir -p /agix /agix /opt/playwright /var/agix

# Copy filesystem files to root
COPY ./docker/run/fs/ /
# Copy current development files to git, they will only be used in "local" branch
COPY ./ /git/agix

# pre installation steps
RUN bash /ins/pre_install.sh $BRANCH

# install AGIX
RUN bash /ins/install_agix.sh $BRANCH

# install additional software
RUN bash /ins/install_additional.sh $BRANCH

# cleanup repo and install AGIX without caching, this speeds up builds
ARG CACHE_DATE=none
RUN echo "cache buster $CACHE_DATE" && bash /ins/install_agix2.sh $BRANCH

# post installation steps
RUN bash /ins/post_install.sh $BRANCH

# CRITICAL: Force-reinstall key ML deps AFTER all other pip install steps.
# Multiple install scripts (install_agix.sh, install_additional.sh, install_agix2.sh)
# can leave packages at incompatible versions. Reinstalling LAST ensures pip resolves
# wheels compiled against each other.
# - numpy/scipy/scikit-learn: ABI mismatch (RecursionError in _dtype.py)
# - tokenizers/transformers: version drift (transformers requires tokenizers<=0.23.0)
RUN /opt/venv-agix/bin/pip install --force-reinstall --no-cache-dir \
    numpy scipy scikit-learn \
    "tokenizers>=0.22.0,<0.23.1" \
    "transformers>=4.52.0,<5.0" \
    2>&1 | tail -10

# STRICT VERIFICATION — build MUST FAIL if any of these imports break.
# DO NOT add `|| echo` fallbacks — silent failures here caused user-facing HuggingFace 401
# errors in production (scipy.special._multiufuncs ValueError, 2026-04-15).
# Also verifies tokenizers version explicitly (tokenizers 0.23.1 crash, 2026-06-11).
RUN /opt/venv-agix/bin/python -c "\
import numpy; print(f'✅ numpy {numpy.__version__}'); \
import scipy; print(f'✅ scipy {scipy.__version__}'); \
from scipy.special import _multiufuncs; print('✅ scipy.special._multiufuncs deep import OK'); \
import tokenizers; print(f'✅ tokenizers {tokenizers.__version__}'); \
assert tuple(int(x) for x in tokenizers.__version__.split('.')) < (0, 23, 1), \
    f'❌ tokenizers {tokenizers.__version__} >= 0.23.1 will crash transformers'; \
import transformers; print(f'✅ transformers {transformers.__version__}'); \
import sentence_transformers; print(f'✅ sentence-transformers {sentence_transformers.__version__}')"

# Expose ports
EXPOSE 22 80 9000-9009

RUN chmod +x /exe/*.sh /exe/*.py

# initialize runtime and switch to supervisord
CMD ["/exe/initialize.sh", "$BRANCH"]
