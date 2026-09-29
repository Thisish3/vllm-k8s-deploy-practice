# RTX 3080(10GB)에서 vLLM OpenAI 호환 서버를 Docker로 실행.
# 사용법:  .\windows\run-vllm.ps1              (기본: TinyLlama, 포트 8000)
#          .\windows\run-vllm.ps1 -Stop        (컨테이너 중지/삭제)
#          .\windows\run-vllm.ps1 -OpenFirewall  (관리자 권한 필요, Mac에서 LAN으로 접속하려면)

param(
    [string]$Model = "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
    [int]$Port = 8000,
    [int]$MaxModelLen = 2048,
    # 3080은 윈도우 화면 출력도 같은 VRAM을 쓰므로 여유를 남긴다.
    [double]$GpuMemUtil = 0.80,
    [string]$Image = "vllm/vllm-openai:latest",
    [switch]$Stop,
    [switch]$OpenFirewall
)

$name = "vllm-3080"

if ($Stop) {
    docker rm -f $name
    exit $LASTEXITCODE
}

if ($OpenFirewall) {
    New-NetFirewallRule -DisplayName "vLLM $Port" -Direction Inbound -Protocol TCP `
        -LocalPort $Port -Action Allow -Profile Private
    exit $LASTEXITCODE
}

docker rm -f $name 2>$null | Out-Null

# hf-cache 볼륨: 모델 가중치를 컨테이너 재시작마다 다시 받지 않도록.
# (Windows 폴더 바인드 마운트는 WSL2 경계를 넘어 느리므로 named volume 사용)
docker run -d --name $name --gpus all --ipc=host `
    -p "${Port}:8000" `
    -v hf-cache:/root/.cache/huggingface `
    $Image `
    --model $Model `
    --max-model-len $MaxModelLen `
    --gpu-memory-utilization $GpuMemUtil `
    --enable-prefix-caching
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "모델 로딩 대기 중 (첫 실행은 이미지/가중치 다운로드로 수 분 걸림)..."
$deadline = (Get-Date).AddMinutes(20)
while ((Get-Date) -lt $deadline) {
    if (-not (docker ps -q -f "name=^$name$")) {
        Write-Host "컨테이너가 종료됨 — 마지막 로그:" -ForegroundColor Red
        docker logs --tail 40 $name
        exit 1
    }
    try {
        Invoke-WebRequest -UseBasicParsing "http://localhost:$Port/health" -TimeoutSec 2 | Out-Null
        break
    } catch { Start-Sleep -Seconds 5 }
}

$ip = (Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.PrefixOrigin -eq "Dhcp" } | Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "준비 완료: http://localhost:$Port  (LAN: http://${ip}:$Port)" -ForegroundColor Green
Write-Host "Mac에서 벤치마크:  python3 app/bench_remote.py --base-url http://${ip}:$Port"
Write-Host "로그 보기:        docker logs -f $name"
