FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install CPU-only torch explicitly (saves ~1.5 GB vs default CUDA wheels,
# works on both linux/amd64 and linux/arm64 from the same index).
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        torch

# Project dependencies — copy minimum needed for the editable install,
# then drop build-essential to slim the final image.
COPY pyproject.toml ./
COPY src/ src/
RUN pip install --no-cache-dir -e . \
    && apt-get purge -y build-essential \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/ scripts/
COPY config.yaml ./

# Bake the public HPO ontology and a pre-seeded ChromaDB into the image
# so first-run is functional without any download except the LLM call.
RUN mkdir -p data/hpo \
    && curl -fsSL https://purl.obolibrary.org/obo/hp.obo -o data/hpo/hp.obo \
    && python scripts/seed_hpo.py

# Pre-fetch ChromaDB's default ONNX embedding model. Cached under
# ~/.cache/chroma/ — the launcher only mounts ~/.cache/huggingface,
# so this stays baked at runtime.
RUN python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction; ef = DefaultEmbeddingFunction(); ef(['warmup'])"

EXPOSE 7860

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["gui"]
