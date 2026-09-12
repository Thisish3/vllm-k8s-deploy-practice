"""
CI에서 매 커밋마다 돌리는 CPU 전용 지연시간 벤치마크.

실제 vLLM/GPU 추론은 GitHub-hosted 러너에서 돌릴 수 없으므로(GPU 없음),
서버의 요청 처리 오버헤드(라우팅, 세마포어 기반 동시성 제어, 응답 직렬화)를
측정 대상으로 삼는다. 이 오버헤드는 실제 vLLM 서버에서도 동일하게 존재하는
"추론 자체가 아닌" 비용이라, 회귀를 잡아내는 CI 게이트로는 의미가 있다.

baseline.json과 비교해서 p50/p95가 허용 오차(기본 25%)를 넘으면 비정상
종료(exit 1)한다 — GitHub Actions에서 그대로 실패 처리됨.
"""

import argparse
import json
import os
import statistics
import time
from pathlib import Path

os.environ.setdefault("STANDIN_TOKENS_PER_SEC", "100000")  # CI에서는 sleep 최소화
os.environ.setdefault("STANDIN_MAX_CONCURRENCY", "8")

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402

BASELINE_PATH = Path(__file__).parent.parent / "benchmarks" / "baseline.json"


def run(n_requests: int) -> dict:
    client = TestClient(app)
    latencies_ms = []
    for _ in range(n_requests):
        t0 = time.perf_counter()
        resp = client.post("/v1/completions", json={"prompt": "hello", "max_tokens": 16})
        assert resp.status_code == 200, resp.text
        latencies_ms.append((time.perf_counter() - t0) * 1000)

    latencies_ms.sort()
    p50 = statistics.median(latencies_ms)
    p95 = latencies_ms[int(len(latencies_ms) * 0.95) - 1]
    return {"n_requests": n_requests, "p50_ms": round(p50, 3), "p95_ms": round(p95, 3)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--tolerance", type=float, default=0.25, help="허용 회귀 비율 (0.25 = 25%)")
    args = parser.parse_args()

    result = run(args.n)
    print(f"측정 결과: {json.dumps(result, ensure_ascii=False)}")

    if args.update_baseline:
        BASELINE_PATH.parent.mkdir(exist_ok=True)
        BASELINE_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        print(f"baseline 갱신: {BASELINE_PATH}")
        return

    if not BASELINE_PATH.exists():
        raise SystemExit(f"baseline이 없습니다: {BASELINE_PATH} (--update-baseline로 먼저 생성)")

    baseline = json.loads(BASELINE_PATH.read_text())
    regressed = []
    for key in ("p50_ms", "p95_ms"):
        limit = baseline[key] * (1 + args.tolerance)
        if result[key] > limit:
            regressed.append(
                f"{key}: {result[key]}ms > baseline {baseline[key]}ms + {args.tolerance:.0%} 허용치 ({limit:.3f}ms)"
            )

    if regressed:
        print("성능 회귀 감지:")
        for line in regressed:
            print(f"  - {line}")
        raise SystemExit(1)

    print(f"OK — baseline({baseline['p50_ms']}ms/{baseline['p95_ms']}ms) 대비 {args.tolerance:.0%} 이내")


if __name__ == "__main__":
    main()
