FROM python:3.12-slim

# uv installs the exact versions in uv.lock, the same as the start scripts.
COPY --from=ghcr.io/astral-sh/uv:0.6.17 /uv /bin/uv

# The pinned HPO release and the Whisper model come from config.yaml.
# `phenoscribe setup` downloads both, the same way the start scripts do.

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Use the image's Python and put the project venv first on PATH.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# Dependencies first (cached layer), then the project itself.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache
COPY src/ src/
RUN uv sync --frozen --no-dev --no-cache \
    && apt-get purge -y build-essential \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/ scripts/
COPY config.yaml ./

# One setup path for all installs: the pinned hp.obo, the ChromaDB index
# (with its ONNX embedding model) and the Whisper + PII models, baked into the
# image so the first user click does not start a multi-GB download.
RUN phenoscribe setup

EXPOSE 7860

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["gui"]
