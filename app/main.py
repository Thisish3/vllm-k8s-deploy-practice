"""
vLLM OpenAI 호환 서버의 스탠드인(stand-in) — Kubernetes 배포 패턴(헬스체크,
롤링 업데이트, HPA)을 GPU 없이 빠르게 검증하기 위한 최소 모사 서버입니다.

실제 vLLM과 다른 점:
- 진짜 추론을 하지 않고, max_tokens에 비례해서 sleep으로 디코딩 시간을 흉내냅니다.
- /metrics가 실제 vLLM의 Prometheus 지표(vllm:num_requests_running 등)와
  같은 "형태"의 지표를 노출하지만 값은 이 프로세스의 동시 요청 수를 셀 뿐입니다.

목적: 이 환경엔 GPU가 없어서 실제 vLLM 컨테이너로 로컬 클러스터를 띄우고
      검증할 수 없습니다 (이미지도 수 GB, 모델 로딩도 GPU 필요). 대신 API
      계약(엔드포인트/헬스체크/지표 형태)이 동일한 경량 서버로 배포
      매니페스트 자체(롤링 업데이트, 헬스체크, HPA 스케일링 동작)를
      실제로 로컬 클러스터에 띄워서 검증합니다.
"""

import asyncio
import os
import time

from fastapi import FastAPI, Response
from pydantic import BaseModel

app = FastAPI(title="vllm-stand-in")

_num_running = 0
_num_waiting = 0
_total_requests = 0
_start_time = time.time()

# 초당 처리 가능한 토큰 수를 흉내내는 값 (작을수록 요청이 오래 걸려서
# HPA가 더 쉽게 스케일 아웃을 트리거하도록 만듦 - 데모 목적)
TOKENS_PER_SEC = float(os.environ.get("STANDIN_TOKENS_PER_SEC", "40"))
MAX_CONCURRENCY = int(os.environ.get("STANDIN_MAX_CONCURRENCY", "4"))
_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)


class CompletionRequest(BaseModel):
    prompt: str = ""
    max_tokens: int = 16


@app.get("/health")
async def health():
    return {"status": "ok", "uptime_sec": round(time.time() - _start_time, 1)}


@app.get("/metrics")
async def metrics():
    # 실제 vLLM의 vllm:num_requests_running / vllm:num_requests_waiting와
    # 같은 형태(gauge)의 Prometheus 텍스트 포맷.
    body = (
        f"# HELP standin_num_requests_running Requests currently being processed\n"
        f"# TYPE standin_num_requests_running gauge\n"
        f"standin_num_requests_running {_num_running}\n"
        f"# HELP standin_num_requests_waiting Requests queued\n"
        f"# TYPE standin_num_requests_waiting gauge\n"
        f"standin_num_requests_waiting {_num_waiting}\n"
        f"# HELP standin_requests_total Total requests served\n"
        f"# TYPE standin_requests_total counter\n"
        f"standin_requests_total {_total_requests}\n"
    )
    return Response(content=body, media_type="text/plain")


@app.post("/v1/completions")
async def completions(req: CompletionRequest):
    global _num_running, _num_waiting, _total_requests
    _num_waiting += 1
    async with _semaphore:
        _num_waiting -= 1
        _num_running += 1
        try:
            gen_time = max(req.max_tokens, 1) / TOKENS_PER_SEC
            await asyncio.sleep(gen_time)
            _total_requests += 1
            return {
                "id": f"cmpl-standin-{_total_requests}",
                "object": "text_completion",
                "choices": [
                    {
                        "text": f"[stand-in output, {req.max_tokens} tokens simulated]",
                        "index": 0,
                        "finish_reason": "length",
                    }
                ],
                "usage": {"completion_tokens": req.max_tokens},
            }
        finally:
            _num_running -= 1
