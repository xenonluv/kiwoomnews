# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

**2026-06 전면 리뉴얼 완료.** 기존 "눌림목 스크리너 + 멀티에이전트 시그널" 파이프라인을 폐기하고
**"이벤트 매집 레이더"** 로 교체됨. Next.js 사이트가 Vercel에 라이브
(https://stocknews-cyan.vercel.app, `xenonluv/stocknews` push 시 자동 재배포, Root Directory=`web`).

## Goal (목적.md)

10일 이내 자명하게 발생할 글로벌 증시 이벤트(FOMC·CPI·실적 등)를 앞두고, **당일 큰돈이 들어와
급등 후 식은(매집 의심) 종목**을 자동 탐지해 웹에 게시:

1. D-10 이벤트 캘린더 (실적·글로벌 매크로)
2. 당일 분봉 스파크(거래량 폭발 분봉) 발생 종목
3. 당일 거래대금 ≥ 700억 + 고가 등락률 ≥ +13% 찍고 하락 중
4. 현재가 ≥ 일봉 10일선
5. 2~4 만족 + 이벤트/뉴스 민감 종목 (테마 매칭 가점)
6. 현재 등락률 −6% ~ +10%

## Architecture: 레이더 파이프라인 (순수 Python, LLM 미사용)

```
[유니버스] 네이버 up/down 랭킹 전수 스캔 (등락률 밴드 −6~10% + 거래대금 ≥700억, 정확값)
   ▼
[정밀 판정·종목별, KIS 공식 API] price_now(고가+13% 후 하락) → 일봉 MA10 → 당일 분봉 스파크
   ▼                              → 투자자 수급(외인/기관 순매수, 가점)
[조건1·5] event_calendar(D-10 정적 캘린더+규칙) × theme_map(뉴스·업종 테마 매칭) 가점
   ▼
수상함 점수(0~100, 결정론 가중합: base30+스파크15+페이드15+수급15+이벤트15+MA10여유10)
   ▼
publish.py → web/data/radar.json → 변경 시에만 git push → Vercel 재빌드(~30초)
```

- **빈 레이더(수상종목 0)도 유효 상태**로 게시 ("오늘은 레이더 깨끗").
- `score_breakdown`을 JSON에 그대로 실어 웹에서 점수 해부도로 투명 공개.
- 기존 prompts/(멀티에이전트)·screener.py는 **레거시**(수동 참고용). analyzer/는 별도 서브시스템(종가베팅 /forecast 데이터).

## Scripts 카탈로그 (`scripts/`)

| 파일 | 역할 |
|------|------|
| `kis_client.py` | **KIS 공식 API 클라이언트** (표준라이브러리만). 토큰 발급/캐시(.kis_token.json, 1일 유효, 1분 1회 발급 제한 — 쿨다운 내장), 일봉/현재가/당일분봉/투자자수급. 토큰 무효(401/EGW00121/123) 시 자동 재발급. 분봉은 **당일 봉만**(날짜 필터 = 휴장일 가드). |
| `radar.py` | 6조건 스캐너 CLI. `--min-value`(원, 기본 700억) `--high-pct`(13) `--chg-min/max`(−6/10) `--spark-x`(분봉 거래량 중앙값 8배) `--spark-pct`(0.8) `--names`(watchlist). stdout JSON. 유니버스 0종목이면 exit 2 (수집 장애 구분). |
| `event_calendar.py` | D-10 이벤트: `data/macro_events.json`(정적, **연 1회 수동 갱신**) + 규칙 생성(옵션만기=둘째 목, 미 고용=첫 금). |
| `theme_map.py` | 이벤트 category(금리/반도체/환율/유가/전쟁/실적/수급) ↔ 종목 뉴스·업종 정규식 매칭. |
| `publish.py` | radar → `web/data/radar.json` → 변경 시에만 commit+push. flock 락, `--dry-run`(/tmp/radar_preview.json), `--max`(기본 12). radar 인자 그대로 전달. 매 회차 `data/radar_history/`에 검증용 이력 기록(raw 점수). |
| `radar_backtest.py` | **자가 검증·개선** (cron 17:20): 익일 일봉 대조(적중=익일종가>신호일종가, entry는 일봉 종가 재정합) → 점수대 보정표(n≥20) → 표본 n≥30 시 가중치 자동 튜닝(±30% bounded, `data/radar_weights.json`) → `web/data/performance.json`(대시보드) → `--push`. 통계는 **raw(가중치 적용 전) 점수**만 사용(드리프트 방지). 25일 초과 미평가는 만료 처리. |
| `team1_collect.py` | 네이버 수집 유틸 (랭킹/코드해석/종목뉴스/컨센서스). radar가 재사용. ⚠️ 네이버 `transactionAmount`/`tradingVolume` 랭킹은 **2026-06 폐지됨(404)** — `up`/`down`만 동작. |
| `team2_relevance.py` | 뉴스 재료필터(별칭 매칭·호악재·중요도). radar가 재사용. |
| `net.py` | HTTP 유틸 (재시도+레이트리밋). 네이버 호출용. |
| `screener.py` 등 기타 | 레거시 (구 눌림목 스크리너). cron에서 제외됨. |

```bash
# WSL에서:
python3 scripts/radar.py > out.json            # 스캐너 단독 실행
python3 scripts/publish.py --dry-run           # 게시 미리보기
python3 scripts/kis_client.py 005930           # KIS API 점검 (삼성전자)
python3 scripts/event_calendar.py 10           # D-10 이벤트 확인
```

## 데이터 소스

- **KIS 공식 API** (`openapi.koreainvestment.com:9443`, .env의 KIS_APP_KEY/SECRET):
  일봉 `FHKST03010100` / 현재가(고가·등락률·거래대금·업종) `FHKST01010100` /
  당일 1분봉 `FHKST03010200`(1콜 30봉, 역방향 페이지네이션) / 투자자 일별 수급 `FHKST01010900`.
  실전 rate ~20건/초(클라이언트 0.06초 간격). **랭킹 API는 30행 한정**이라 유니버스 구성엔 부적합.
- **네이버** (유니버스·뉴스): `m.stock.naver.com/api/stocks/{up|down}/{KOSPI|KOSDAQ}?page=N&pageSize=100`
  (행에 등락률·누적거래대금 포함), 종목뉴스 `api/news/stock/{code}`, autocomplete `ac.stock.naver.com`.
- **정적 캘린더**: `data/macro_events.json` — FOMC(확정)/CPI(추정)/금통위(추정)/삼성 잠정실적(추정).
  `estimated:true`는 추정일. **연초에 새해 일정으로 갱신 필요.**

## 게시 자동화 (cron, WSL)

```
*/15 9-15 * * 1-5  cd /home/xenonluv/stocknews && /usr/bin/python3 scripts/publish.py >> /tmp/publish.log 2>&1
45 15 * * 1-5      cd /home/xenonluv/stocknews && /usr/bin/python3 scripts/publish.py >> /tmp/publish.log 2>&1
20 17 * * 1-5      cd /home/xenonluv/stocknews && /usr/bin/python3 scripts/radar_backtest.py --push >> /tmp/radar_backtest.log 2>&1
```
(일괄 설치는 `bash scripts/install_cron.sh` — analyzer cron 포함, idempotent)
- 15:45 회차 = 장 마감 확정 데이터 반영. "변경 시에만 push"로 Vercel 무료 한도 내 안정.
- ⚠️ PC가 켜져 있어야 함. WSL 재부팅 후 `sudo service cron start` 필요할 수 있음.
- KRX 공휴일: 분봉 날짜 필터 덕에 스파크 0 → 수상종목 0으로 안전 (stale 게시 없음).

## 공개 REST API (읽기 전용)

- `GET /api/radar` — 레이더 전체 상태 `{generated_at, market_session, events[], suspects[], params}`. 엣지 캐시 30초. suspect에 `calibrated_prob`(실측 적중률, raw 점수대 표본 n≥20일 때만) 포함.
- `/performance` 페이지 — 자가 검증 대시보드 (누적 적중률 추세 SVG 차트·점수대 보정표·가중치 패널). 데이터 = `web/data/performance.json`.
- `GET /api/predictions` — 종가베팅(/forecast)용. analyzer/ 서브시스템이 생성.
- `GET /api/stock/{code}` — **온디맨드 종목 분석 리포트** (룰베이스, LLM 미사용). 요청 시
  네이버 공개(무인증) API 6종을 병렬 호출해 주가현황·기술지표·수급·재무·재료뉴스·이벤트
  민감도·종합판정을 생성. 엣지 캐시 180초. 시크릿 불필요(KIS 미사용 — Vercel 무시크릿 유지).
  `GET /api/stock/search?q=` — 자동완성 프록시(ac.stock.naver.com, CSP 때문에 경유 필수).
- 흐름: `web/data/radar.json` → `lib/radar/repository.ts`(SSOT) → `app/page.tsx`(SSG) + `app/api/radar`.
- 프론트 폴링은 `services/radar.client.ts` 경유만 (컴포넌트 직접 fetch 금지).

## 프론트엔드 (web/)

**Next.js(App Router) + TS + Tailwind + shadcn/ui + Pretendard**. 다크 금융 대시보드.
- ⚠️ **한국 색 관례 — 상승=빨강(`--up`), 하락=파랑(`--down`)** (미국과 반대). 토큰 SSOT = `web/app/globals.css`.
- 레이더 UI: `components/radar/` — EventStrip(D-day 칩, 클릭 시 민감 종목 필터), SuspectCard(페이드 바+스파크 타임라인+점수 해부도+수급), LiveRadar(60초 폴링), SuspicionGauge.
- 종목 분석 UI: 메인 검색박스(`components/stock/SearchBox`, 자동완성+키보드 내비) →
  `/stock/[code]` 동적 페이지. 분석 엔진 = `web/lib/stock/` — `indicators.ts`(analyzer/indicators.py
  1:1 포팅, **파이썬 쪽 산식 변경 시 동기화 필요**), `news-score.ts`(team2_relevance 포팅),
  `theme-match.ts`(theme_map 포팅), `scoring.ts`(confluence+컨센서스·수급·이벤트 확장 판정),
  `report.ts`(오케스트레이터 — 섹션별 graceful degradation + warnings).
  KRX 시장경보 반영: 네이버 basic의 `marketAlertType`(01 주의/02 경고/03 위험, 미지정 시 필드
  부재)·`isManagement`(관리종목)·`tradeStopType`(HALTED=정지) — 경고/위험·관리종목은 감점
  + 매수 계열 판정 금지, 헤더 배지로 노출 (02·HALTED·isManagement는 실종목 전수 실증됨).
- 빈 상태("오늘은 레이더 깨끗")가 제품 사양. 면책 문구("매수 추천 아님") 유지.
- 빌드 검증: `cd web && npm run build` (**WSL + nvm Node 20만** — Windows npm은 UNC에서 깨짐).

## ⚠️ 환경 함정 (필독 — WSL/Windows 분리)

- **Python 스크립트**: WSL에서 `wsl.exe -e bash -lc '...'`. system python3(3.10), 표준라이브러리만 사용.
- **Next.js**: WSL + nvm Node 20만. `nvm use 20 && npm ...`.
- **인라인 파이썬 따옴표**: `wsl -lc '...'` 안 중첩 따옴표 깨짐 → 스크립트 파일로 작성해 실행.
- Codex CLI(레거시 에이전트용)는 Windows Git Bash에서만.

## Security

- `.env`에만 시크릿: NAVER_CLIENT_ID/SECRET + **KIS_APP_KEY/SECRET/CANO** (gitignore 처리).
- `.kis_token.json`(토큰 캐시), `open_api/`(KIS 공식 샘플 레포, 벤더 코드), `apikey.md`, `kis_devlp.yaml` 모두 gitignore.
- Vercel엔 시크릿 불필요 (웹은 정적 JSON만 사용, API 키는 로컬 파이프라인 전용).
