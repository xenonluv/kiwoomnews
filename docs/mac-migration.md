# 키움 시스템 Windows → Mac 이전 가이드

키움 자동매매·레이더 시스템을 Windows PC(Task Scheduler)에서 **Mac(상시 가동)** 으로 이전한다.
대상 Mac = 기존 KIS(stocknews)가 도는 그 Mac과 **동일 기기**, 방식 = **Mac 완전 이전**.

> 복잡도 낮음: 코드는 순수 Python stdlib라 OS 독립. `publish.py` flock 락은 Mac에서 정상 작동. Mac 기본 UTF-8이라 인코딩 이슈 없음. 이 저장소는 원래 Mac cron용으로 설계됨.

## ⚠️ 두 가지 함정

1. **cron 충돌 → KIS 파괴 금지.** KIS `scripts/install_cron.sh`는 스크립트 **파일명**으로 기존 cron을 지운다. 키움도 같은 `publish.py` 파일명이라 그 설치기를 키움에서 돌리면 KIS cron까지 삭제됨. → **반드시 `scripts/install_cron_kiwoom.sh`(네임스페이스 `# KIWOOMNEWS_BEGIN/END` 블록만 관리)만 사용.** KIS `install_cron.sh`는 키움 폴더에서 절대 실행 금지.
2. **노트북 잠자기.** MacBook은 뚜껑 닫으면 잠들어 cron이 멈춘다. → `sudo pmset -c disablesleep 1`.

## .env 이전 방법 (중요)

`.env`는 gitignore돼 있어 **git clone으로 안 넘어온다**(시크릿). Mac에서 다시 만든다 — 두 방법 중 하나:

- **방법 A (권장): `setup_mac.sh`가 자동 생성.** 스크립트가 값을 물어보면 붙여넣으면 `.env`를 만든다(입력값은 화면에 안 보임, 권한 600).
- **방법 B: 파일 직접 복사.** Windows의 `kiwoom-prod\.env`를 USB/클라우드로 Mac `~/kiwoomnews/.env`에 복사.

필요한 5개 값과 출처:
| 키 | 출처 |
|----|------|
| `KIWOOM_APP_KEY` / `KIWOOM_SECRET_KEY` | `kiwwom_apikey.md` (또는 키움 OpenAPI 포털) |
| `KIWOOM_MARKET` | `UN` (고정) |
| `KV_REST_API_URL` / `KV_REST_API_READ_ONLY_TOKEN` | `KV_DATA.md` (또는 Vercel → kiwoomnews → Settings → Environment Variables → Reveal) |

## 이전 절차

```bash
# 1) 클론 (KIS ~/stocknews 와 별개 폴더)
git clone https://github.com/xenonluv/kiwoomnews.git ~/kiwoomnews
cd ~/kiwoomnews

# 2) 원클릭 셋업 (.env 생성 → 토큰 테스트 → cron 설치[자동매매 DRY])
bash scripts/setup_mac.sh

# 3) 시스템 (한 번만)
sudo systemsetup -settimezone Asia/Seoul      # 시간대 KST (cron 시각 전제)
sudo pmset -c disablesleep 1                   # 뚜껑 닫아도 안 잠 (노트북 필수)

# 4) Windows 자동매매 해제 (이중 실매수 방지 — Mac 확인 후)
#    Windows PowerShell에서:
#    Unregister-ScheduledTask -TaskName "KiwoomNews Radar Publish","KiwoomNews AutoTrade Buy KRX","KiwoomNews AutoTrade Buy NXT","KiwoomNews AutoTrade Monitor" -Confirm:$false
```

## 자동매매 실발주 켜기 (스모크 후, 회장님 결정)

기본 설치는 **자동매매 DRY**(미발주). 실계좌 발주는 이중 게이트 둘 다 필요:
1. 소액 스모크(체결 안 되는 1주 지정가 후 취소)로 주문 배관 검증.
2. `bash scripts/install_cron_kiwoom.sh --live` (cron에 `AUTOTRADE_LIVE=1` 포함).
3. 사이트에서 자동매매 토글 **ON** (KV `autotrade:enabled=1`).

## cron 스케줄 (설치되는 것)

| 시각(평일) | 작업 |
|-----------|------|
| 9~20시 10분 간격 | `publish.py` — 레이더 게시 |
| 15:18 | `autotrade_executor.py --slot krx` — KRX 종가베팅 매수 |
| 19:50 | `autotrade_executor.py --slot nxt` — NXT 종가베팅 매수 |
| 9~15시 5분 간격 | `autotrade_monitor.py` — 청산 감시 |

## 검증

```bash
python3 scripts/kiwoom_client.py 005930          # 토큰·조회 OK (Mac IP)
PYTHONUTF8=1 python3 scripts/publish.py --dry-run # 게시 미리보기 한글 정상
bash scripts/install_cron_kiwoom.sh --dry-run     # 설치될 블록 확인
crontab -l                                        # 키움 블록 + KIS 라인 둘 다 존재 확인
AUTOTRADE_FORCE_ON=1 python3 scripts/autotrade_executor.py --slot krx  # 매수판정(dry, 발주 없음)
```

## 되돌리기 / 유지보수

- 키움 cron 제거: `bash scripts/install_cron_kiwoom.sh --uninstall` (KIS 무손상).
- 코드 갱신: `git pull` 후 `bash scripts/install_cron_kiwoom.sh` 재실행.
- 로그: `/tmp/kiwoom_publish.log`, `/tmp/kiwoom_autotrade.log`.
