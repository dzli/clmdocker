#!/usr/bin/env bash
set -e

# ==============================================================================
# CLM (Contrastive Language Model) Docker Entrypoint
# ==============================================================================
# Modes:
#   MODE=full (default): Starts both the vLLM pooling embedder (Qwen3-8B) in the
#                        background and the CLM API server in the foreground.
#   MODE=clm           : Starts only the CLM API server, connecting to an external
#                        embedder specified by CLM_EMB_URL.
#   MODE=vllm          : Starts only the vLLM pooling embedder.
#   MODE=mock          : Starts the lightweight mock server (0 GPU, no model download),
#                        perfect for testing API calls and web UI playground.
# ==============================================================================

# Custom command override: if user supplies custom arguments to `docker run clm <cmd>`
if [ "$#" -gt 0 ] && [ "$1" != "full" ] && [ "$1" != "clm" ] && [ "$1" != "vllm" ] && [ "$1" != "mock" ]; then
    exec "$@"
fi

# Override MODE if first argument matches a known mode
if [ "$1" = "full" ] || [ "$1" = "clm" ] || [ "$1" = "vllm" ] || [ "$1" = "mock" ]; then
    MODE="$1"
    shift
fi

MODE="${MODE:-full}"
PORT="${PORT:-8700}"
VLLM_PORT="${VLLM_PORT:-8090}"
MODEL="${MODEL:-Qwen/Qwen3-8B}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-qwen3-8b}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.8}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-2048}"
CLM_EMB_URL="${CLM_EMB_URL:-http://127.0.0.1:${VLLM_PORT}/v1/embeddings}"
EXTRA_VLLM_ARGS="${EXTRA_VLLM_ARGS:-}"
EXTRA_CLM_ARGS="${EXTRA_CLM_ARGS:-}"

echo "======================================================================"
echo " Starting Contrastive Language Model (CLM) Service"
echo " Mode: $MODE"
echo " Service Port (CLM API / Web UI): $PORT"
[ "$MODE" = "full" ] || [ "$MODE" = "vllm" ] && echo " Embedder Port (vLLM): $VLLM_PORT"
[ "$MODE" = "full" ] || [ "$MODE" = "vllm" ] && echo " Model: $MODEL"
echo "======================================================================"

VLLM_PID=""
CLM_PID=""

cleanup() {
    echo ""
    echo "[CLM Entrypoint] Stopping services..."
    if [ -n "$CLM_PID" ] && kill -0 "$CLM_PID" 2>/dev/null; then
        echo "[CLM Entrypoint] Terminating CLM server (PID: $CLM_PID)..."
        kill -TERM "$CLM_PID" 2>/dev/null || true
    fi
    if [ -n "$VLLM_PID" ] && kill -0 "$VLLM_PID" 2>/dev/null; then
        echo "[CLM Entrypoint] Terminating vLLM server (PID: $VLLM_PID)..."
        kill -TERM "$VLLM_PID" 2>/dev/null || true
    fi
    wait 2>/dev/null || true
    echo "[CLM Entrypoint] Stopped."
    exit 0
}

trap cleanup SIGINT SIGTERM

case "$MODE" in
    mock)
        echo "[CLM Entrypoint] Launching CLM Mock Playground on port $PORT..."
        echo "[CLM Entrypoint] Accessible at: http://0.0.0.0:$PORT"
        exec python3 /app/CLM/tools/playground_mock.py --host 0.0.0.0 --port "$PORT" "$@"
        ;;

    vllm)
        echo "[CLM Entrypoint] Launching vLLM pooling server on port $VLLM_PORT..."
        exec vllm serve "$MODEL" \
            --served-model-name "$SERVED_MODEL_NAME" \
            --runner pooling \
            --enforce-eager \
            --enable-prefix-caching \
            --max-model-len "$MAX_MODEL_LEN" \
            --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
            --max-num-seqs 32 \
            --port "$VLLM_PORT" \
            $EXTRA_VLLM_ARGS "$@"
        ;;

    clm)
        echo "[CLM Entrypoint] Launching CLM API server on port $PORT..."
        echo "[CLM Entrypoint] Connecting to embedder at: $CLM_EMB_URL"
        exec clm-serve \
            --host 0.0.0.0 \
            --port "$PORT" \
            --emb-url "$CLM_EMB_URL" \
            --emb-model "$SERVED_MODEL_NAME" \
            $EXTRA_CLM_ARGS "$@"
        ;;

    full|*)
        echo "[CLM Entrypoint] Step 1/2: Starting vLLM pooling server in background on port $VLLM_PORT..."
        vllm serve "$MODEL" \
            --served-model-name "$SERVED_MODEL_NAME" \
            --runner pooling \
            --enforce-eager \
            --enable-prefix-caching \
            --max-model-len "$MAX_MODEL_LEN" \
            --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
            --max-num-seqs 32 \
            --port "$VLLM_PORT" \
            $EXTRA_VLLM_ARGS &
        VLLM_PID=$!

        echo "[CLM Entrypoint] Waiting for vLLM embedder to be ready..."
        READY=0
        for i in $(seq 1 300); do
            if ! kill -0 "$VLLM_PID" 2>/dev/null; then
                echo "[CLM Entrypoint] ERROR: vLLM process crashed or exited! See logs above."
                exit 1
            fi
            if curl -s -f "http://127.0.0.1:${VLLM_PORT}/v1/models" >/dev/null 2>&1; then
                READY=1
                echo "[CLM Entrypoint] vLLM embedder is ready on port $VLLM_PORT!"
                break
            fi
            sleep 2
        done

        if [ "$READY" -ne 1 ]; then
            echo "[CLM Entrypoint] ERROR: Timed out waiting for vLLM to start after 10 minutes."
            kill -TERM "$VLLM_PID" 2>/dev/null || true
            exit 1
        fi

        echo "[CLM Entrypoint] Step 2/2: Starting CLM API server on port $PORT..."
        clm-serve \
            --host 0.0.0.0 \
            --port "$PORT" \
            --emb-url "$CLM_EMB_URL" \
            --emb-model "$SERVED_MODEL_NAME" \
            $EXTRA_CLM_ARGS "$@" &
        CLM_PID=$!

        echo "[CLM Entrypoint] Services up and running!"
        echo "[CLM Entrypoint] Web UI and API available at: http://0.0.0.0:$PORT"

        # Wait for either process to exit
        wait -n "$VLLM_PID" "$CLM_PID"
        cleanup
        ;;
esac
