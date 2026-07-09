# kiwoomnews

## ACTIVE 운영 저장소

이 저장소(`/Users/jinjin/kiwoomnews`, `xenonluv/kiwoomnews`)가 현재 운영 기준입니다.
2026-07-06 이후 레이더 게시, suspects 이력, 자동매매, 익일 검증 cron은 이 저장소에서 실행됩니다.

운영 데이터 확인 시 기본 경로:

```bash
cd /Users/jinjin/kiwoomnews
```

주요 현재 데이터:

- `data/radar_history/YYYYMMDD.json` — 일자별 suspects 이력
- `web/data/radar.json` — 현재 웹 게시 데이터
- `/tmp/kiwoom_publish.log` — 게시 cron 로그
- `/tmp/kiwoom_backtest.log` — 익일 검증 로그
- `/tmp/kiwoom_autotrade.log` — 자동매매 로그

`/Users/jinjin/stocknews`는 2026-07-06에 retired 되었으므로 현재 운영 질문에는 사용하지 마세요.

국내 주식 매매 **사전정보 취합 사이트 + REST API**. 다중 AI 에이전트(Claude/Codex)가 실시간 뉴스를 수집·분석·검증하고, CEO 승인된 매매 시그널만 웹사이트와 공개 API로 게시합니다.

## 구성

```
kiwoomnews/
├── prompts/          # 9개 에이전트 시스템 프롬프트 (팀원1~6 + 팀장 + CEO + 디자이너)
├── schemas/          # Codex 에이전트 출력 JSON 스키마 (--output-schema 용)
├── scripts/          # codex-agent.sh — Codex 팀원 headless 러너
├── web/              # Next.js 게시 사이트 + 공개 REST API (/api/signals)
├── docs/             # 설계/QA 문서 (design-system, ui-qa)
├── 목적.md            # 시스템 목표·에이전트 파이프라인 명세 (원천)
└── CLAUDE.md         # Claude Code 작업 가이드 (아키텍처 SSOT)
```

## 에이전트 파이프라인

```
팀원1(수집)→팀원2(중요도)→팀원3(퀀트)→팀원4(검증)→팀장(브리핑)→CEO(승인≥85%)
                                                              ↓ 승인 시
                                            디자이너(디자인)→팀원5(게시)→팀원6(API)
```
- **Claude 역할**: 팀원1·2, 팀장, 디자이너, 팀원5(프론트)
- **Codex 역할**: 팀원3·4, CEO, 팀원6(API) — `scripts/codex-agent.sh`로 실행

## 빠른 시작

### 1) 환경변수
```bash
cp .env.example .env   # NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 채우기
```

### 2) Codex 팀원 실행 (이 컴퓨터에서)
```bash
# 예: 팀원3 퀀트 분석 (스키마 강제 + 결과만 저장)
SCHEMA=schemas/03_팀원3.schema.json OUT=팀원3_출력.json \
  scripts/codex-agent.sh prompts/03_팀원3_퀀트분석.md 팀원2_출력.json
```

### 3) 웹사이트 실행
> ⚠️ WSL 경로라 Windows npm은 UNC에서 동작 안 함 → **WSL 내부 Linux Node(nvm)** 필수. 상세: `web/README.md`
```bash
cd web && npm install && npm run dev   # http://localhost:3000
```

## 배포
Vercel 무료(Hobby) 권장 — 서버리스라 평소 비가동, `.vercel.app` 주소 무료.
에이전트 파이프라인은 이 컴퓨터(Claude/Codex)에서 실행 → 새 시그널을 push하면 사이트 갱신.

## 보안
- API 키는 `.env`에만(절대 커밋 금지, `.gitignore` 처리됨).
- 공개 REST API(`/api/signals`)는 **읽기 전용** — 생성은 내부 CEO 승인 경로로만.
