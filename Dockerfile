FROM python:3.11-slim

# Pinned versions — bump deliberately, then re-validate against ground truth.
# HPO_VERSION must match a tag at
#   https://github.com/obophenotype/human-phenotype-ontology/releases
# and must match hpo.release in config.yaml. The v<date> tag downloads an obo
# whose data-version: header reads hp/releases/<date>; the startup guard
# (check_obo_version) fails loudly if the two disagree.
# WHISPER_MODEL is the faster-whisper model that gets baked into the image.
ARG HPO_VERSION=v2026-02-16
ARG WHISPER_MODEL=large-v3

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only torch (saves ~1.5 GB vs default CUDA wheels, works on amd64 + arm64).
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        torch

COPY pyproject.toml ./
COPY src/ src/
RUN pip install --no-cache-dir -e . \
    && apt-get purge -y build-essential \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/ scripts/
COPY config.yaml ./

# HPO ontology — pinned release, then seeded into ChromaDB.
RUN mkdir -p data/hpo \
    && curl -fsSL "https://github.com/obophenotype/human-phenotype-ontology/releases/download/${HPO_VERSION}/hp.obo" \
        -o data/hpo/hp.obo \
    && echo "${HPO_VERSION}" > data/hpo/VERSION \
    && python scripts/seed_hpo.py

# ChromaDB's default ONNX embedding model — caches under ~/.cache/chroma/.
RUN python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction; ef = DefaultEmbeddingFunction(); ef(['warmup'])"

# Bake the Whisper model into the image so the first user click doesn't
# trigger a multi-GB download mid-transcription. ~3 GB for large-v3.
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_MODEL}', device='cpu')"

EXPOSE 7860

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["gui"]
