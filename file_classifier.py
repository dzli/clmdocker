#!/usr/bin/env python3
"""File Typing / Classification Client for CLM (Contrastive Language Model).

This script reads local files (source code, text, scripts, or ELF binaries),
builds a representation, sends it to the CLM service API (listening on the
Docker container's service port), and predicts the file type with candidate
probabilities and confidence scores.

Features:
- Pure Python standard library (zero host dependencies: no pip packages needed).
- Supports popular file types: Python, Shell, ELF binary, C/C++, JavaScript,
  Dockerfile, JSON, YAML, Markdown, HTML, Archive, Plain text, etc.
- Smart header inspection (ELF headers, shebang lines, binary magic bytes).
- Supports both CLM API modes:
    * 'systemone' (TypeSafe /v1/systemone choice criteria)
    * 'rank'      (Direct candidate ranking /v1/rank)
- Output formats: formatted visual probability bars or machine-readable JSON.
"""

import argparse
import json
import os
import struct
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_URL = os.environ.get("CLM_URL", "http://127.0.0.1:8700")

# Popular file types and descriptions for /v1/systemone criteria
SYSTEMONE_CRITERIA = {
    "python": "Python source code (.py file with python syntax, import statements, def functions, classes, decorators)",
    "shell": "Bash or Unix shell script (.sh file with bash commands, shell variables, echo, export, set -e)",
    "elf": "Linux ELF binary executable (.elf, compiled machine code with 7f 45 4c 46 header bytes and symbols)",
    "c_cpp": "C or C++ programming language source code (.c, .cpp, .h with #include, pointers, structs, main function)",
    "javascript": "JavaScript or TypeScript source code (.js, .ts, npm modules, const/let, functions)",
    "dockerfile": "Dockerfile container build specification (FROM, RUN, COPY, ENTRYPOINT, CMD, WORKDIR)",
    "json": "JSON structured data format (.json file with curly braces, key-value pairs, arrays)",
    "yaml": "YAML configuration data file (.yaml, .yml with indented key-value mappings, docker-compose)",
    "markdown": "Markdown documentation text (.md file with markdown headings #, bullet lists, documentation)",
    "html": "HTML web document markup (.html with <html>, <div>, tags)",
    "archive": "Compressed archive file (zip, tar, gzip binary data archive)",
    "plain_text": "Plain human-readable text document (.txt, logs, unstructured notes)",
}

# Candidate answers for /v1/rank mode
RANK_CANDIDATES = [
    "Python source code",
    "Bash shell script",
    "Linux ELF binary executable",
    "C or C++ source code",
    "JavaScript or TypeScript code",
    "Dockerfile container build configuration",
    "JSON data format",
    "YAML configuration",
    "Markdown documentation",
    "HTML web markup",
    "Compressed archive (zip, tar, gzip)",
    "Plain text document",
]

# Mapping candidate names to short canonical IDs
RANK_TO_ID = {
    "Python source code": "python",
    "Bash shell script": "shell",
    "Linux ELF binary executable": "elf",
    "C or C++ source code": "c_cpp",
    "JavaScript or TypeScript code": "javascript",
    "Dockerfile container build configuration": "dockerfile",
    "JSON data format": "json",
    "YAML configuration": "yaml",
    "Markdown documentation": "markdown",
    "HTML web markup": "html",
    "Compressed archive (zip, tar, gzip)": "archive",
    "Plain text document": "plain_text",
}

# User-friendly display names
TYPE_LABELS = {
    "python": "Python Source Code (.py)",
    "shell": "Shell / Bash Script (.sh)",
    "elf": "Linux ELF Binary Executable (.elf / binary)",
    "c_cpp": "C / C++ Source Code (.c / .cpp)",
    "javascript": "JavaScript / TypeScript (.js / .ts)",
    "dockerfile": "Dockerfile Container Build File",
    "json": "JSON Data Format (.json)",
    "yaml": "YAML Configuration (.yaml / .yml)",
    "markdown": "Markdown Documentation (.md)",
    "html": "HTML Web Document (.html)",
    "archive": "Compressed Archive (.zip / .tar / .gz)",
    "plain_text": "Plain Text / Log (.txt)",
}


def inspect_file(filepath: str) -> Tuple[str, Dict[str, Any]]:
    """Inspect file header, magic bytes, shebang, and extract representation for CLM."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")
    if os.path.isdir(filepath):
        raise IsADirectoryError(f"Path is a directory, not a file: {filepath}")

    filename = os.path.basename(filepath)
    filesize = os.path.getsize(filepath)

    with open(filepath, "rb") as f:
        header = f.read(8192)

    meta: Dict[str, Any] = {
        "filename": filename,
        "filesize": filesize,
        "is_binary": False,
        "magic_hint": None,
    }

    # 1. ELF Binary Inspection
    if header.startswith(b"\x7fELF"):
        meta["is_binary"] = True
        meta["magic_hint"] = "ELF"
        ei_class = header[4] if len(header) > 4 else 0
        ei_data = header[5] if len(header) > 5 else 0
        bitness = "64-bit" if ei_class == 2 else "32-bit"
        endian = "little endian" if ei_data == 1 else "big endian"

        elf_type_str = "binary"
        if len(header) >= 18:
            endian_char = "<" if ei_data == 1 else ">"
            t = struct.unpack(f"{endian_char}H", header[16:18])[0]
            types = {
                1: "relocatable object (.o)",
                2: "executable",
                3: "shared object / PIE (.so)",
                4: "core dump",
            }
            elf_type_str = types.get(t, f"type {t}")

        # Extract printable ASCII strings (interpreter, symbols, libraries)
        strings: List[str] = []
        cur: List[str] = []
        for b in header[64:]:
            if 32 <= b <= 126:
                cur.append(chr(b))
            else:
                if len(cur) >= 4:
                    s = "".join(cur)
                    if not s.startswith("..."):
                        strings.append(s)
                cur = []
        symbols_excerpt = " ".join(strings[:25])

        state_text = (
            f"File: {filename} (size: {filesize} bytes)\n"
            f"Format: Linux ELF {bitness} {endian} {elf_type_str}\n"
            f"Header magic: 7f 45 4c 46\n"
            f"Embedded symbols & strings: {symbols_excerpt}"
        )
        return state_text, meta

    # 2. Known Archive / Binary Formats
    if header.startswith(b"PK\x03\x04"):
        meta["is_binary"] = True
        meta["magic_hint"] = "ZIP"
        return f"File: {filename}\nMagic: PK\\x03\\x04 (ZIP archive binary)", meta
    if header.startswith(b"\x1f\x8b"):
        meta["is_binary"] = True
        meta["magic_hint"] = "GZIP"
        return f"File: {filename}\nMagic: \\x1f\\x8b (GZIP compressed data)", meta
    if len(header) > 262 and header[257:262] == b"ustar":
        meta["is_binary"] = True
        meta["magic_hint"] = "TAR"
        return f"File: {filename}\nMagic: ustar (TAR archive)", meta
    if header.startswith(b"%PDF"):
        meta["is_binary"] = True
        meta["magic_hint"] = "PDF"
        return f"File: {filename}\nMagic: %PDF document", meta

    # Check for general binary (null bytes in first 512 bytes)
    if b"\x00" in header[:512]:
        meta["is_binary"] = True
        hex_preview = " ".join(f"{b:02x}" for b in header[:32])
        return (
            f"File: {filename} (size: {filesize} bytes)\n"
            f"Binary file. Hex preview: {hex_preview}"
        ), meta

    # 3. Text Files
    try:
        text = header.decode("utf-8")
    except UnicodeDecodeError:
        text = header.decode("latin1", errors="replace")

    lines = text.splitlines()
    shebang = None
    if lines and lines[0].startswith("#!"):
        shebang = lines[0]
        meta["shebang"] = shebang

    # Take first ~80 lines or up to 2,500 characters
    excerpt = "\n".join(lines[:80])[:2500]

    state_parts = [f"File: {filename}"]
    if shebang:
        state_parts.append(f"Shebang: {shebang}")
    state_parts.append(f"Content excerpt:\n{excerpt}")

    state_text = "\n".join(state_parts)
    return state_text, meta


def query_clm_systemone(
    base_url: str,
    state_text: str,
    model: str = "clm-latest",
    api_key: Optional[str] = None,
    timeout: int = 15,
) -> Dict[str, Any]:
    """Query /v1/systemone choice question."""
    url = f"{base_url.rstrip('/')}/v1/systemone"
    payload = {
        "model": model,
        "state": state_text,
        "questions": {
            "file_type": {
                "type": "choice",
                "instructions": "What is the file format, programming language, or type of this file?",
                "criteria": SYSTEMONE_CRITERIA,
            }
        },
    }

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=req_data, headers=headers)

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        latency_ms = resp.headers.get("X-CLM-Latency-Ms")
        if latency_ms:
            data["_latency_ms"] = float(latency_ms)
        return data


def query_clm_rank(
    base_url: str,
    state_text: str,
    model: str = "clm-latest",
    api_key: Optional[str] = None,
    timeout: int = 15,
) -> Dict[str, Any]:
    """Query /v1/rank candidate ranking."""
    url = f"{base_url.rstrip('/')}/v1/rank"
    payload = {
        "model": model,
        "context": state_text,
        "question": "What is the programming language or file format of this file?",
        "answers": RANK_CANDIDATES,
    }

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=req_data, headers=headers)

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        latency_ms = resp.headers.get("X-CLM-Latency-Ms")
        if latency_ms:
            data["_latency_ms"] = float(latency_ms)
        return data


def format_bar(prob: float, width: int = 20) -> str:
    """Render a text bar representing probability."""
    filled = int(round(prob * width))
    return "█" * filled + "░" * (width - filled)


def classify_file(
    filepath: str,
    base_url: str = DEFAULT_URL,
    endpoint_mode: str = "systemone",
    model: str = "clm-latest",
    top_k: int = 5,
    api_key: Optional[str] = None,
    verbose: bool = False,
    json_output: bool = False,
) -> Dict[str, Any]:
    """Inspect and classify a file using the selected CLM endpoint."""
    state_text, meta = inspect_file(filepath)

    if verbose and not json_output:
        print(f"\n[DEBUG] Extracted state representation for '{filepath}':")
        print("-" * 50)
        print(state_text[:500] + ("..." if len(state_text) > 500 else ""))
        print("-" * 50)

    try:
        if endpoint_mode == "rank":
            res = query_clm_rank(base_url, state_text, model=model, api_key=api_key)
            ranked_list = res.get("ranked", [])
            choice = RANK_TO_ID.get(ranked_list[0]["candidate"], ranked_list[0]["candidate"]) if ranked_list else "unknown"
            top_prob = ranked_list[0]["prob"] if ranked_list else 0.0
            probabilities = {
                RANK_TO_ID.get(item["candidate"], item["candidate"]): item["prob"]
                for item in ranked_list
            }
            # Confidence in rank: difference between top prob and mean of remainder
            other_probs = [item["prob"] for item in ranked_list[1:]]
            confidence = (top_prob - (sum(other_probs) / len(other_probs))) if other_probs else 1.0
        else:
            res = query_clm_systemone(base_url, state_text, model=model, api_key=api_key)
            answer = res.get("answers", {}).get("file_type", {})
            choice = answer.get("choice", "unknown")
            confidence = answer.get("confidence", 0.0)
            probabilities = answer.get("probabilities", {})
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        raise RuntimeError(f"CLM HTTP {e.code}: {err_body}")
    except urllib.error.URLError as e:
        raise ConnectionError(
            f"Cannot reach CLM at {base_url} ({e.reason}).\n"
            "Ensure the Docker container is running: 'docker compose ps' or 'docker ps'."
        )

    # Sort probabilities descending
    sorted_probs = sorted(probabilities.items(), key=lambda kv: -kv[1])

    result_data = {
        "file": filepath,
        "metadata": meta,
        "endpoint_mode": endpoint_mode,
        "predicted_type": choice,
        "predicted_label": TYPE_LABELS.get(choice, choice),
        "confidence": confidence,
        "probabilities": dict(sorted_probs),
        "model": res.get("model", model),
        "latency_ms": res.get("_latency_ms"),
    }

    if json_output:
        print(json.dumps(result_data, indent=2))
        return result_data

    # Human-readable output
    pred_label = TYPE_LABELS.get(choice, choice)
    top_prob = probabilities.get(choice, 0.0)

    print("\n" + "=" * 65)
    print(f"File:       {filepath}")
    size_str = (
        f"{meta['filesize']:,} bytes"
        if meta["filesize"] < 1024 * 1024
        else f"{meta['filesize'] / (1024 * 1024):.2f} MB"
    )
    print(f"Size:       {size_str}")
    if meta.get("magic_hint"):
        print(f"Header:     Detected {meta['magic_hint']} magic bytes")
    if meta.get("shebang"):
        print(f"Shebang:    {meta['shebang']}")
    print("-" * 65)
    print(f"Prediction: {pred_label}")
    print(f"Confidence: {top_prob * 100:.1f}% (margin: {confidence:.3f})")
    if res.get("_latency_ms"):
        print(f"Latency:    {res['_latency_ms']:.1f} ms (server)")
    print("-" * 65)
    print(f"Top {min(top_k, len(sorted_probs))} Probabilities:")

    for rank, (cand, prob) in enumerate(sorted_probs[:top_k], start=1):
        label = TYPE_LABELS.get(cand, cand)
        bar = format_bar(prob, width=20)
        marker = " ◄" if cand == choice else ""
        print(f"  {rank}. {label:32} {bar} {prob * 100:5.1f}%{marker}")
    print("=" * 65)

    return result_data


def main():
    parser = argparse.ArgumentParser(
        description="Classify file types using the Contrastive Language Model (CLM) API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  ./file_classifier.py client_example.py
  ./file_classifier.py entrypoint.sh /bin/ls Dockerfile
  ./file_classifier.py --mode rank client_example.py entrypoint.sh
  ./file_classifier.py --top 3 --json /bin/bash
  ./file_classifier.py --url http://127.0.0.1:8700 ./docker-compose.yml
""",
    )
    parser.add_argument(
        "files",
        nargs="+",
        help="One or more file paths to classify",
    )
    parser.add_argument(
        "--url",
        "-u",
        default=DEFAULT_URL,
        help=f"CLM service base URL (default: {DEFAULT_URL} or $CLM_URL)",
    )
    parser.add_argument(
        "--mode",
        "-e",
        choices=["systemone", "rank"],
        default="systemone",
        help="API mode: 'systemone' (POST /v1/systemone choice) or 'rank' (POST /v1/rank)",
    )
    parser.add_argument(
        "--model",
        "-m",
        default="clm-latest",
        help="Model name (default: clm-latest)",
    )
    parser.add_argument(
        "--top",
        "-k",
        type=int,
        default=5,
        help="Number of top candidates to display (default: 5)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("CLM_API_KEY"),
        help="Optional API key for authorization ($CLM_API_KEY)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print verbose debug info and the exact representation sent to CLM",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON format instead of human-readable text",
    )

    args = parser.parse_args()

    # Pre-check health endpoint
    try:
        health_url = f"{args.url.rstrip('/')}/health"
        req = urllib.request.Request(health_url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            health = json.loads(resp.read().decode("utf-8"))
            if not health.get("ok"):
                print(f"[Warning] CLM service at {args.url} reports ok=false", file=sys.stderr)
    except Exception as e:
        print(f"[Error] Failed to connect to CLM at {args.url}: {e}", file=sys.stderr)
        print("Please check if the container is running: 'docker compose ps' or 'docker compose up -d'.", file=sys.stderr)
        sys.exit(1)

    errors = 0
    for filepath in args.files:
        try:
            classify_file(
                filepath,
                base_url=args.url,
                endpoint_mode=args.mode,
                model=args.model,
                top_k=args.top,
                api_key=args.api_key,
                verbose=args.verbose,
                json_output=args.json,
            )
        except Exception as e:
            print(f"[Error] Failed to classify '{filepath}': {e}", file=sys.stderr)
            errors += 1

    if errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
