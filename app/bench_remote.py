"""
원격 vLLM 서버(예: Windows PC의 RTX 3080)에 HTTP로 동시 부하를 걸어
TTFT / 요청 지연 / 출력 처리량을 측정한다. 표준 라이브러리만 사용하므로
Mac에서 추가 설치 없이 실행 가능:

    python3 app/bench_remote.py --base-url http://192.168.0.10:8000

bench_latency.py(CI 게이트, 프로세스 내 TestClient)와 달리 실제 네트워크
너머의 서버를 대상으로 한다. 서버가 SSE 스트리밍을 지원하면(vLLM) 첫 토큰
시각으로 TTFT를 재고, JSON으로 한 번에 응답하면(stand-in) TTFT = 전체 지연.
"""

import argparse
import json
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def get_model(base_url: str) -> str:
    with urllib.request.urlopen(f"{base_url}/v1/models", timeout=10) as resp:
        return json.load(resp)["data"][0]["id"]


def one_request(base_url: str, model: str, prompt: str, max_tokens: int) -> dict:
    body = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": 0,
            "ignore_eos": True,  # 출력 길이를 고정해야 처리량 비교가 공정함
            "stream": True,
            "stream_options": {"include_usage": True},
        }
    ).encode()
    req = urllib.request.Request(
        f"{base_url}/v1/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    ttft = None
    tokens = 0
    with urllib.request.urlopen(req, timeout=300) as resp:
        if "text/event-stream" not in resp.headers.get("Content-Type", ""):
            data = json.load(resp)
            elapsed = time.perf_counter() - t0
            return {
                "ttft": elapsed,
                "latency": elapsed,
                "tokens": data.get("usage", {}).get("completion_tokens", 0),
            }
        for raw in resp:
            line = raw.decode().strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[len("data: "):])
            if ttft is None and chunk.get("choices"):
                ttft = time.perf_counter() - t0
            if chunk.get("usage"):
                tokens = chunk["usage"]["completion_tokens"]
    latency = time.perf_counter() - t0
    return {"ttft": ttft or latency, "latency": latency, "tokens": tokens}


def pct(values: list[float], p: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p))]


def run(base_url: str, model: str, n: int, concurrency: int, prompt: str, max_tokens: int):
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(
            pool.map(lambda _: one_request(base_url, model, prompt, max_tokens), range(n))
        )
    wall = time.perf_counter() - t0
    ttfts = [r["ttft"] * 1000 for r in results]
    lats = [r["latency"] * 1000 for r in results]
    total_tokens = sum(r["tokens"] for r in results)
    return {
        "concurrency": concurrency,
        "n_requests": n,
        "ttft_p50_ms": round(statistics.median(ttfts), 1),
        "ttft_p95_ms": round(pct(ttfts, 0.95), 1),
        "latency_p50_ms": round(statistics.median(lats), 1),
        "latency_p95_ms": round(pct(lats, 0.95), 1),
        "output_tok_per_s": round(total_tokens / wall, 1),
        "req_per_s": round(n / wall, 2),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True, help="예: http://192.168.0.10:8000")
    parser.add_argument("--model", help="생략하면 /v1/models의 첫 모델")
    parser.add_argument("--n", type=int, default=64, help="동시성 단계별 요청 수")
    parser.add_argument("--concurrency", default="1,4,16,32", help="쉼표로 구분한 동시성 단계")
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--prompt", default="Explain how a GPU executes a matrix multiply.")
    parser.add_argument("--out", help="결과를 JSON 파일로 저장")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    model = args.model or get_model(base_url)
    print(f"대상: {base_url}  모델: {model}")

    rows = []
    for c in (int(x) for x in args.concurrency.split(",")):
        row = run(base_url, model, args.n, c, args.prompt, args.max_tokens)
        rows.append(row)
        print(
            f"c={c:>3}  TTFT p50 {row['ttft_p50_ms']:>8.1f}ms  p95 {row['ttft_p95_ms']:>8.1f}ms  "
            f"| latency p50 {row['latency_p50_ms']:>8.1f}ms  "
            f"| {row['output_tok_per_s']:>8.1f} tok/s  {row['req_per_s']:>6.2f} req/s"
        )

    if args.out:
        with open(args.out, "w") as f:
            json.dump({"base_url": base_url, "model": model, "results": rows}, f, indent=2)
        print(f"저장: {args.out}")


if __name__ == "__main__":
    main()
