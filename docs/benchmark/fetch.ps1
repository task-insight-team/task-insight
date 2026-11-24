# 분석 보고서가 읽은 오픈소스 저장소 3곳을 "같은 커밋"으로 받는다 (검증용, 읽기 전용).
# 우리 저장소에는 외부 코드·데이터를 넣지 않는다. 기본 위치는 임시 폴더라 git 추적 대상이 아니다.
#
#   사용법:  powershell -File docs\benchmark\fetch.ps1                 (3곳 모두)
#            powershell -File docs\benchmark\fetch.ps1 -Only dash      (agno dash만, 가장 작음)
#            powershell -File docs\benchmark\fetch.ps1 -Dest D:\ti-bench
#
# 용량: WrenAI 약 37MB, agno dash 약 0.5MB, InfiAgent 약 112MB(데이터 포함, 데이터는 CC BY-NC 4.0).
# 받은 폴더 안의 코드는 실행하지 않는다 (설치·빌드·테스트 금지). 파일을 읽기만 한다.
param(
    [string]$Dest = (Join-Path $env:TEMP "ti-bench"),
    [ValidateSet("all", "wrenai", "dash", "infiagent")][string]$Only = "all"
)

$repos = @(
    @{ Key = "wrenai";    Dir = "Canner__WrenAI";       Url = "https://github.com/Canner/WrenAI.git";          Sha = "48de0b6cbd02cbbe3753f29e37f4be24e8a9a7fd" },
    @{ Key = "dash";      Dir = "agno-agi__dash";       Url = "https://github.com/agno-agi/dash.git";          Sha = "6d86fca8e85b17349238389f5fad92d15e8f633f" },
    @{ Key = "infiagent"; Dir = "InfiAgent__InfiAgent"; Url = "https://github.com/InfiAgent/InfiAgent.git";    Sha = "3d6c4a70198e0a41fadf539f5b43c88b8c1a2d9c" }
)

foreach ($r in $repos) {
    if ($Only -ne "all" -and $Only -ne $r.Key) { continue }
    $path = Join-Path $Dest $r.Dir
    if (Test-Path $path) { Write-Host "이미 있음(건너뜀): $path"; continue }
    New-Item -ItemType Directory -Force $path | Out-Null
    git -C $path init -q
    git -C $path remote add origin $r.Url
    git -C $path fetch -q --depth 1 origin $r.Sha
    if ($LASTEXITCODE -ne 0) { Write-Error "받기 실패: $($r.Url) @ $($r.Sha)"; continue }
    git -C $path checkout -q FETCH_HEAD
    $got = (git -C $path rev-parse HEAD).Trim()
    $ok = if ($got -eq $r.Sha) { "일치" } else { "불일치!" }
    Write-Host ("{0}: {1} ({2})" -f $r.Dir, $got, $ok)
}
Write-Host "완료. 위치: $Dest"
