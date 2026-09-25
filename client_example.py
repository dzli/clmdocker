#!/usr/bin/env python3
"""Example client script to interact with the CLM Docker service from the host.

Uses only Python's standard library (urllib.request, json) so NO extra packages
or virtual environments need to be installed on your host machine.
"""

import json
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8700"


def send_request(url: str, data: dict | None = None, api_key: str | None = None) -> dict:
    """Send an HTTP GET or POST request and return parsed JSON."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req_data = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=req_data, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        print(f"[Error] HTTP {e.code}: {err_body}", file=sys.stderr)
        raise
    except urllib.error.URLError as e:
        print(f"[Error] Could not reach {url}: {e.reason}", file=sys.stderr)
        print("Tip: The container may still be downloading weights or starting up.", file=sys.stderr)
        print("Run 'docker compose logs -f' to check container progress.", file=sys.stderr)
        raise


def test_health(base_url: str):
    print("\n--- 1. Health Check (/health) ---")
    res = send_request(f"{base_url}/health")
    print(json.dumps(res, indent=2))


def test_models(base_url: str):
    print("\n--- 2. Models List (/v1/models) ---")
    res = send_request(f"{base_url}/v1/models")
    print(json.dumps(res, indent=2))


def test_rank(base_url: str):
    print("\n--- 3. Candidate Ranking (/v1/rank) ---")
    payload = {
        "context": "The user is asking a basic astronomy question.",
        "question": "What causes tides on Earth?",
        "answers": [
            "The Moon's gravitational pull.",
            "Photosynthesis in deep ocean plants.",
            "Earth's magnetic field rotating.",
        ],
    }
    print(f"Question: {payload['question']}")
    print(f"Candidates: {payload['answers']}")
    res = send_request(f"{base_url}/v1/rank", data=payload)
    print("Ranked results:")
    for item in res.get("ranked", []):
        print(f"  Rank {item['rank']}: prob={item['prob']:.4f} -> {item['candidate']}")


def test_system_one(base_url: str):
    print("\n--- 4. System One Typed Decision (/v1/systemone) ---")
    state = "Customer: my invoice was charged twice and nobody answers the phone!"
    payload = {
        "state": state,
        "questions": {
            "urgency": {
                "type": "noul",
                "instructions": "Is this urgent?",
            },
            "department": {
                "type": "choice",
                "instructions": "Which team should handle this?",
                "criteria": {
                    "billing": "Charges, invoices, payment disputes, refunds",
                    "technical": "System bugs, crashes, service outages",
                    "general": "General inquiries and feedback",
                },
            },
            "frustration": {
                "type": "score",
                "instructions": "How frustrated is the customer?",
                "criteria": ["Calm", "Frustrated", "Very angry"],
            },
        },
    }
    print(f"State: {state}")
    res = send_request(f"{base_url}/v1/systemone", data=payload)
    answers = res.get("answers", {})
    print("Answers:")
    if "urgency" in answers:
        print(f"  Urgency (noul probability true): {answers['urgency'].get('noul')}")
    if "department" in answers:
        dept = answers["department"]
        print(f"  Department (choice): {dept.get('choice')} (confidence: {dept.get('confidence')})")
        print(f"  Department probabilities: {dept.get('probabilities')}")
    if "frustration" in answers:
        frust = answers["frustration"]
        print(f"  Frustration (score 0-2): {frust.get('score')} (confidence: {frust.get('confidence')})")


def main():
    base_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    print(f"Testing CLM Service at {base_url}...")

    try:
        test_health(base_url)
        test_models(base_url)
        test_rank(base_url)
        test_system_one(base_url)
        print("\nAll tests completed successfully!")
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
