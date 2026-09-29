# Windows PC(RTX 3080)에서 vLLM 컨테이너를 띄우기 전 환경 점검.
# 사용법: PowerShell에서  .\windows\check-env.ps1
# 실행 정책에 막히면:  powershell -ExecutionPolicy Bypass -File .\windows\check-env.ps1

$ErrorActionPreference = "Continue"
$failed = 0

function Step($name, [scriptblock]$body, $hint) {
    Write-Host "`n=== $name" -ForegroundColor Cyan
    $global:LASTEXITCODE = 0
    try { & $body; $ok = $? -and $LASTEXITCODE -eq 0 } catch { Write-Host $_; $ok = $false }
    if (-not $ok) {
        Write-Host "[FAIL] $name" -ForegroundColor Red
        Write-Host "  -> $hint" -ForegroundColor Yellow
        $script:failed++
    } else {
        Write-Host "[OK] $name" -ForegroundColor Green
    }
}

Step "NVIDIA 드라이버 (호스트)" { nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv } `
    "https://www.nvidia.com/drivers 에서 GeForce 드라이버 설치 후 재부팅"

Step "WSL2" { wsl --status } `
    "관리자 PowerShell에서 'wsl --install' 실행 후 재부팅"

Step "Docker 데몬" { docker version --format "client {{.Client.Version}} / server {{.Server.Version}}" } `
    "Docker Desktop 설치/실행, Settings > General > 'Use the WSL 2 based engine' 켜기"

Step "컨테이너 안에서 GPU 인식" { docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi -L } `
    "드라이버 최신화 + Docker Desktop 재시작. WSL 안에는 NVIDIA 드라이버를 따로 설치하지 말 것(호스트 드라이버를 공유)"

Write-Host ""
if ($failed -eq 0) {
    Write-Host "모든 점검 통과 — 다음: .\windows\run-vllm.ps1" -ForegroundColor Green
} else {
    Write-Host "$failed 개 항목 실패 — 위 안내대로 고친 뒤 다시 실행" -ForegroundColor Red
    exit 1
}
