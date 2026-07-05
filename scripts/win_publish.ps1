# KiwoomNews 레이더 자동 게시 래퍼 (Windows Task Scheduler 전용)
# 역할: PYTHONUTF8 설정 → kiwoom-prod에서 publish.py 실행 → publish.log에 누적 기록.
# publish.py가 radar.py(키움)를 돌려 web/data/radar.json 갱신 후 변경 시에만 kiwoomnews로 push.
$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"            # 한글 인코딩(cp949 깨짐 방지)
$repo = "C:\Users\User\Documents\kiwoom-prod"
$py   = "C:\Python313\python.exe"
$log  = Join-Path $repo "publish.log"
Set-Location $repo
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $log -Value "=== $ts publish 시작 ===" -Encoding utf8
& $py "scripts\publish.py" *>> $log
Add-Content -Path $log -Value "=== $ts 종료 (exit $LASTEXITCODE) ===" -Encoding utf8
