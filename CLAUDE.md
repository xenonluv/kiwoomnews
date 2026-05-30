# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

**Active development.** A Next.js publishing site is **live on Vercel** (https://stocknews-cyan.vercel.app, auto-deploy on push to `xenonluv/stocknews`), and a Python data/analysis pipeline (collection → relevance filter → chart analysis → screener) runs locally against Naver. Agent prompts for the multi-agent decision chain live in `prompts/`.

What exists:
- `목적.md` — original spec (Korean): system goal + multi-agent pipeline with per-agent prompts and JSON contracts.
- `prompts/` — 10 agent system prompts (팀원1~6 + 팀장 + CEO + 디자이너 + 차트분석).
- `schemas/` — JSON output schemas for Codex agents (`--output-schema`).
- `scripts/` — Python pipeline: data collection, 팀원2 relevance filter, chart context, screener, Codex runner.
- `web/` — Next.js site + public REST API (`/api/signals`), deployed.
- `docs/` — design-system + QA notes.
- `.env` — Naver API keys (gitignored). `.bkit/` — bkit plugin state (gitignored).

## Goal

A **Korean stock-market pre-trade intelligence site + REST API**: collect domestic news/rankings, score material relevance, read the technical chart, fuse into a rise-probability, risk-check, and publish CEO-approved trade signals to the web + API.

## Architecture: Multi-Agent Pipeline (stock-centric)

Strict **hand-off chain**; each stage consumes the prior stage's structured output. Treat the JSON shapes as **API contracts** — changing a field is a breaking change downstream.

```
팀원1 수집 (Python/Claude) ─ 거래대금·상승률 상위 랭킹 + 종목별 뉴스/공시 + 컨센서스 + 차트지표
   ▼
팀원2 재료 (Claude/자동) ─ 시황·타종목 노이즈 제거(별칭매칭) → 호재/악재 + 중요도(1~10)
   ▼
차트분석 팀원 (Codex) ─ 기술적 지표 해석 → direction(상승/하락/중립) · phase(저점/눌림목/과열/박스/분석불가) · confidence
   ▼
팀원3 융합 (Codex) ─ 재료 + 차트판정 + 컨센서스 → probability_of_rise + trading_strategy
   ▼
팀원4 (Codex) ─ 리스크 교차검증  ──REJECT──▶ 팀원3 재검수(retry loop)
   ▼
팀장 (Claude) ─ 최종 검수 + 마크다운 브리핑
   ▼
CEO (Codex) ─ 승인 게이트 (APPROVED만 통과)  {status, target_stock, signal_probability, position_type, headline, published_at}
   ▼
디자이너 (Claude) ─ 다크 금융 대시보드 디자인 (토큰 + shadcn/ui 컴포넌트)
   ▼
팀원5 (Claude) ─ 디자인에 승인 데이터 바인딩 → 웹 게시
   ▼
팀원6 (Codex) ─ 외부 공개 읽기전용 REST API 발행 (GET /api/signals[/{post_id}])
```

- **모델 분담**: Claude = 팀원1·2·팀장·디자이너·팀원5 / Codex = 차트분석·팀원3·팀원4·CEO·팀원6.
- **차트분석 팀원(신규, `prompts/10`)**: 팀원3에서 차트 해석을 분리. LLM은 차트를 "보지" 못하므로 **결정론적 지표(`team3_price_context.py`)를 입력**받아 해석만 한다.
- **팀원3 = 융합 역할**(종목매칭이 아니라 재료+차트+컨센서스 → 확률).
- 게시는 **CEO 승인 게이트 통과분만** 디자이너→팀원5→팀원6으로 흐른다. 팀원6 API는 **외부 읽기 전용**.

## Screener Engine (`scripts/screener.py`)

"오늘 오를 종목 예측기"가 아니라 **안전 셋업 스크리너 + 리스크 필터**로 포지셔닝. 3조건 AND:

- **A 이력**: 최근 5거래일 중 하루라도 거래량 급증(기본 ≥2배) + 강한 상승(기본 ≥5%), **AND 최근 5일 일봉 거래대금 최대치 ≥ 500억**(잡주 제외 게이트, `MIN_TRADING_VALUE`). 거래대금은 종가×거래량 근사. (정확화는 `snapshot_ranks.py` 일별 누적)
- **B 재료**: `team2_relevance.py` 자동필터 통과 뉴스 ≥ N건.
- **C 차트**: 3분봉 MA60 ≥ MA120(정배열) + 최근 골든크로스 발생 + 이격도 작음(갓 교차). 3분봉은 fchart 멀티데이 1분봉을 3분 합성.

임계값 CLI 튜닝: `--vol-x --gain --min-value --news-min --gc-window --disp-max --topn --names`.
- `--min-value`는 원 단위(기본 50000000000 = 500억). 예: 1000억 → `--min-value 100000000000`.
- `--names 종목명...`: 랭킹 유니버스에 watchlist 종목 강제 포함(랭킹 top-N 밖이어도 평가). **랭킹 top-N엔 초대형주에 밀려 누락되는 중형주(예: 한온시스템)는 반드시 `--names`로 넣어야 함.**

결과는 업종(섹터)별 그룹핑, `screener_report.py`로 뉴스 링크 포함 마크다운 리포트.

```bash
# WSL 터미널에서 (python3 + 네트워크 필요)
python3 scripts/screener.py --vol-x 1.5 --gain 3.0 --min-value 50000000000 \
  --news-min 2 --gc-window 40 --disp-max 2.0 --names 삼성전자 한온시스템 > out.json
python3 scripts/screener_report.py out.json
```
> ⚠️ C(3분봉 GC)는 **최신 장 세션 기준**(주말이면 직전 거래일). 실전은 장중 실행.

## 게시 자동화 (`scripts/publish.py` + cron)

스크리너 결과를 사이트에 자동 게시. **순수 Python(LLM 미사용)** 으로 cron 안정성 확보.

- 흐름: `스크리너 → 결정론 스코어 → web/data/signals.json → 변경 시에만 git commit+push → Vercel 자동 재빌드(~30초)`.
- **2등급(tier)**: `signal`(A+B+C 통과) / `candidate`(A+B 통과, C 대기). 사이트가 "📌 시그널 / 👀 후보 종목군" 2섹션으로 표시.
- 결정론 스코어(`signal_probability`): 재료 중요도 + 일봉 국면(저점/눌림목 가점·과열 감점) + 3분봉 타이밍 + 호악재. (자동 루프는 Codex 미사용 — Codex 팀원3 심층분석은 수동/별도)
- `position_type`은 일봉 국면(`MarketStatus`: 저점/눌림목/과다상승/분석불가)을 정직 표기. 사이트는 "매수 추천 아님(참고용)".
- **`--names`를 반드시 스크리너로 전달**(publish가 forward). 미전달 시 한온시스템 등 누락됨(과거 버그).

```bash
# 1회 실행(미리보기): python3 scripts/publish.py --dry-run --names 한온시스템 ...
# 실제 게시:        python3 scripts/publish.py --max-candidates 8 --names <watchlist...>
```

**cron (WSL, KST, 평일 장중 15분 주기)** — `crontab -l`로 설치됨:
```
*/15 9-15 * * 1-5 cd /home/xenonluv/stocknews && /usr/bin/python3 scripts/publish.py \
  --max-candidates 8 --names 한온시스템 삼성전자 현대차 NAVER LG전자 삼성전기 노타 코칩 로보스타 로보티즈 >> /tmp/publish.log 2>&1
```
- 15분 주기 = 하루 ~26회 ≪ Vercel 무료 100배포/일 → **무료·안정**. 1회 사이클 ~3~5분 < 15분(겹침 없음). "변경 시에만 push"로 빌드 절약.
- ⚠️ **PC가 켜져 있어야** 발동. **WSL cron은 재부팅 후 자동 시작 안 될 수 있음**(`sudo service cron start` 또는 `/etc/wsl.conf` 부팅 설정; 더 견고히는 Windows 작업 스케줄러로 `wsl.exe ... publish.py` 호출).
- 첫 자동 실행 후 `/tmp/publish.log`로 push 성공 확인.

## Scripts 카탈로그 (`scripts/`)

| 파일 | 역할 |
|------|------|
| `team1_collect.py` | 랭킹/지정종목 수집: 코드해석(autocomplete) + 종목뉴스(URL) + 컨센서스 + 차트지표. ETF/우선주 제외. |
| `team1_fetch_news.py` | 네이버 검색 API 뉴스 수집 (팀원1 출력 스키마). |
| `team2_relevance.py` | 팀원2 자동 재료필터: HARD(시황/지수/칼럼) 제외 + 종목명 **별칭 매칭** + 호악재/중요도. `MANUAL_ALIAS`에 영문/약어 종목 보강. |
| `team3_price_context.py` | 일봉 → 이평선(5/20/60)·이격도·거래량비·52주위치·국면힌트. `compute_context(code,name)` 재사용. |
| `screener.py` | 3조건 스크리너 (A 거래량+상승+거래대금≥500억 / B 재료 / C 3분봉 GC). |
| `screener_report.py` | 스크리너 JSON → 마크다운 리포트(뉴스 링크·호악재·중요도). |
| `publish.py` | **게시 자동화**: 스크리너→결정론 스코어→signals.json→변경 시 push. 2등급(signal/candidate). cron용. |
| `snapshot_ranks.py` | 거래대금/상승률 상위 일별 스냅샷 (`data/ranks/`) — A조건 정확화. |
| `codex-agent.sh` | Codex 에이전트 headless 러너 (아래). |

## 데이터 소스 (네이버, 무료)

- 랭킹: `m.stock.naver.com/api/stocks/{up|transactionAmount|tradingVolume}/{KOSPI|KOSDAQ}`
- 코드해석: `ac.stock.naver.com/ac?q={name}&target=stock` (items[].code)
- 종목 종합(컨센서스·리포트): `m.stock.naver.com/api/stock/{code}/integration` (`consensusInfo.priceTargetMean` 등)
- 종목 뉴스: `m.stock.naver.com/api/news/stock/{code}` (officeId/articleId → `n.news.naver.com/mnews/article/{oid}/{aid}`)
- 일봉: `api.finance.naver.com/siseJson.naver?symbol={code}&timeframe=day`
- 분봉(멀티데이 1분): `fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=minute&count=N` (XML) — api.stock minute는 하루치만 줌
- 업종명: `finance.naver.com/item/main.naver?code={code}` HTML의 `type=upjong&no=...">업종명`
- 검색 뉴스: 네이버 검색 API (`.env`의 `NAVER_CLIENT_ID/SECRET`)

## ⚠️ 환경 함정 (필독 — WSL/Windows 분리)

이 repo는 WSL 경로(`/home/xenonluv/stocknews`)이고, 도구마다 실행 쉘이 다르다:

- **Python 스크립트** (수집/필터/스크리너): **WSL에서** `wsl.exe -e bash -lc '...'`. WSL의 system `python3`(3.10) 사용, 네트워크 필요.
- **Codex CLI**: **Windows Git Bash(기본 Bash 도구)로만** 실행. `wsl.exe`로 부르면 Windows codex가 리눅스 네이티브 바이너리 없음 에러. cd 경로는 `//wsl.localhost/Ubuntu/home/xenonluv/stocknews`.
- **Next.js (web/)**: **WSL + Linux Node(nvm v20)** 로만. Windows npm은 UNC 경로(`\\wsl.localhost\...`)에서 동작 안 함 (`C:\Windows` cwd로 깨짐). `nvm use 20 && npm ...`.
- **인라인 파이썬 따옴표**: `wsl -lc '...'` 안에서 중첩 따옴표는 깨진다 → 스크립트 파일로 작성해 실행.

## 공개 REST API (팀원6, 읽기 전용)

Next.js Route Handler. 외부는 조회만(생성은 내부 CEO 승인 경로).
- `GET /api/signals` — 목록. Query: `?stock=종목명` `?page` `?limit`. 응답 `{ data[], pagination }`.
- `GET /api/signals/{post_id}` — 상세. 없으면 `404 {error:{code:"NOT_FOUND"}}`.
- 흐름: `page/service → /api/signals → lib/signals/repository.ts → web/data/signals.json` (현재 정적 JSON, `status:"PUBLISHED"`만; 운영 시 DB로 교체).
- 프론트는 `services/signal.service.ts` 경유만(컴포넌트 직접 fetch 금지). 서버 컴포넌트 절대 URL은 `lib/api/base-url.ts`.

## 프론트엔드 스택 (web/)

**Next.js(App Router) + TS + Tailwind + shadcn/ui + Pretendard**. 웹 + `/api/signals`를 한 스택에서. SSR로 SEO.
- 다크 금융 대시보드. ⚠️ **한국 색 관례 — 상승=빨강(`--up`), 하락=파랑(`--down`)** (미국과 반대). 국면(저점/눌림목=안전, 과열=경고, 분석불가=중립)을 색+아이콘 코드화.
- 디자인 토큰 SSOT = `web/app/globals.css`, 시그널 컴포넌트 = `web/components/signal/`, 사양 = `prompts/09` + `docs/02-design/design-system.md`.
- 보안헤더(CSP/HSTS/X-Frame 등)는 `web/next.config.mjs`. OG/메타는 `web/app/layout.tsx`(`NEXT_PUBLIC_SITE_URL` 미설정 시 운영 도메인 fallback).
- 빌드 검증: `cd web && npm install && npm run build` (WSL+nvm20). QA: `docs/03-analysis/ui-qa.md`.

## Codex 팀원 실행

Codex 역할(차트분석·팀원3·4·CEO·팀원6)은 로컬 Codex CLI(`codex-cli`, ChatGPT 로그인, model `gpt-5.5`)로 headless 실행. 러너 `scripts/codex-agent.sh` (Git Bash에서):
```bash
cd //wsl.localhost/Ubuntu/home/xenonluv/stocknews
SCHEMA=schemas/10_차트분석.schema.json OUT=out.json \
  bash scripts/codex-agent.sh prompts/10_차트분석_기술적분석.md 입력.json
```
- 환경변수: `SCHEMA`(출력 스키마 강제), `OUT`(최종 결과만 저장), `SANDBOX`(기본 read-only; 파일쓰기 시 workspace-write), `MODEL`.
- 러너는 프롬프트를 **stdin으로 전달**(대용량 입력 시 "Argument list too long" 방지) + `--disable memories --skip-git-repo-check`.
- 스키마: 팀원3(`03`), CEO(`06`), 팀원6(`08`), 차트분석(`10`). OpenAI strict 모드라 **모든 속성을 `required`에** 넣어야 함(누락 시 400).

## Key business rules
- **팀원4**: news↔ticker 연관성, 악재를 호재로 오판했는지, 차트 국면 논리모순을 검증. 실패 시 **팀원3로 반려(retry loop)**.
- **CEO 승인 게이트**: `probability_of_rise ≥ 85%` **AND** 안전 타점(눌림목/저점)일 때만 승인. 과열/과다상승은 확률 무관 기각. (정직성: 확률을 게이트 통과용으로 부풀리지 말 것.)
- 팀원1 수집 시 찌라시/중복/시황 노이즈 제거.

## 배포

Vercel(무료 Hobby). **Root Directory = `web`** (Next 앱이 하위 디렉터리). `xenonluv/stocknews` push 시 자동 재배포. 운영 도메인 `stocknews-cyan.vercel.app`. Vercel엔 시크릿 불필요(웹앱은 Naver 키 미사용 — 키는 로컬 파이프라인 전용).

## Security
- Naver Client ID/Secret은 `.env`에만 (`.gitignore` 처리, git 히스토리에 없음). 클론 환경은 `.env.example` 복사 후 채움.
- 평문 노출 이력이 있었으므로 Naver 개발자센터에서 **Client Secret 재발급** 권장.
