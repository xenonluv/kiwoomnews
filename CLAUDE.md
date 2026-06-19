# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 최종 갱신: **2026-06-18 업그레이드 기준** (상세 변경 이력 = 리포 루트 `패치0618.md`).

## Project Status

**"이벤트 매집 레이더"** — 10일 내 자명한 글로벌 증시 이벤트(FOMC·CPI·실적)를 앞두고 **큰돈이
들어와 매집·재반등이 의심되는 종목**을 자동 탐지해 웹에 게시하는 시스템. 순수 Python 파이프라인
(레이더 본체는 LLM 미사용)이 데이터를 만들고, Next.js 사이트가 Vercel에 라이브
(https://stocknews-cyan.vercel.app, `xenonluv/stocknews` push 시 자동 재배포, Root Directory=`web`).

⚠️ **환경 분리 (필독):**
- **이 WSL은 백업·코드작업 사본.** 프로덕션 cron(게시·검증·푸시)과 텔레그램 실송은 **Mac에서** 돌아간다.
  코드를 푸시한 뒤 Mac 반영은 `git pull` + (cron 변경 시) `install_cron.sh` 재실행이 필요하다.
- **KIS/네이버/텔레그램 시크릿은 Mac `.env`에만** 존재(WSL엔 없어 일부 스크립트는 no-op).

## 탐지 트랙 (2종 공존)

레이더는 두 트랙을 동시에 게시하되 **통계 격리 원칙**으로 분리한다:

1. **reaccum (재매집·재반등) — 현재 주력, 화면 노출용 실험 트랙.**
   13일내 폭발(거래대금 1천억+ · 고가 +13%) → 식음 → 현재가 MA20 위 → 투신(ivtr) 순매집 →
   **당일 재반등 10분봉** 발생. 게이트는 종가/현재 등락률 기준(`REACCUM_CHANGE_MIN=-4.0` ~ `MAX=10.0`).
   `visible_experimental=True` + **`score_raw=0`** → 화면엔 보이되 **core 적중률·가중치 튜닝엔 미반영**.
2. **fade / shakeout (급등 후 식음 / 눌림 후 재상승) — 기존 core 트랙.**
   고가 +13% 전제, fade=식음(−6~+10%) / shakeout=고점 −10%+ 눌림 후 30%+ 회복(≤+30%).
   결정론 가중합 점수가 **raw 통계에 반영**(가중치 자동 튜닝 대상).

> **통계 격리 원칙(드리프트 방지):** reaccum·forecast·strategy_sim·change_band 등 "실험·표시 전용"
> 데이터는 전부 `score_raw=0`으로 core 적중률·가중치 튜닝과 분리한다. 화면 표시 ≠ 통계 반영.

## Architecture: 파이프라인

```
[유니버스] 시장별(코스피/코스닥) 거래대금 TOP20(KIS volume-rank) + 등락률 TOP20(네이버 up 랭킹)
           합집합 → 등락률 밴드. KIS 장애 시 네이버 전수 스캔 폴백(params.universe로 구분)
   ▼
[reaccum 레지스트리] data/reaccum_seed.json + 라이브 탐지 → 13일내 폭발 종목 추적
   ▼
[정밀 판정·종목별, KIS 공식 API]
   reaccum: price_now(등락률 −4~+10%) → MA20 위 → 투신 매집 → 재반등 10분봉(reignition_bars)
   fade/shakeout: price_now(고가+13% 전제) → MA10 → 당일 분봉 스파크 → 투자자 수급
   ▼
[조건 가점] event_calendar(D-10 정적 캘린더+규칙) × theme_map(뉴스·업종 테마 매칭)
   ▼
[점수]
   reaccum: 변별 점수 = base62 + re_value(0~12)+re_body(0~6)+re_count(0~6)+flow(0~8)+explosion(0~6),
            min(95, 합) — 표시·정렬 전용(score_raw=0)
   fade/shakeout: raw 가중합(통계 반영)
   forecast: 동결 모델 "3일내 +7% 터치" 과거 실측 확률 라벨(표시 전용)
   ▼
publish.py → web/data/radar.json → 변경 시에만 git push → Vercel 재빌드(~30초)
            → 재반등 봉이면 텔레그램 알림(Mac만)
```

- **빈 레이더(수상종목 0)도 유효 상태**로 게시 ("오늘은 레이더 깨끗").
- `score_breakdown`을 JSON에 그대로 실어 웹에서 점수 해부도로 투명 공개.
- `analyzer/`는 별도 서브시스템(종가베팅 `/forecast` — `/api/predictions`). `screener.py`·`prompts/` 등은 레거시.

## Scripts 카탈로그 (`scripts/`)

| 파일 | 역할 |
|------|------|
| `kis_client.py` | **KIS 공식 API 클라이언트** (표준라이브러리만). 토큰 발급/캐시(.kis_token.json, 1일 유효, 1분 1회 발급 제한 — 쿨다운 내장), 일봉/현재가/당일분봉/투자자수급. 토큰 무효(401/EGW00121/123) 시 자동 재발급. 분봉은 **당일 봉만**(날짜 필터 = 휴장일 가드). |
| `radar.py` | 스캐너 CLI. 유니버스 = 시장별 거래대금·등락률 TOP-N 합집합(`--top-n` 기본 20, KIS 장애 시 네이버 전수 폴백). **reaccum 트랙**: `--reaccum-change-min/max`(−4/10) `--reignition-body-pct`(2.0=10분봉 몸통%) `--reignition-value-10m`(30억) `--reaccum-seed`(data/reaccum_seed.json) `--reaccum-max`(12) `--no-reaccum`/`--no-reaccum-visible` `--telegram-seed`(기본 on, 채널 보조시드)/`--no-telegram-seed`/`--telegram-channel`/`--telegram-max-age`(360분). **fade/shakeout 트랙**: `--high-pct`(13) `--chg-min/max`(−6/10) `--shake-pct`/`--shake-recover`/`--shake-chg-max` `--spark-x`(중앙값 8배) `--min-value`(700억, 정밀판정 게이트). suspect에 `pattern`·`reignition`·`reignition_bars`·`forecast` 필드. stdout JSON. 유니버스 0종목이면 exit 2. |
| `event_calendar.py` | D-10 이벤트: `data/macro_events.json`(정적, **연 1회 수동 갱신**) + 규칙(옵션만기=둘째 목, 미 고용=첫 금). |
| `theme_map.py` | 이벤트 category(금리/반도체/환율/유가/전쟁/실적/수급) ↔ 종목 뉴스·업종 정규식 매칭. |
| `publish.py` | radar → `web/data/radar.json` → 변경 시에만 commit+push. flock 락, `--dry-run`, `--max`. radar 인자 그대로 전달. 매 회차 `data/radar_history/`에 검증용 이력(raw 점수) 기록. 게시 후보 중 **재반등 봉 발생 시 `telegram_notify.notify_reignitions` 호출**(토큰 없으면 조용히 skip). |
| `telegram_notify.py` | **재반등 10분봉 알림** (봇 `@signalpyo_bot`, 표준라이브러리만). **완료된 봉만**(`_bar_complete`) · 봉 단위 디둡(`date:code:HH:MM`, `.telegram_notified.json`) · fail-safe(실패해도 publish 진행). `_load_state`는 손상 파일도 빈 상태로 안전 처리. 시크릿 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`는 **Mac `.env`에만**. |
| `radar_backtest.py` | **자가 검증·개선** (cron 17:20). 익일 일봉 대조(적중=익일종가>신호일종가) → reaccum 후보 **마감 후 AI(Kimi) 익일예측 기록**(`ai_predict`, `RADAR_AI_PREDICT=0`로 비활성, history에 `ai_pred{prob_up,direction}`) → 점수대 보정표(n≥20) → n≥30 시 가중치 자동 튜닝(±30% bounded, `data/radar_weights.json`) → **`change_band_stats`**(등락률 구간별 익일 상승확률) → **`strategy_sim_stats`**(분할매매 실현성적, 아래) → `web/data/performance.json` → `--push`. 통계는 **raw 점수만** 사용. 25일 초과 미평가 만료. |
| `track_eval.py` | **검색 종목 📌추적 일일 검증** (cron 17:30). Upstash KV(`track:watchlist`)에서 추적 코드 읽기 → 각 종목 `/api/stock/{code}`(룰 종합판정) + `/api/stock/{code}/ai`(Kimi 상승확률) 기록 → 익일 일봉 평가 → `web/data/track_performance.json`(룰 vs AI 4분면). radar performance와 **별도 파일**. 시크릿: `KV_REST_API_URL`/`KV_REST_API_READ_ONLY_TOKEN`. |
| `team1_collect.py` | 네이버 수집 유틸(랭킹/코드해석/종목뉴스/컨센서스). radar 재사용. ⚠️ 네이버 `transactionAmount`/`tradingVolume` 랭킹은 2026-06 폐지(404) — `up`/`down`만 동작. |
| `team2_relevance.py` | 뉴스 재료필터(별칭 매칭·호악재·중요도). radar 재사용. |
| `net.py` | HTTP 유틸(재시도+레이트리밋). 네이버 호출용. |
| `telegram_news.py` | **공개 텔레그램 채널(@FastStockNews) 웹 미리보기 스크래퍼** (표준라이브러리만). `t.me/s/{channel}`에서 ① 공시 글의 6자리코드(네이버링크/A코드, 공시 맥락에서만) ② 헤드라인 앞머리 종목명을 네이버 자동완성 **정확 일치**로 해석. radar.py가 재매집 **보조 시드**로 사용(랭킹 미진입 재료 종목을 한발 일찍 포착). 추출 종목은 reaccum 게이트 통과해야 노출 — 채널 맹신 안 함. fail-safe(실패해도 본작업 계속). `--no-names`로 종목명 해석 끔. |
| `screener.py`·`reaccum_backtest.py`·`reaccum_reclaim_bt.py`·`snapshot_ranks.py` 등 | 레거시·일회성 연구 스크립트. cron 제외. |

```bash
# WSL에서 (코드 점검용):
python3 scripts/radar.py > out.json            # 스캐너 단독 실행
python3 scripts/publish.py --dry-run           # 게시 미리보기 (/tmp/radar_preview.json)
python3 scripts/kis_client.py 005930           # KIS API 점검 (삼성전자, 시크릿 필요)
python3 scripts/event_calendar.py 10           # D-10 이벤트 확인
```

## 분할 전략 실측 트래커 (strategy_sim)

레이더 신호를 실제 매매했다 가정한 **실현 net 성적을 라이브 누적**(`radar_backtest.py` → `performance.json`의 `strategy_sim`).
- 가정: **20/30/50 분할 매수 + 익절 +7% / 손절 −5%(종가)**, forward 10거래일 일봉, **수수료 0.3%p 차감**.
- `_strategy_outcome` / `strategy_eval`(멱등 · age 16일 게이트 · 40일 만료) / `strategy_sim_stats`.
- `/performance` **StrategySimPanel**: 거래수·승률·net 평균·손절률·수익거래%·최악. `min_n=30` 미만은 "수집 중".
- **표시 전용 · core 통계 미반영 · 보장 아님 · 종가손절 가정** 명시.

## 데이터 소스

- **KIS 공식 API** (`openapi.koreainvestment.com:9443`, .env의 KIS_APP_KEY/SECRET):
  일봉 `FHKST03010100` / 현재가(고가·등락률·거래대금·업종) `FHKST01010100` /
  당일 1분봉 `FHKST03010200`(1콜 30봉, 역방향 페이지네이션) / 투자자 일별 수급 `FHKST01010900`.
  실전 rate ~20건/초(0.06초 간격). 거래대금 순위 `FHPST01710000`(volume-rank, `FID_INPUT_ISCD`
  0001=코스피/1001=코스닥). ⚠ 등락률 순위 `FHPST01700000`은 **정렬 코드 0~4 전부 동작 안 함(실측)**
  → 등락률 TOP20은 네이버 up 랭킹 1페이지로 대체.
- **네이버**(유니버스·뉴스): `m.stock.naver.com/api/stocks/{up|down}/{KOSPI|KOSDAQ}?page=N&pageSize=100`,
  종목뉴스 `api/news/stock/{code}`, autocomplete `ac.stock.naver.com`.
- **정적 캘린더**: `data/macro_events.json` — FOMC(확정)/CPI·금통위·삼성 잠정실적(추정).
  `estimated:true`는 추정일. **연초에 새해 일정으로 갱신 필요.**
- **Upstash KV**: 추적 watchlist(`track:watchlist`). track_eval이 읽기 토큰으로 조회.

## 게시 자동화 (cron — **Mac 프로덕션**)

`bash scripts/install_cron.sh`로 일괄 설치(idempotent). 핵심 잡:

```
1,11,21,31,41,51 9-15 * * 1-5  publish.py                 # 10분 간격, :01 오프셋(재반등 10분봉 주기와 정합)
7,22,37,52 9-15 * * 1-5        analyzer/run.py            # 종가베팅 forecast
10 17 * * 1-5                  analyzer/backtest.py --push
20 17 * * 1-5                  radar_backtest.py --push   # 익일 적중·AI예측·strategy_sim·change_band
30 17 * * 1-5                  track_eval.py --push       # 검색 추적 종목 룰 vs AI
```

- "변경 시에만 push"로 Vercel 무료 한도 내 안정. **PC가 켜져 있어야 함.**
- ⚠️ **cron(특히 publish 10분 간격)을 바꾸면 Mac에서 `install_cron.sh` 재실행 필요.**
- KRX 공휴일: 분봉 날짜 필터 덕에 스파크 0 → 수상종목 0으로 안전(stale 게시 없음).

## 공개 REST API (읽기 전용)

- `GET /api/radar` — 레이더 전체 상태 `{generated_at, market_session, events[], suspects[], params}`.
  엣지 캐시 30초. suspect에 `calibrated_prob`(raw 점수대 표본 n≥20일 때), `reignition`·`forecast` 포함.
- `/performance` — 자가 검증 대시보드. 데이터 = `web/data/performance.json`. 패널: TrendChart(적중률 추세),
  CalibrationTable(점수대 보정), WeightsPanel(가중치), AiPredictionPanel(AI 방향별 적중·Brier),
  **ChangeBandTable**(등락률 구간별 익일 상승확률), **StrategySimPanel**(분할매매 실현성적),
  **TrackPerformancePanel**(검색 추적 룰 vs AI 4분면, 데이터 = `track_performance.json`),
  ThemeStatsTable·SparkFlowMatrix. 모든 패널 `min_n` 게이트.
- `GET /api/predictions` — 종가베팅(/forecast)용. analyzer/ 서브시스템이 생성.
- `GET /api/track` — 추적 watchlist 등록/조회(KV).
- `GET /api/stock/{code}` — **온디맨드 종목 분석 리포트**(룰베이스, LLM 미사용). 네이버 공개 API 7종
  병렬 호출 → 주가·기술지표·수급·분봉 스파크·재무·재료뉴스·이벤트 민감도·종합판정. 엣지 캐시 180초.
  시크릿 불필요(KIS 미사용 — Vercel 무시크릿 유지).
  ⚠ 분봉 소스 fchart(`sise.nhn?timeframe=minute`) 함정: 시/고/저 "null"(종가만 유효), **거래량은
  당일 누적값**(분당=차분 필요), ~6세션치 응답(KST 당일 필터 필수), 08:30~ 장전 봉 포함.
  스파크 = `web/lib/stock/sparks.ts`(radar.py 1:1 포팅, **산식 변경 시 동기화**).
  `GET /api/stock/search?q=` — 자동완성 프록시(ac.stock.naver.com, CSP 때문에 경유 필수).
- `GET /api/stock/{code}/ai` — **AI(LLM) 심층 분석** (Moonshot `kimi-k2.6`, LLM 사용처는 이 `/ai`와 아래 `/ask` 둘뿐).
  룰베이스 리포트 전체를 직렬화해 Kimi에 전달 → **익일 상승 확률 `prob_up`(0~100)** 추정.
  방향(상승/하락/관망)은 코드가 파생(≥54/≤42 — **임계값 프롬프트 노출 금지**, 재앵커링 방지).
  `MOONSHOT_SAMPLES`(기본 3) 병렬 호출 → **중앙값 합의**(self-consistency). 버튼 클릭 시에만 호출.
  성공 30분/에러 60초 CDN 캐시 + 쿼리스트링 차단 + in-flight 디둡.
  ⚠ kimi-k2.6 함정: temperature 지정 시 400(1만 허용) / 확률 0~1로 줄 때 정규화 /
  **reasoning(기본값)은 15~120초+ → Vercel 타임아웃** → `thinking:{type:"disabled"}`로 5~20초.
  `MOONSHOT_THINKING=enabled`로 깊은 추론(이때 `maxDuration=300` Fluid Compute 필요).
  시크릿: `MOONSHOT_API_KEY`(+BASE_URL/MODEL) — `web/.env.local` + Vercel.
- `POST /api/stock/{code}/ask` — **AI 자유질문(찌라시 RAG + 근거 종합추론)**. 사용자 질문을 그 종목의
  실제 데이터 + 수집 글(뉴스·토론방·텔레그램)을 근거로 Kimi가 답함(`/ai`와 별개 엔드포인트).
  body `{question}`(2~300자) → `{answerable, answer, facts[], rumors[], calcUnverified, droppedCount,
  caveat, sourceCounts}`. 질문마다 답이 달라 **CDN 캐시 불가**(`force-dynamic`·`no-store`·POST),
  `maxDuration=300`. **answer는 수집 자료를 종합한 추론·결론 허용**(자료 밖 새 사실 날조는 금지) —
  대신 **근거의 추적성으로 신뢰 담보**: ① 프롬프트(추론은 자료에서 출발·근거를 evidence에 남길 것·
  인용 시 원문 발췌 필수) ② 사후 대조 — 모델이 댄 `quote`가 수집 원문에 substring 존재할 때만 채택,
  데이터 근거 4자리+ 숫자는 실제 데이터에 있어야 채택, 미통과분 자동 삭제(`droppedCount`).
  **facts[]/rumors[]의 각 항목은 `url`(원문 링크)을 실어 사용자가 직접 검증**(뉴스=`n.news.naver.com`,
  토론방=`board_read.naver`, 텔레그램=`t.me/{채널}/{id}`; 데이터 근거는 url 없음). 찌라시(토론방·
  텔레그램)=**미확인 루머** / 데이터·뉴스=사실 분리 표시. answer 속 계산수치·% 백스톱(`calcUnverified`).
  엔진: `lib/stock/ask.ts`(오케스트레이터) + `lib/stock/rumors.ts`(토론방·텔레그램 수집, best-effort)
  + `ai.ts`의 `callKimiJson`/`serializeForPrompt` 공유. UI = `components/stock/AskQuestionCard.tsx`
  (`StockReportView`에서 `!tradeStop`일 때 마운트), 호출은 `services/stock.client.ts`의 `askQuestion`.
  시크릿: `MOONSHOT_API_KEY`(KIS 미사용 — 무시크릿 유지, 네이버 공개 HTML만 추가).
- 흐름: `web/data/radar.json` → `lib/radar/repository.ts`(SSOT) → `app/page.tsx`(SSG) + `app/api/radar`.
- 프론트 폴링은 `services/radar.client.ts` 경유만(컴포넌트 직접 fetch 금지).

## 프론트엔드 (web/)

**Next.js(App Router) + TS + Tailwind + shadcn/ui + Pretendard**. 다크 금융 대시보드.
- ⚠️ **한국 색 관례 — 상승=빨강(`--up`), 하락=파랑(`--down`)** (미국과 반대). 토큰 SSOT = `web/app/globals.css`.
- **프론트 게이트**: `components/auth/PasswordGate.tsx`(쿠키 기반 화면 가리개, `layout.tsx`에 적용) + `noindex` 메타.
  **실보안 아님**(돈 거래 없음·미마케팅 합의 전제), 단순 개인용 가리개.
- 레이더 UI: `components/radar/` — EventStrip·ThemeStrip(칩 필터), SuspectCard(페이드/재반등 바+스파크
  타임라인+점수 해부도+수급+forecast 라벨), LiveRadar(60초 폴링), SuspicionGauge, ScoreBreakdownBars.
- 성과 UI: `components/performance/` (위 `/performance` 패널 목록 참조).
- 종목 분석 UI: 메인 검색박스(`components/stock/SearchBox`) → `/stock/[code]`. 엔진 = `web/lib/stock/`
  — `indicators.ts`(analyzer/indicators.py 1:1 포팅), `news-score.ts`, `theme-match.ts`, `scoring.ts`,
  `report.ts`(오케스트레이터, graceful degradation). **파이썬 산식 변경 시 동기화 필요.**
  KRX 시장경보: 네이버 basic `marketAlertType`(01주의/02경고/03위험)·`isManagement`·`tradeStopType`(HALTED)
  — 경고/위험·관리종목은 감점 + 매수 판정 금지, 헤더 배지 노출.
- 빈 상태("오늘은 레이더 깨끗")가 제품 사양. 면책 문구("매수 추천 아님") 유지.
- forecast·strategy_sim·change_band는 **확률·과거 통계**지 보장이 아님(6개월 약세 단일 레짐 표본 한계).
- 빌드 검증: `cd web && npm run build` (**WSL + nvm Node 20+(현재 24)만** — Windows npm은 UNC에서 깨짐).

## ⚠️ 환경 함정 (WSL/Windows 분리)

- **Python 스크립트**: WSL에서 실행. system python3, 표준라이브러리만.
- **Next.js**: WSL + nvm Node 20만. `nvm use 20 && npm ...`.
- **인라인 파이썬 따옴표**: 중첩 따옴표 깨짐 → 스크립트 파일로 작성해 실행.
- **WSL은 백업 사본** — 프로덕션 cron·푸시·텔레그램 실송은 Mac. WSL엔 시크릿 없음.

## Security

- `.env`(Mac): NAVER_CLIENT_ID/SECRET + KIS_APP_KEY/SECRET/CANO + **TELEGRAM_BOT_TOKEN/CHAT_ID** + KV_REST_API_* (gitignore).
- `web/.env.local`: **MOONSHOT_API_KEY/BASE_URL/MODEL** + KV_REST_API_* (gitignore).
- `.kis_token.json`·`.telegram_notified.json`·`open_api/`·`apikey.md`·`kimiapi.md`·`kis_devlp.yaml` 모두 gitignore.
- Vercel 시크릿은 **MOONSHOT_* + KV_REST_API_*** (서버 온리). KIS/네이버/텔레그램 키는 로컬 파이프라인 전용으로 Vercel 불필요.
