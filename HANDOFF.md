# HANDOFF — 키움 레이더·자동매매 (세션 인계 문서)

> 새 세션(특히 Mac)에서 이 프로젝트를 이어받을 때 **이 파일 하나만 읽으면** 상태를 파악하도록 정리.
> 마지막 갱신: 2026-07-06. 상세 아키텍처는 `CLAUDE.md`(KIS 시절 SSOT, 키움 델타는 아래), 이전 절차는 `docs/mac-migration.md`.

## 이 프로젝트가 뭔가
KIS(한국투자증권) 기반 "이벤트 매집 레이더"를 **키움증권 REST API 버전**으로 컨버전한 것. 저장소 `github.com/xenonluv/kiwoomnews`, 사이트 https://kiwoomnews-five.vercel.app (비번 env `NEXT_PUBLIC_GATE_PASSWORD`, 현재 `3335`). 기존 KIS 시스템(`stocknews`, Mac)과 **완전 분리** — 다른 저장소·키·사이트·머신.

## ⚠️ 최신 세션 인계 (2026-07-06 오후) — 먼저 읽을 것
**이 Mac이 프로덕션.** 오늘 오후 세션에서 한 일 + 열린 이슈 + 반드시 피할 함정:

### 오늘 완료 (전부 커밋·푸시됨)
- **stocknews(구 KIS) 은퇴** — crontab에서 stocknews 잡 전부 주석 처리. 이제 **키움만** 텔레그램 발송(두 시스템이 같은 봇/챗 공유해 섞이던 문제 해소). 백업: `~/crontab_backup_*.txt`.
- **크리티컬 픽스 2건**(적대검증 통과): ① 웹 OFF 가림(route.ts GET catch가 KV 읽기실패를 configured:false로) ② NXT 매도 접수-오인(`_sell`이 실체결 수량 반환·NXT는 보유감소로 확인, KRX 무회귀).
- **흔들기 튜닝 파이프라인 복구/확장**: `publish.record_history`에 흔들기 변별필드(turnover_2d/peak_dd/tier/band) 추가 · `radar_backtest` 키움 브로커 스위치(전엔 kis_client 하드코딩이라 익일채점 불능) · `next_high_pct`(익일 고가폭) + 자가치유 백필 · `shakeout_bands`(회전2d/낙폭/티어 밴드) + **결합(AND) 코호트**(적정회전 90~140 AND 깊은눌림 -30~-45) · `scripts/shakeout_backfill.py`(과거 흔들기 소급 재구성 → `data/shakeout_backfill.json`).
- **크론 추가**: 17:20 `radar_backtest --push`, 17:25 `autotrade_stats --push` (RADAR_BROKER=kiwoom).

### Mac 프로덕션 cron 현황 (키움 6잡, 전부 실발주 게이트 `AUTOTRADE_LIVE=1`)
`publish.py`(9~20시 10분) · `autotrade_executor --slot krx`(15:18) · `--slot nxt`(19:50) · `autotrade_monitor`(8~15시 1분) · `radar_backtest --push`(17:20) · `autotrade_stats --push`(17:25). 실발주는 여전히 **AUTOTRADE_LIVE=1 AND KV `autotrade:enabled`=1** 이중 게이트.

### ⏳ 열린 이슈 (회장님 결정 대기)
- **폭발(/forecast)·youtong 종목이 `radar_history`에 미기록 → 백테스트가 익일 성과 미추적.** `record_history`는 `suspects`(재매집/흔들기)만 저장. 회장님이 실제 보시는 폭발/youtong 종목(예: 금요일 419050·진흥)의 성과가 통계에 안 잡힘. **이걸 추적에 추가할지 결정 대기.**

### ⚠️ 반드시 피할 함정
- **종목명 충돌**: `삼기`(122350, 로봇/다이캐스팅) ≠ `삼기에너지솔루션즈`(419050, 2차전지). 회장님이 "삼기"라 하시면 **어느 코드인지 먼저 확인**(내가 오늘 이걸 혼동해 크게 헛다리 짚음).
- **페이지 구분**: 자동매매 매수 = **`suspects`(메인 레이더)만**. `/forecast`(폭발)·`/youtong`(곧폭발)은 **매수 대상 아님**(표시·감시용). 회장님 질문이 어느 페이지인지 먼저 확인.

---

## 현재 상태 (베이스 — 위 최신 세션이 우선)
- ✅ **Part A** — `scripts/kiwoom_client.py`가 `kis_client.py`의 **드롭인 대체**(동일 시그니처). `radar.py`는 `RADAR_BROKER` env 토글(기본 kiwoom, `=kis`로 복귀). 실측 검증됨.
- ✅ **Part B** — 웹 포크·리브랜딩(StockNews→KiwoomNews) → Vercel 라이브. 실 키움 데이터 자동 게시 중.
- ✅ **Part C** — 자동매매(주문·실행기·모니터·웹토글) 빌드·검증 완료. Mac cron 실발주 게이트 걸림(위 참조).
- ✅ 독립 운영 — **Mac 프로덕션 이전 완료**(cron 가동 중). Windows Task Scheduler는 정리 대상.
- ⏳ 남음: (1) 자동매매 실발주 최종 활성화(소액 스모크 후) (2) 위 '열린 이슈'(폭발/youtong 추적) 결정 (3) 선택: agent_alpha 스왑.

## 아키텍처 (키움 델타)
- **파이프라인(Python stdlib)**: `radar.py`(네이버 up 랭킹 스캔 + 키움 검증) → `publish.py` → `web/data/radar.json` 갱신 → 변경 시 git push → Vercel 재빌드. 스캔 소스=네이버(키움 무관).
- **키움 TR 매핑**: 일봉 `ka10081` / 현재가 `ka10001` / 분봉 `ka10080` / 종목별투자자 `ka10059` / 주문 `kt10000`(매수)·`kt10001`(매도) `POST /api/dostk/ordr`.
- **시장구분** = 종목코드 접미사: 없음=KRX(가격 기준), `_AL`=통합/SOR(거래대금·거래량), `_NX`=NXT. `_overlay_money`로 J가격+통합돈 병합.
- **웹**: Next.js(App Router), KIS/키움 비의존(네이버 공개 API). `/api/radar`가 radar.json 서빙. 자동매매 토글 `/api/autotrade` + `components/radar/AutoTradeToggle.tsx`(LiveRadar에 `suspects[0]` 대상 마운트).

## 자동매매 규칙 (회장님 지시 — 정확히 이대로)
- **대상**: 레이더 **메인 1위 = `suspects[0]`** (youtong/forecast/alpha 서브페이지 아님).
- **계좌/규모**: 실전 실계좌, **고정 100만원/일, 하루 1회**.
- **매수**: 15:18 **KRX 시장가** / 단 종목이 **NXT 거래가능이면 19:50 NXT 지정가(매도 5호가 위 = 시장가 효과)**.
- **청산**: **-5% 전량손절** / **+7% 50% 익절** / 잔량 **+11% 익절** / 1차 익절 후 잔량이 진입가 근처(≤+0.5%)로 재하락하면 **본전 매도**.
- **안전필터(자동 제외)**: 빈 레이더, `change_basis=="NXT"`(야간가 기준), `alert_now in (경고,위험)`.

## 실발주 이중 안전장치 (둘 다여야 실제 주문)
1. **웹 토글** KV `autotrade:enabled=1` (사이트에서 ON).
2. **env `AUTOTRADE_LIVE=1`** (Windows: `win_autotrade.ps1` 주석 / Mac: `install_cron_kiwoom.sh --live`).
- 테스트훅 `AUTOTRADE_FORCE_ON=1`(KV 없이 ON 취급, 실발주는 여전히 LIVE 필요). 기본은 **DRY(미발주 로그만)**.

## 핵심 파일
| 파일 | 역할 |
|------|------|
| `scripts/kiwoom_client.py` | 키움 조회 클라이언트(드롭인) |
| `scripts/kiwoom_trade.py` | 주문(kt10000/kt10001) — dry+LIVE 이중 게이트, 재시도 없음 |
| `scripts/autotrade_common.py` | KV토글·포지션(fail-closed 로드/저장)·안전필터·상수 |
| `scripts/autotrade_executor.py` | 15:18/19:50 매수 (`--slot krx|nxt`) |
| `scripts/autotrade_monitor.py` | 청산 감시(매도 직후 즉시 저장 — 이중매도 방지) |
| `scripts/publish.py` | 레이더 게시(radar.py 호출→radar.json→push) |
| `scripts/install_cron_kiwoom.sh` | Mac 네임스페이스 cron(`--live`/`--dry-run`/`--uninstall`) |
| `scripts/setup_mac.sh` | Mac 원클릭 셋업(.env 프롬프트→토큰테스트→cron) |
| `scripts/win_publish.ps1`·`win_autotrade.ps1` | Windows Task Scheduler 래퍼 |
| `web/app/api/autotrade/route.ts` | 토글 GET/POST(KV) |
| `docs/mac-migration.md` | Windows→Mac 이전 절차 |

## 시크릿 위치 (값은 저장소에 없음 — .env·gitignore)
- 키움 키: `KIWOOM_APP_KEY`/`KIWOOM_SECRET_KEY` (Windows 부모폴더 `kiwwom_apikey.md`, 또는 키움 포털).
- KV: `KV_REST_API_URL`/`KV_REST_API_READ_ONLY_TOKEN` (`KV_DATA.md`, 또는 Vercel Reveal). 웹 쓰기용 `KV_REST_API_TOKEN`은 Vercel에만.
- `.env`는 각 머신에서 생성(Mac은 `setup_mac.sh`가 프롬프트로 생성).

## 운영 현황 & 스케줄
- **현재(Windows)**: Task Scheduler 4작업 — "KiwoomNews Radar Publish"(10분), "…Buy KRX"(15:18), "…Buy NXT"(19:50), "…Monitor"(5분). 자동매매는 DRY.
- **Mac 이전 후**: cron 네임스페이스 블록(`# KIWOOMNEWS_BEGIN/END`)에 동일 스케줄. ⚠ 이전 후 **Windows 작업 4개 해제 필수**(이중매매 방지). 자동매매는 **한 기기에서만** LIVE.

## 실발주 켜기 체크리스트 (실계좌 — 회장님 결정)
1. Mac 이전 완료·정상 게시 확인.
2. 소액 스모크(체결 안 되는 1주 지정가 후 취소)로 주문 배관 검증.
3. `bash scripts/install_cron_kiwoom.sh --live` (Mac) — `AUTOTRADE_LIVE=1` cron 반영.
4. 사이트에서 자동매매 토글 **ON**.
5. Windows 자동매매 작업 해제 확인(이중매매 0).

## 최근 코드리뷰(적대적) 반영
- `load_positions` fail-open → **fail-closed+재시도**(파일 읽기 실패 시 매수 중단, 중복 실매수 차단).
- 모니터 배치 저장 → **매도 직후 즉시 저장**(이중 매도 차단).
