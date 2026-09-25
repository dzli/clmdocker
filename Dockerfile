# Base image with CUDA, PyTorch, and vLLM pre-installed
FROM vllm/vllm-openai:latest

WORKDIR /app

# Install dependencies for CLM serving & download utilities
RUN pip install --no-cache-dir \
    "fastapi>=0.100" \
    "uvicorn>=0.23" \
    "requests>=2.28" \
    "huggingface_hub>=0.20" \
    "httpx>=0.25"

# Copy CLM repository and entrypoint script
COPY CLM /app/CLM
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Install CLM package in editable mode
RUN pip install --no-cache-dir -e /app/CLM

# Pre-download the reference projection head (75 MB) so it's ready out-of-the-box
RUN clm-download

# Default environment variables
ENV PORT=8700 \
    VLLM_PORT=8090 \
    MODEL=Qwen/Qwen3-8B \
    SERVED_MODEL_NAME=qwen3-8b \
    GPU_MEMORY_UTILIZATION=0.8 \
    MAX_MODEL_LEN=2048 \
    MODE=full \
    PYTHONUNBUFFERED=1

# Expose CLM API / Playground (8700) and vLLM Embedder (8090)
EXPOSE 8700 8090

ENTRYPOINT ["/app/entrypoint.sh"]
