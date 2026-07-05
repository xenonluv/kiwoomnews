# KiwoomNews 자동매매 래퍼 (Windows Task Scheduler 전용)
#   -Task exec-krx : 15:18 KRX 종가베팅 매수 (NXT 불가 종목)
#   -Task exec-nxt : 19:50 NXT 종가베팅 매수 (NXT 가능 종목, 5호가위 지정가)
#   -Task monitor  : 장중 청산 감시 (-5%/+7%50%/+11%/본전)
#
# ⚠⚠ 실발주 안전장치(이중): (1) 웹 토글 KV autotrade:enabled=1  AND  (2) 아래 AUTOTRADE_LIVE=1.
#    기본은 AUTOTRADE_LIVE 미설정 → 스케줄이 돌아도 DRY(미발주 로그만). 실계좌 발주를 원하면
#    아래 줄 주석(#)을 해제하라. (소액 스모크 검증 완료 후에만!)
param([Parameter(Mandatory = $true)][ValidateSet("exec-krx", "exec-nxt", "monitor")][string]$Task)
$ErrorActionPreference = "Continue"
$env:PYTHONUTF8 = "1"
# $env:AUTOTRADE_LIVE = "1"   # ← 실발주 켜기(주석 해제). 기본은 안전(미발주).
$repo = "C:\Users\User\Documents\kiwoom-prod"
$py   = "C:\Python313\python.exe"
$log  = Join-Path $repo "autotrade.log"
Set-Location $repo
switch ($Task) {
  "exec-krx" { & $py "scripts\autotrade_executor.py" --slot krx *>> $log }
  "exec-nxt" { & $py "scripts\autotrade_executor.py" --slot nxt *>> $log }
  "monitor"  { & $py "scripts\autotrade_monitor.py" *>> $log }
}
