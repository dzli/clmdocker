# Dockerized Contrastive Language Model (CLM) Service

This directory contains the Docker configuration to run **[CLM (Contrastive Language Model)](https://github.com/Contrastive-LM/CLM)** without modifying or installing anything on your host machine.

The container runs the CLM service and exposes port **8700** for the CLM System One API and Web Playground, plus port **8090** for the underlying vLLM embedding server.

---

## Architecture Overview

```
Host (Your Machine)               Docker Container (`clm:latest`)
┌────────────────────────┐        ┌────────────────────────────────────────────────────────┐
│ - Browser:             │        │                                                        │
│   http://localhost:8700│───────►│  clm-serve (:8700)                                     │
│                        │        │  - Web Playground UI at /                              │
│ - Host Python Code:    │        │  - POST /v1/systemone (typed decision API)             │
│   ./client_example.py  │───────►│  - POST /v1/rank      (candidate scoring API)          │
│                        │        │  - GET  /v1/models    (available models)               │
│ - curl / HTTP clients  │        │  - GET  /health       (health status)                  │
│                        │        │         │                                              │
│                        │        │         ▼ (internal /v1/embeddings)                    │
│                        │        │  vLLM Pooling Server (:8090)                           │
│                        │        │  - Qwen/Qwen3-8B last-token pooling                    │
│                        │        │  - Contrastive-LM/CLM-v0.1-8B projection head (75 MB)  │
└────────────────────────┘        └────────────────────────────────────────────────────────┘
```

---

## 1. Quick Start

### Clone the Repository
The upstream [CLM](https://github.com/Contrastive-LM/CLM) code is included as a git submodule, so clone recursively:
```bash
git clone --recursive git@github.com:dzli/clmdocker.git
# or, if already cloned without --recursive:
git submodule update --init
```

### Build the Docker Image
```bash
docker build -t clm:latest .
```

### Run the Container

#### Option A: Using Docker Compose (Recommended)
```bash
# Start in background with GPU support and host cache mount
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

#### Option B: Using `docker run` Directly
```bash
# Ensure cache directories exist on host for persistent weights
mkdir -p cache/huggingface cache/clm

docker run -d \
  --name clm-service \
  --gpus all \
  --ipc host \
  -p 8700:8700 \
  -p 8090:8090 \
  -v "$(pwd)/cache/huggingface:/root/.cache/huggingface" \
  -v "$(pwd)/cache/clm:/root/.cache/clm" \
  clm:latest
```

---

## 2. Accessing the Service from Your Host

Once started, the service listens on port `8700`.

### A. Web Playground
Open your browser at:
```
http://localhost:8700
```
Interactive UI to test states, typed questions (noul/choice/score), and view prediction distributions.

### B. Python Client (Zero Host Dependencies)
Run the included client test script directly from your host:
```bash
python3 client_example.py
```
*(This script uses only Python's built-in `urllib` and `json` libraries. No `pip` or virtualenv needed on your host.)*

### C. File Classifier / Typing Tool (`file_classifier.py`)
Classify any file (Python source, Shell script, ELF binary, Dockerfile, YAML, JSON, Markdown, etc.) using the CLM service:
```bash
# Classify a single file
./file_classifier.py client_example.py

# Classify multiple files
./file_classifier.py client_example.py entrypoint.sh /bin/ls Dockerfile

# Output structured JSON
./file_classifier.py --json /bin/bash

# View top 3 candidates only
./file_classifier.py --top 3 docker-compose.yml
```

### D. cURL Examples

#### Health Check
```bash
curl http://localhost:8700/health
```

#### Candidate Ranking (`/v1/rank`)
```bash
curl -X POST http://localhost:8700/v1/rank \
  -H "Content-Type: application/json" \
  -d '{
    "context": "Astronomical question",
    "question": "What causes tides on Earth?",
    "answers": [
      "The Moon'\''s gravitational pull.",
      "Photosynthesis in ocean plants.",
      "Earth'\''s magnetic field rotating."
    ]
  }'
```

#### System One Typed Decisions (`/v1/systemone`)
```bash
curl -X POST http://localhost:8700/v1/systemone \
  -H "Content-Type: application/json" \
  -d '{
    "state": "Customer: my invoice was charged twice and nobody answers the phone!",
    "questions": {
      "urgency": {
        "type": "noul",
        "instructions": "Is this urgent?"
      },
      "department": {
        "type": "choice",
        "instructions": "Which team should handle this?",
        "criteria": {
          "billing": "Charges, invoices, payment disputes, refunds",
          "technical": "System bugs, crashes, service outages"
        }
      },
      "frustration": {
        "type": "score",
        "instructions": "How frustrated is the customer?",
        "criteria": ["Calm", "Frustrated", "Very angry"]
      }
    }
  }'
```

---

## 3. Operating Modes (`MODE`)

The image supports different modes via the `MODE` environment variable:

| Mode | Description | GPU Required | Model Download |
|---|---|---|---|
| `full` *(default)* | Runs vLLM (Qwen3-8B) + CLM API server together | Yes | Downloads Qwen3-8B and CLM head |
| `mock` | Runs CLM mock server with hashed n-grams | No (CPU only) | None (instant startup) |
| `clm` | Runs only CLM API, connecting to external `CLM_EMB_URL` | Optional | Downloads CLM head (75 MB) |
| `vllm` | Runs only the vLLM pooling embedder | Yes | Downloads Qwen3-8B |

### Testing Instantly in Mock Mode (No GPU / No Weights)
To test the web UI and host client immediately without waiting for weights:
```bash
docker run --rm -p 8700:8700 -e MODE=mock clm:latest
```
Then run `./client_example.py` or open `http://localhost:8700`.

### Connecting to an External Embedding Server
If you run vLLM on a separate GPU box:
```bash
docker run -d \
  -p 8700:8700 \
  -e MODE=clm \
  -e CLM_EMB_URL=http://your-remote-gpu:8090/v1/embeddings \
  clm:latest
```

---

## 4. Configuration Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MODE` | `full` | Operating mode: `full`, `mock`, `clm`, `vllm` |
| `PORT` | `8700` | Service port for CLM API and Playground |
| `VLLM_PORT` | `8090` | Internal port for vLLM pooling embedder |
| `MODEL` | `Qwen/Qwen3-8B` | Embedding backbone model repo on Hugging Face |
| `SERVED_MODEL_NAME` | `qwen3-8b` | Model name identifier exposed by vLLM |
| `GPU_MEMORY_UTILIZATION` | `0.8` | Fraction of GPU memory allocated to vLLM |
| `MAX_MODEL_LEN` | `2048` | Maximum sequence length for embeddings |
| `CLM_EMB_URL` | `http://127.0.0.1:8090/v1/embeddings` | URL for the OpenAI-compatible embedding endpoint |
| `CLM_API_KEY` | *(empty)* | Optional Bearer token authentication |
| `EXTRA_VLLM_ARGS` | *(empty)* | Extra flags passed to `vllm serve` (e.g. `--cpu-offload-gb 4`) |
| `EXTRA_CLM_ARGS` | *(empty)* | Extra flags passed to `clm-serve` |

---

## 5. Notes on GPU VRAM

- `Qwen/Qwen3-8B` has ~7.57B parameters. In 16-bit (`bfloat16`), it requires ~15 GB VRAM.
- On GPUs with ≤ 10 GB VRAM (such as RTX 3080 10GB), you can:
  - Add CPU offloading: `EXTRA_VLLM_ARGS="--cpu-offload-gb 8"`
  - Or point `MODE=clm` to an external host running the embedder.
