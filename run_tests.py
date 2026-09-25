#!/usr/bin/env python3
"""Automated End-to-End Self-Test for CLM Docker Service.

Tests the Docker container lifecycle and all HTTP API endpoints from the host.
Uses Python standard library only (no external host dependencies).
"""

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE_URL = "http://127.0.0.1:8700"
CONTAINER_NAME = "clm-selftest"
IMAGE_NAME = "clm:latest"


def log(msg: str):
    print(f"[TEST] {msg}", flush=True)


def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def wait_for_ready(url: str, timeout_sec: int = 30) -> bool:
    log(f"Waiting for {url}/health to return 200 OK...")
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False


def http_get(path: str) -> tuple[int, dict | str]:
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        content_type = resp.headers.get("Content-Type", "")
        body = resp.read().decode("utf-8")
        if "application/json" in content_type:
            return resp.status, json.loads(body)
        return resp.status, body


def http_post(path: str, payload: dict) -> tuple[int, dict]:
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def main():
    print("=" * 65)
    print(" Running CLM Docker Self-Test")
    print("=" * 65)

    # 1. Clean up any existing container
    run_cmd(["docker", "rm", "-f", CONTAINER_NAME])

    # 2. Start container in mock mode for fast verification
    log(f"Launching container '{CONTAINER_NAME}' with image '{IMAGE_NAME}' (MODE=mock)...")
    res = run_cmd([
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        "-p", "8700:8700",
        "-e", "MODE=mock",
        IMAGE_NAME,
    ])
    if res.returncode != 0:
        log(f"FAILED to start container: {res.stderr}")
        sys.exit(1)
    container_id = res.stdout.strip()[:12]
    log(f"Container started (ID: {container_id})")

    passed = 0
    total = 0

    try:
        # 3. Wait for container to be ready
        if not wait_for_ready(BASE_URL, timeout_sec=20):
            log("FAILED: Container did not become ready in time.")
            log_res = run_cmd(["docker", "logs", CONTAINER_NAME])
            print("Container Logs:\n", log_res.stdout, log_res.stderr)
            sys.exit(1)
        log("Container is ready and listening on port 8700!")

        # TEST 1: GET /health
        total += 1
        log("\n--> Test 1: GET /health")
        status, data = http_get("/health")
        assert status == 200, f"Expected 200, got {status}"
        assert data.get("ok") is True, f"Expected ok: True, got {data}"
        assert "models" in data, "Expected 'models' key in health response"
        print("    Status: 200 OK")
        print(f"    Payload: {json.dumps(data)}")
        log("    [PASS] Health check verified.")
        passed += 1

        # TEST 2: GET /v1/models
        total += 1
        log("\n--> Test 2: GET /v1/models")
        status, data = http_get("/v1/models")
        assert status == 200, f"Expected 200, got {status}"
        assert "models" in data and len(data["models"]) > 0, "No models listed"
        print(f"    Models found: {[m['name'] for m in data['models']]}")
        log("    [PASS] Models list endpoint verified.")
        passed += 1

        # TEST 3: GET / (Web Playground UI)
        total += 1
        log("\n--> Test 3: GET / (Playground UI)")
        status, html = http_get("/")
        assert status == 200, f"Expected 200, got {status}"
        assert "<title>CLM Playground</title>" in html, "Playground HTML title missing"
        assert "app.js" in html, "Static asset reference missing"
        log("    [PASS] Web Playground HTML served successfully.")
        passed += 1

        # TEST 4: POST /v1/rank (Candidate ranking)
        total += 1
        log("\n--> Test 4: POST /v1/rank")
        rank_payload = {
            "context": "Customer inquiries",
            "question": "What is the capital of France?",
            "answers": ["Paris", "London", "Berlin", "Madrid"],
        }
        status, data = http_post("/v1/rank", rank_payload)
        assert status == 200, f"Expected 200, got {status}"
        assert "ranked" in data, "No 'ranked' list in response"
        assert len(data["ranked"]) == 4, f"Expected 4 ranked items, got {len(data['ranked'])}"
        top = data["ranked"][0]
        print(f"    Top candidate: {top['candidate']} (prob: {top['prob']:.4f})")
        log("    [PASS] /v1/rank returned valid ranked candidates.")
        passed += 1

        # TEST 5: POST /v1/systemone (Typed decisions: noul, choice, score)
        total += 1
        log("\n--> Test 5: POST /v1/systemone (Typed Decisions)")
        sysone_payload = {
            "state": "The user reported: 'Website login button fails with HTTP 500 error!'",
            "questions": {
                "is_bug": {
                    "type": "noul",
                    "instructions": "Is this a software bug report?",
                },
                "category": {
                    "type": "choice",
                    "instructions": "Categorize this issue",
                    "criteria": {
                        "frontend": "UI elements, buttons, CSS, javascript",
                        "backend": "Server errors, database, 500 errors",
                        "billing": "Invoices, credit card charges",
                    },
                },
                "severity": {
                    "type": "score",
                    "instructions": "Rate severity from 0 (minor) to 2 (critical)",
                    "criteria": ["Minor", "Major", "Blocker"],
                },
            },
        }
        status, data = http_post("/v1/systemone", sysone_payload)
        assert status == 200, f"Expected 200, got {status}"
        answers = data.get("answers", {})
        assert "is_bug" in answers, "Missing 'is_bug' answer"
        assert "category" in answers, "Missing 'category' answer"
        assert "severity" in answers, "Missing 'severity' answer"

        print(f"    is_bug (noul probability true): {answers['is_bug'].get('noul')}")
        print(f"    category (choice): {answers['category'].get('choice')} "
              f"(confidence: {answers['category'].get('confidence'):.4f})")
        print(f"    category probabilities: {answers['category'].get('probabilities')}")
        print(f"    severity (score): {answers['severity'].get('score'):.4f}")
        log("    [PASS] /v1/systemone typed questions evaluated successfully.")
        passed += 1

        # TEST 6: Error handling (invalid request)
        total += 1
        log("\n--> Test 6: Error Handling (Invalid Payload)")
        bad_payload = {"invalid_field": 123}
        try:
            http_post("/v1/systemone", bad_payload)
            log("    FAILED: Server accepted invalid payload!")
        except urllib.error.HTTPError as e:
            assert e.code == 422, f"Expected HTTP 422, got {e.code}"
            log(f"    [PASS] Server correctly rejected bad payload with HTTP {e.code}.")
            passed += 1

    finally:
        log("\nCleaning up test container...")
        run_cmd(["docker", "rm", "-f", CONTAINER_NAME])
        log("Container cleaned up.")

    print("\n" + "=" * 65)
    print(f" Self-Test Results: {passed}/{total} Passed")
    print("=" * 65)
    if passed == total:
        print("ALL TESTS PASSED SUCCESSFULLY!")
        return 0
    else:
        print("SOME TESTS FAILED!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
