# Build stage: the C compiler for packages with no wheel stays in this stage.
FROM python:3.12-slim AS build

# uv installs the exact versions in uv.lock, the same as the start scripts.
COPY --from=ghcr.io/astral-sh/uv:0.6.17 /uv /bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy

# Dependencies first (cached layer), then the project itself.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache
COPY README.md LICENSE ./
COPY src/ src/
RUN uv sync --frozen --no-dev --no-cache


FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The app runs as a normal user. UID 1000 is the first user on most Linux
# hosts, so a bind-mounted output folder of that user stays writable.
RUN useradd --create-home --uid 1000 phenoscribe

WORKDIR /app
COPY --from=build --chown=phenoscribe:phenoscribe /app /app
COPY --chown=phenoscribe:phenoscribe config.yaml ./
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /app/output && chown phenoscribe:phenoscribe /app/output

USER phenoscribe
ENV PATH="/app/.venv/bin:$PATH"

# The pinned hp.obo, the search index and the Whisper and privacy models (from
# config.yaml), in the image so the first click starts no multi-GB download.
RUN phenoscribe setup

# In a container the page listens on all interfaces, so `docker run -p` reaches it.
ENV GRADIO_SERVER_NAME=0.0.0.0 \
    PHENOSCRIBE_OUTPUT_DIR=/app/output
EXPOSE 7860

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["gui"]
