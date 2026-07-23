# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## CRITICAL: active production repository

`/Users/jinjin/kiwoomnews` is the **active production repository**.
Since **2026-07-06**, production cron, suspects history, autotrade, and backtests run from this tree.

Use this repository for current operational questions:

- suspects: `data/radar_history/YYYYMMDD.json`
- current radar payload: `web/data/radar.json`
- publish log: `/tmp/kiwoom_publish.log`
- backtest log: `/tmp/kiwoom_backtest.log`
- autotrade log: `/tmp/kiwoom_autotrade.log`

`/Users/jinjin/stocknews` is retired. Do not use it for current suspects or current trading results.

> 최종 갱신: **2026-07-23 / rank4-v3 확정**(bucket 0 = 조합D 단독+75점 이상, 기존 강한흔들기 교집합·Tier1 단독은 bucket 4 표본관찰, 발효 `20260724`, 이전 모델과 forward 분리). 직전: 2026-07-14 추천 전 공시·거래가능여부 선확인 절대규칙(금호전기 사고, `AGENTS.md §0`). 기저: **Mac 로컬 JSON 권위 원본**(관찰 전수·분봉·의사결정·자동매매 감사, prior/retro/forward 분리). SQL/SQLite는 사용하지 않는다.
> ⚠️ 폭발/식음/반등 정의는 "탐지 트랙"·"Architecture"가 현행. 운영 절차의 전체 SSOT는 `AGENTS.md`.

## 커뮤니케이션 규칙 (필독)

- 사용자는 반드시 **회장님**으로 호칭한다.
- 답변은 항상 **존댓말**로 한다. 반말·친구 같은 말투를 쓰지 않는다.
- 매매·순위·자동매매 관련 답변은 단정적으로 짧게 말하되, 판단 근거와 데이터 한계를 함께 밝힌다.

## ⛔ 최우선 절대규칙 — 추천 전 공시·거래가능여부 선확인 (2026-07-14 금호전기 사고 후 확정)

종목 추천, 한 종목 선택, 종가베팅, **익일 고가 예측, suspects/youtong 우선순위 작성** 시에는
**과거 확률·레이더 순위·로컬 통계를 보기 전에** 아래 순서로 최신 외부 사실을 먼저 확인한다. 순서를 뒤집지 않는다.

1. **KRX/KOSCOM 원공시에서 다음 거래일 실제 거래 가능 여부** — 매매거래정지 시작·종료·해제조건, 주식병합/분할/감자/합병에 따른 변경상장일, **정지 '예고' vs 이미 확정된 정지 구분**, 상장폐지·정리매매·관리종목 일정.
2. 현재·다음 거래일 **시장경보·거래제한** — 주의/경고/위험, 단기과열 단일가매매, 추가 상승 시 정지 조건.
3. 회사 최신 주요공시·권리일정 — 유상증자·CB·보호예수 해제·권리락·신주 상장.
4. 최신 뉴스·직접 재료·테마 지속 여부·시장 상태.
5. 위 검증을 통과해 **다음 거래일 매매 가능한 종목에 한해서만** 로컬 예측기·과거 통계를 실행하고 순위를 만든다.

- **다음 거래일 매매정지가 확정된 종목은 과거 적중률·예상 고가가 아무리 높아도 추천·순위·익일고가 모집단에서 즉시 제외**하고 `거래정지로 예측 불가`라고 명시한다. 거래 가능 여부를 확실히 확인 못 하면 `공시 확인 불완전`으로 표시하고 추천하지 않는다.
- **레이더에 suspect로 노출됐다는 사실은 거래 가능성을 보증하지 않는다.** 로컬 통계·배지는 KRX/KOSCOM 확정 공시를 덮어쓸 수 없다.
- ⚠️ **시스템은 거래정지를 감지·배제하지 못한다(코드 공백).** radar·publish·autotrade에 정지/주식병합 배제 로직이 없고(`autotrade_common.py`는 "거래정지 리스크 감수는 회장님 책임"이라고만 명시), `alert_release`(🔓 해제예정 배지) 로직은 공시 파싱에서 "매매거래" 제목을 제외해 **정지 예고를 못 본다.** 그래서 정지 예정 종목에 오히려 "해제 예정" 배지가 붙을 수 있다. 배지·순위를 거래가능 보증으로 오인 금지.

**영구 회귀 사례(2026-07-14 금호전기 001210)**: 주식병합으로 KOSCOM 공시상 `2026-07-15~07-30 매매정지`, `07-31 신주권 상장 예정`이었는데, `alert_release=true`(🔓 해제 예정)로 익일 고가 후보 상위에 잘못 올랐다. 정지 예고 공시(07-01·07-08)와 주식병합 정지를 `alert_release`가 파싱 제외해 정반대 배지를 달았고, 시스템에 정지 배제 로직도 없었다. 이후 모든 추천은 **공시 선확인 → 거래가능 필터 → 뉴스 → 통계 비교** 순서. 상세 절차는 `AGENTS.md §0·§4·§11`.

## ⚠️ 종목군 4종 구분 (답변 전 필독 — 절대 혼동 금지)

이 시스템은 **4개의 완전히 다른 종목군**을 만든다. 회장님이 매수하는 것은 **suspects뿐**이다.
답변 전 반드시 어느 그룹인지 확정할 것 — forecast/youtong을 suspects와 섞지 말 것.

| 종목군 | 페이지 | 회장님 매수? | suspects와의 관계 |
|--------|--------|-------------|------------------|
| **suspects** (재매집·흔들기) | 메인 레이더 | ✅ **매수 대상**(autotrade) | 본체 |
| **explosions** (폭발) | /forecast | ❌ 표시용 | 폭발 레지스트리 = 재매집의 씨앗(재료) |
| **youtong** (곧 폭발) | /youtong | ❌ 표시용 | 레지스트리 이력 → 급소/저점매집 일부 기여 |

- autotrade·record_history·백테스트는 **suspects만** 읽는다. explosions[]/youtong[]은 매수·통계에 안 쓰인다(표시용).
- 회장님이 "종목/1위/이거/금요일 종목" 물으면 **어느 페이지인지 먼저 확인**하고 답한다. 절대 섞지 마라.

## suspects 정렬 SSOT (2026-07-23 rank4-v3 확정 / 2026-07-10 정렬4 기반)

아래 표가 현재 suspects 화면·자동매매 순위의 단일 기준이다. 탐지 트랙 설명에 남아 있는 예전 "급소 최상단", "저점매집 관찰축", "매우좋음후보 우선" 문구보다 이 표가 우선한다. **모델 SSOT는 `scripts/rank_policy.py`이며 현행은 `rank4-v3`(발효일 `20260724`).** v3는 v2의 기존 분기 위치를 유지한 채 bucket 0과 4의 대상군만 교체했다. 따라서 중복 조건의 bucket 1~3 분류와 kill switch 의미는 바뀌지 않는다.

| bucket | 조건 | 운영 판단 |
|--------|------|-----------|
| 0 | **조합D 단독+75점 이상**(`shakeout is True` AND `very_good is False` AND `strength_tier >= 3` AND `suspicion_score >= 75`) | 최근 live final n=15: +7 13/15(86.7%), +13 11/15(73.3%), -5 10/15(66.7%); rank4-v1 EOD n=7: +7 6/7(85.7%). 단, 2026-01~05 current-retro 전수는 +7 84/169(49.7%)이므로 **최근 국면 우선순위**일 뿐 장기 고정확률·매수 보장이 아니다. `very_good` 결측을 False로 추정해 승격하지 않는다. 급소/저점매집 중복은 기존 branch 위치상 bucket 1~3을 유지한다. |
| 1 | `geupso` AND `peak_turnover_pct >= 150` | 급소+회전150. 표본주의·폭발형 배지, kill switch 필수 |
| 2 | `low_accum` AND `peak_turnover_pct >= 90` | 저점매집 상위 복귀 |
| 3 | `low_accum` 기타 | 저점매집 실전 상위 |
| 4 | **강한흔들기+조합D**(`very_good is True` AND `very_good_tier in (tier1, tier2)` AND `strength_tier >= 3`) 또는 **매우좋음 Tier1 단독** | 소표본 관찰군. live final 2건(CS 교집합·금호전기 Tier1 단독)의 2/2를 기대확률로 노출하지 않는다. 내부 정렬은 Tier1+조합D → Tier2+조합D → Tier1 단독 |
| 5 | `shakeout` AND `suspicion_score >= 75` | 흔들기 우선 |
| 6 | `shakeout` AND `strength_tier >= 3` | 조합D 흔들기 |
| 7 | `suspicion_score >= 75` 기타 | 중상위 |
| 8 | `shakeout` 기타 | 중위 |
| 9 | `alert_release` OR `alert_risk_released` 단독 | 규제해소 재료 관찰 |
| 10 | `geupso` 단독 | 약승격 |
| 11 | 기타 suspects | 기본 |

정렬 내부 tie-breaker: `suspicion_score` 내림차순 → 흔들기 `fade_pct` 내림차순 → `peak_turnover_pct` 내림차순 → `turnover_2d_pct` 내림차순 → 기존 안정 tie-breaker. bucket 0은 `suspicion_score` 내림차순으로 정렬하고, bucket 4 내부만 `_very_good_sort_rank`(Tier1+조합D=0 → Tier2+조합D=1 → Tier1 단독=2)를 먼저 적용한다.

정렬4/rank4-v3 구현 원칙:

- **bucket 0 승격은 `very_good is False`를 반드시 요구한다(rank4-v3).** `_is_combo_d_only`는 `shakeout is True` AND `very_good is False` AND `strength_tier>=3`이고, 여기에 `suspicion_score>=75`가 붙을 때만 최상위다. 결측 `very_good`을 False로 추정하지 않는다.
- `_is_very_good_combo_d`와 `very_good` Tier1 단독은 bucket 4 표본관찰로 분류한다. `very_good=False`인데 tier 문자열만 tier1/tier2인 불일치 저장값은 강한흔들기로 승격하지 않는다.
- `very_good_candidate`는 승격키가 없다. 배지·history·전진검증만 유지하고 자기 흔들기 bucket으로 자연 편입한다.
- `strength_tier >= 3`을 조합D로 본다. 과거 history에는 `Tier4(약)` 라벨이 섞여 있으므로 문자열 판정 금지.
- **버전 분리**: rank4-v3는 발효일 `20260724`부터 forward로 인정한다. rank4-v1(발효 `20260713`)과 배포 전 교체된 rank4-v2 메타는 각 버전 평가에 보존하고 v3 성과표와 섞지 않는다(`radar_backtest.KNOWN_FORWARD_EFFECTIVE_FROM`). 버전 승격 직후 v3 forward 표본은 n=0에서 시작하므로 킬스위치는 표본이 쌓일 때까지 "수집 중"이다.
- `alert_now` 경고/위험은 정렬을 직접 바꾸지 않고 `고위험 고탄력` 배지로만 격리한다.
- `상단컷`, `표본주의`, `폭발형`은 배지다. bucket 자체를 바꾸지 않는다.
- suspect와 history에는 `rank_bucket`, `rank_reason`, `shadow_bucket`, `expected_touch7_rate`, `expected_high_pct`, `rank_bucket_stats_snapshot`을 저장한다.
- `/performance`는 `rank_bucket_stats`로 bucket별 n·고유종목·+7/+13 터치·Wilson 하단·평균/중앙값 고가·최저 고가·평균 종가수익·상승마감률과 kill switch 상태를 보여준다.

Kill switch:

- bucket 1은 신규 표본에서 익일 고가 +7% 미달이 1건이라도 나오면 bucket 4 아래로 하향 상신한다. n=10에서 Wilson 하단 <60%, 종가평균 <+3%, 최저 익일고가 <+7% 중 하나라도 걸리면 bucket 1 자격 상실.
- bucket 2·3은 신규 10표본마다 Wilson 하단 <55%, 종가평균 <+3%, 상승마감률 <55% 중 2개 이상이면 관찰축 하향 상신한다.
- 매우좋음후보는 n>=15, Wilson 하단>=60%, 종가평균>+3%, 상승마감률>=55%를 모두 만족할 때만 재승격 논의한다.

## Mac 로컬 JSON 권위 원본 (문제7 합의)

연구·감사 원본은 `/Users/jinjin/kiwoomnews/data/local/radar_raw/` 아래의 **JSON만** 사용한다. `data/local/`은 Git에서 제외하며 용량 제한, 자동 삭제, 자동 축약, 자동 아카이브를 두지 않는다. 백업은 회장님이 수동으로 한다.

- `schema.json`, `models/rank4-v1.json`: 스키마와 불변 모델 정의. 모델 SSOT는 `scripts/rank_policy.py`이며 **현행은 `rank4-v3`(발효일 `20260724`)**. rank4-v1(발효 `20260713`)과 배포 전 교체된 rank4-v2 메타는 보존·평가하고 v3 forward와 분리한다. 2026-07-10은 `mixed-deployment` 참고값으로만 보존하고 forward에서 제외한다.
- `YYYY/MM/DD/scans/scan_*.json`: radar 관찰 전수와 실제 게시 회차의 불변 파일. 대상은 전 종목 시장이 아니라 `active_explosion + youtong + seed + naver up/down + shakeout extra`의 **실제 관찰 합집합**이다. suspects 탈락 종목도 값과 복수 게이트 사유를 보존한다.
- `minute/CODE_MARKET.json`: 종목·거래일·시장기준별 분봉. 시간 중복을 병합하고 후속 완결값을 우선하되 과거 봉을 삭제하지 않는다. 빈 응답과 API 오류를 별도로 기록한다.
- `decisions/krx_1518.json`, `krx_close.json`, `nxt_1950.json`, `operational_eod.json`: publish 회차 중 각 기준시각 이전 최신 성공본(operational EOD는 20:51 이후)을 첫 성공 파일로 고정한다. 15:21/19:51 회차가 과거 결정을 덮어쓰지 못한다.
- `trades/decisions.jsonl`, `events.jsonl`: 자동매매가 실제 읽은 root 메타, 안전판정, 주문 시도·결과, 핵심 원장 기록 여부를 append한다. 기존 포지션 JSON과 거래 로그를 대체하지 않는다.
- `evaluation/next_day.json`, `manifest.json`: 의사결정 모집단별 익일 결과와 일별 파일 checksum 색인. manifest는 디렉터리에서 재생성할 수 있으며 각 JSON 자체 payload checksum도 다시 검증한다.

모든 완결 JSON은 같은 디렉터리의 임시파일에 쓰고 `flush -> fsync -> JSON 재파싱 -> os.replace` 순서로 교체한다. scan은 덮어쓰지 않는다. 토큰·앱키·Authorization·계좌번호는 재귀적으로 제거한다. SQL, SQLite, 별도 DB 프로그램은 도입하지 않는다.

안전 경계:

- scan/minute/manifest/연구용 JSON 저장 실패는 레이더 순서·게시·주문 종목·수량을 바꾸지 않는 선택적 감사 실패다.
- stale/손상 radar, 포지션 원장 로드 실패, 중복매수 판정 불가, 예산·수량·안전필터 실패, 계좌 안전상태 불명은 fail-closed다.
- 체결 후 핵심 포지션 원장 저장 실패는 긴급 로그와 텔레그램 경고를 남기고 reconciliation 대상으로 기록한다.

순위 시점은 `precut_rank`, `published_rank`, `first_seen_rank`, `latest_published_rank`, `krx_decision_rank`, `nxt_decision_rank`, `eod_rank`로 구분한다. 기존 `final`은 하위호환일 뿐 forward 모집단의 대체값으로 추론하지 않는다. `/performance`의 실제 상신 가능한 kill switch는 저장된 모델·버킷·의사결정 모집단을 만족하는 `rank_bucket_stats_forward.eod`만 사용한다. 소표본은 n=1부터 값과 함께 표시하며 자동 제외하지 않는다.

## 시장 레짐 메모 (2026-07-09 회장님 관찰)

- 회장님 관찰: **시장 전체나 코스닥이 약세장일 때 코스닥 신규상장주가 단기 급등하는 경우가 많다.**
- 해석: 약세장에서는 기존 주도주·대형주 수급이 둔해지고, 당일 단기 자금이 **유통물량이 작고 스토리가 새로우며 차트 매물대가 짧은 신규상장주**로 몰릴 수 있다.
- 신규상장주는 공모가·상장일 고가·상장 후 저점처럼 시장 참여자가 보기 쉬운 기준선이 있어, `공모가 회복`, `상장 첫날 고가 대비 과락`, `유통물량 회전`, `보호예수/락업`, `AI·정책·주주환원 명분`이 결합되면 재료 크기보다 수급 탄력이 크게 나올 수 있다.
- 따라서 약세장 신규상장주 급등을 분석할 때는 **공시 금액만 보지 말고 유통시총 대비 재료 규모, 유통가능물량 회전율, 공모가 회복 여부, 상장 후 낙폭, 당일 시장 레짐**을 함께 본다.
- 단, 이 규칙은 아직 백테스트로 확정된 공식이 아니라 **보조 가설**이다. 답변 시 "약세장 신규상장 수급 프리미엄 가능성"으로 표현하고, 확정 재료처럼 단정하지 않는다.

## 뉴스·공시 재료등급 체계 (2026-07-09 추가)

suspects 후보마다 뉴스/공시 재료를 `material` 객체로 기록한다. 목적은 **매우좋음·흔들기 축에 재료 강도를 결합하면 익일 성과가 좋아지는지 전진검증**하는 것이다. 현재는 점수·정렬·자동매매에 반영하지 않는다.

| 등급 | 의미 | 대표 키워드/해석 |
|------|------|------------------|
| `S` | 정책·상폐해소·M&A·대기업급 강재료 | 정부/국가 프로젝트, 삼성전자/SK하이닉스/대기업, 공개매수, 상폐 우려 해소, 경영권, 인수합병, 거래재개, 회생 |
| `A` | 공시·대형 이벤트 | 제3자배정/유상증자/무상증자, 최대주주, 공급계약, 수주, 투자유치, 지분취득, KRX/DART 공시 |
| `B` | 실적·재무개선·사업 성과 | 채무상환, 재무구조 개선, 실적/영업이익/순이익/매출, 흑자, 특허, 승인, 허가, 임상, 신제품, 수출 |
| `C` | 테마·간접 수혜 | 관련주, 테마, 수혜, 기대감, 부각, 지역/연고/이름 묶임 |
| `D` | 악재·희석 우세 | 횡령/배임, 관리종목, 거래정지, 감사의견, 대규모 희석, CB/BW, 유증/감자 리스크가 호재보다 큰 경우 |
| `N` | 재료 없음/미확인 | 종목뉴스 없음, 네이버 지연, 재료 키워드 없음, 분석 실패 시 fail-safe |

- 구현 SSOT: `scripts/team2_relevance.py`의 `score_material()` / `_material_grade()`. LLM 없이 제목·요약 키워드와 종목 별칭 매칭으로 결정론 분류한다.
- 산출 필드: `grade`, `score(0~100)`, `summary`, `sentiment`, `reliability(공시+뉴스/공시/뉴스/뉴스없음)`, `freshness`, `directness(직접/간접/미확인)`, `tags`, `risk_flags`, `evidence`, `source_count`, `relevant_count`.
- 점수 흐름: 종목명이 제목/본문에 언급되고 재료 키워드가 있어야 후보가 된다. `S/A/B/C` 키워드, 공시성, 직접 제목 언급, 신선도, 출처 수를 가산하고 악재·희석·간접 관련주 표현은 감점한다.
- 악재 캡: 리스크 플래그가 있고 구조조정/상폐해소/최대주주 수혈 같은 `MATERIAL_RESCUE`가 없으면 score를 45 이하로 제한해 `S/A` 오판을 막는다. 상폐·시총미달 뉴스는 구제/회복 문맥이 없으면 강재료로 보지 않는다.
- 레이더 연동: `scripts/radar.py`의 `_explain_cause()`가 종목뉴스 10건을 가져와 `score_news()`로 카드 표시용 관련뉴스를 만들고, 같은 raw news로 `score_material()`을 계산한다. 흔들기와 재매집 후보 모두 `material`을 가질 수 있다.
- 캐시: `data/material_cache/YYYYMMDD.json`에 **등급 결과가 아니라 raw news만** 저장한다. 로직을 바꾸면 같은 raw news로 등급을 재계산할 수 있게 하기 위한 설계다. `.gitignore` 대상이며 운영 데이터 파일로 커밋하지 않는다.
- 캐시/속도 환경변수: `RADAR_MATERIAL_NEWS=0`이면 재료뉴스 조회를 끈다. `RADAR_MATERIAL_CACHE_TTL_SEC` 기본 1800초, `RADAR_MATERIAL_NEWS_TIMEOUT_SEC` 기본 4초, `RADAR_MATERIAL_NEWS_RETRIES` 기본 1회.
- 폭발 레지스트리: 신규 폭발 시 `cause_summary`, `cause_titles`, `material`, `cause_done=True`를 한 번 캡처해 이후 회차에서 stale 뉴스로 덮이지 않게 한다. 과거 레코드는 제목만 있으면 `_material_from_titles()`로 최소 등급을 복원한다.
- 병합 규칙: 같은 종목이 재매집/흔들기 양쪽에서 잡히면 더 높은 `material.score`를 가진 값을 유지하고, 뉴스가 비어 있으면 기존 news를 보존한다.
- history 기록: `scripts/publish.py`가 `data/radar_history/YYYYMMDD.json`에 `material`과 `news[:6]`를 저장한다. 과거 백필은 불가능하므로 오늘 이후 표본만 신뢰한다.
- 성과 검증: `scripts/radar_backtest.py`가 `material_bands`(S/A/B/C/D/N별 익일 종가 상승확률·평균수익)와 `material_signal_bands`(매우좋음 Tier1+S/A, 매우좋음 후보+S/A, 흔들기+S/A 등)를 `web/data/performance.json`에 만든다. 구표본은 `unknown`으로 제외한다.
- 웹 노출: suspects 카드에는 `재료 S/A/B/C/D` 배지가 표시된다(`N`은 숨김). `/performance`에는 "뉴스·공시 재료등급별 익일 상승확률", "재료등급 × 매우좋음/흔들기 조합 성과" 표가 표시된다.
- 운영 원칙: 재료등급은 **설명·기록·검증용**이다. 표본이 충분히 쌓이고 `S/A` 조합이 실제로 리프트를 보일 때만 정렬이나 자동매매 반영을 검토한다.
- 한계: 네이버 종목뉴스 API에 없는 공시/찌라시는 `N`이 될 수 있다. 실시간 분석 답변에서는 필요 시 웹 검색/KRX/DART/언론 원문으로 보강하고, 확인된 공시와 게시판성 루머를 분리해서 보고한다.

## Project Status

**"이벤트 매집 레이더"** — 10일 내 자명한 글로벌 증시 이벤트(FOMC·CPI·실적)를 앞두고 **큰돈이
들어와 매집·재반등이 의심되는 종목**을 자동 탐지해 웹에 게시하는 시스템. 순수 Python 파이프라인
(레이더 본체는 LLM 미사용)이 데이터를 만들고, Next.js 사이트가 Vercel에 라이브
(https://stocknews-cyan.vercel.app, `xenonluv/kiwoomnews` push 시 자동 재배포, Root Directory=`web`).

⚠️ **환경 분리 (필독):**
- **이 WSL은 백업·코드작업 사본.** 프로덕션 cron(게시·검증·푸시)과 텔레그램 실송은 **Mac에서** 돌아간다.
  코드를 푸시한 뒤 Mac 반영은 `git pull` + (cron 변경 시) `install_cron.sh` 재실행이 필요하다.
- **KIS/네이버/텔레그램 시크릿은 Mac `.env`에만** 존재(WSL엔 없어 일부 스크립트는 no-op).

## 탐지 트랙 (2026-06-23 전면 개편)

레이더는 **하나의 흐름(폭발→오늘 5분 양봉 재분출)** + **당일 폭발 리스트** + **곧 폭발할 후보(유통 회전
진행중)** 를 게시한다. 모든 reaccum/explosion/youtong 데이터는 `score_raw=0` 통계 격리(표시·정렬 전용, core 가중치 튜닝 미반영).

- **폭발(explosion)** — 당일 **고가등락률 ≥22% AND 당일 거래량/유통주식수 ≥90%**(유통주식이 통째로 손바뀜).
  거래대금 절대 게이트·거래대금 순위·등락률 합집합 유니버스는 **전면 폐지**. 스캔 소스는 네이버 up(등락률) 랭킹뿐.
  유동비율(발행주식수)이 없으면 90% 회전율 확정 불가 → 폭발 미인정(fail-safe). **당일 폭발 종목은 `/forecast`에 게시.**
  레지스트리는 **오늘 라이브 스캔 + 지난 6거래일 소급 백필**(`backfill_window_explosions`: 오늘 등락률 상위 ∪
  기존 레지스트리 활성 코드 재검증의 일봉을 훑어 22%/90% 폭발일을 `vol_turnover_pct`로 적재)로 채운다.
  비용 가드: 검증완료 코드·당일 이미 스캔한 코드(`window_scanned`)는 재스캔 스킵.
- **재매집/반등(reaccum 수상종목)** — **최근 6거래일 폭발 종목**(전일 폭발)이 **14:30~장종료 5분봉
  양봉(몸통%≥1.5%)이 2회 이상 스파크**(마감 직전 재분출) AND **현재 등락률 −5%~+7%**(깊은 식음/이미 분출 제외,
  조용한 매집 구간)인 상태. ⚠️ 스파크는 **그 봉의 절대 등락률과 무관하게 카운트** — −9%에서 양봉으로 회복해
  −5% 마감한 깊은 식음 반등도 잡는다(현재 등락률 게이트가 최종 위치만 판정). MA20·투신·거래원·거래대금 게이트는
  미사용. ⚠️ **16:00부터(=15:30+신선도상한 30분) '현재 등락률'을 NXT 애프터마켓 야간가(네이버 `overMarketPriceInfo`)로
  재평가**(`_nxt_change_pct`, `NXT_REEVAL_START_HHMM`) — 15:31~15:51엔 정규장 막판 5분 양봉 텔레그램이 신선하게
  나가도록 KRX 유지, 16:00부터 NXT 단일가 체결 시점에 재평가. NXT 시간외 회복(정규장 +8%→NXT −5%, −9%→−5%)하면 밴드 진입,
  이탈하면 빠진다. **스파크(≥2)는 정규장 14:30~15:30 것 그대로** — KIS가 NXT 애프터마켓 분봉을 안 줘(분봉
  15:30서 끊김, 실측) NXT 5분봉 스파크는 데이터 부재로 불가, 위치(등락률)만 보정. suspect에 `change_basis`
  ("KRX"/"NXT") 노출(웹 'NXT 시간외' 배지). publish cron이 **9~20시**로 확장돼 애프터마켓을 커버.
  당일 폭발(signal_date==peak_date)은 `/forecast`에만, 수상종목은 '과거 폭발 + 오늘 재분출'. **백테스트 튜닝 원천 기록(2026-07-07)**: 흔들기/매우좋음 표본은 history와 backfill에 신호일 `signal_open/high/low/close`, `signal_volume/value`, `signal_peak6_price`, `signal_peak60_price`, `signal_ma20/ma10`, `run_6d_pct`, `ma20_gap_pct`, `ma10_margin_pct`, `float_ratio`를 저장한다. 익일 평가는 `next_open/high/low/close`와 각 pct를 저장해 dd6·회전율·MA·손절/익절 경계를 후행 재튜닝할 수 있게 한다. **🎯 매수급소(2026-07-03 회장님 지시)**: ① 추적 풀 확장 — 폭발 후 6일 지나도 **20일선 위인 동안 계속 추적**(`--reaccum-track-days` 30 캡, `_extended_track` 플래그·MA20 아래로 깨지면 탈락) ② **당일 14:30↑ 몸통 ≥2.0% 양봉 스파크 ≥2회(`--geupso-body-pct`/`--geupso-min-count`) = 매수급소** — 등락률 밴드(−5~+7%) **상한 +10%까지만 완화**(`--geupso-change-max`, 회장님 지시 2026-07-06: 현재 등락률 +10% 초과 급등은 급소 제외 — 남광토건 +27.5%가 '재매집'으로 오분류되던 것 차단. 하한은 무제한 유지=깊은 식음 반등 허용·폭락은 저점매집 담당, 밴드 밖은 급소만 통과, 14:30 전엔 기존 조기 컷 유지). suspect에 `geupso`/`geupso_bars` 적재, suspects 정렬 급소 최상단(단 **KRX 시장경보 경고/위험 지정 종목은 급소여도 최후순위 강등, 단 경고+해제예정(`alert_release`)은 최상단 승격** — `radar._alert_level`(네이버 basic marketAlertType, 회당 캐시·fail-safe), suspect `alert_now` 적재·웹 배지·history 기록. 회장님 지시 2026-07-03. **위험→경고 강등 직후도 동급 승격(2026-07-10 회장님 승인)**: `alert_release.recent_risk_release`가 '투자위험종목 지정해제' 공시(네이버 공시목록) 3캘린더일 내면 suspect `alert_risk_released=True` — 최고 단계 규제 해소 재료로 alert_release와 같은 정렬키 승격 + 웹 "🔓 투자위험 해제 직후" 배지(서산 원형: 7/9 위험해제 → 7/10 회전 245% 폭발). 경고 종목은 `alert_elapsed_days`(경고 지정 경과 매매일수, 지정일=1·999=오래된 지정·None=판정불가)도 history에 적재 — "경고 코호트 성과가 지정 경과일에 따라 다른가" 전진검증용(정렬 미사용), 웹 SuspectCard 대형 🎯 배지, 텔레그램 제목 "🎯 매수급소"로 구분. 근거: 덕신(2%+ 스파크 5.67/3.11% 2개=익일 폭락에도 매집 받침) vs 상지건설(1.51% 1개=받침 없음, 7/1 상한가→식음) 실측 — 스파크는 '큰손이 아직 있다'는 지문, 그 지문이 찍힌 날이 매수 급소.
  **🧲 저점매집(2026-07-03 회장님 지시)**: 추적 풀 종목이 **당일 등락 ≤−10% AND 현재가 ≥MA20 AND 시간 무관(09:00~) 몸통 ≥2.0% 5분 양봉 ≥3회**(`--lowaccum-change-max`/-10 `--lowaccum-body-pct`/2.0 `--lowaccum-min-count`/3)면 저점매집 의심 — 주포가 의도적으로 눌러놓고(투자경고예고 회피 등) 밑에서 받는 지문. suspect에 `low_accum`/`low_accum_bars`, 웹 🧲 오렌지 배지(레이더), 텔레그램 제목 "🧲 저점매집 의심". ⚠️ **저점매집 '순위 승격'·'저회전 오름차순' 정렬은 철회(2026-07-04 회장님 결정)** — 익일 기준 미검증(저회전<15% 폭락 표본 n=2·둘다 익일 +13% 실패, 5분봉 지문 소급 불가). low_accum은 **배지·`by_low_accum` 관찰축으로만 유지**(순위는 suspicion_score순, 표본 성숙 후 재도입 판단). **💥 흔들기(2026-07-03 회장님 지시)**: **당일 고가등락 ≥+20% AND 페이드(고가−현재등락) ≥15%p AND 당일 유통회전율 ≥40% AND MA20 위 AND 경고/위험 미지정 AND 과확장붕괴(6일+100%&음수) 아님** — 폭발 직후 상한 터치 후 대량 손바뀜 흔들기(금호건설·동양파일 6/25 원형: 익일 상한+연상). 실측(6~7월 폭발풀 n=38): 익일 고가 +13% 터치 68.4%·+7% 78.9%·평균 +18.0%·EV +4.63%/회. `scan_shakeout`(up∪down 랭킹 소스 — 음봉 마감이 up에 없어 down 병행, 산술 프리필터로 KIS 콜 절감), suspect `pattern="shakeout"`·`shakeout`/`fade_pct`, **정렬 최상단**(경고 강등 다음 최우선), 웹 💥 대형 배지, history 기록. **기존 폭발·youtong 게이트 불변** — 둘 사이 사각지대(금호 6/25 회전 66%·종가 +5.3%) 전용 그물. 상수 `SHAKEOUT_*`. **⭐ 매우좋음(very_good, 회장님 지시 2026-07-07)**: 흔들기 AND 6일 고점대비 낙폭 `dd6 ≤ SHAKEOUT_DD6_MAX(−30)` → suspects **절대 최상단 + ⭐매우좋음 뱃지**·자동매매 rank 대상. `very_good_tier`: **Tier1 −45<dd6≤−30(우선)**, **Tier2 dd6≤−45(과낙 별도 표시)**. **매우좋음 후보**(`-30<dd6≤-25`)는 일반 흔들기보다 우선 정렬되는 관찰 구간이며, 자동매매는 사용자가 선택한 rank 정책을 따른다. 흔들기 `strength_tier`(2일회전+60일낙폭)는 **정렬 미사용·카드 설명/검증용만 유지**; 후보 다음의 일반 흔들기 정렬은 `fade_pct → suspicion_score → dd6` 순. 근거: 전수조사 14만 무편향(익일 고가 +7% 터치 흔들기 AND dd6≤−30 = **72%** vs 단독 흔들기 44%·깊은눌림 41%, `scripts/pullback_census.py`). dd6=6일 기준(기존 `peak_dd_pct` 60일과 별개)·`very_good`/`dd6_pct`/`very_good_tier`/`very_good_candidate` suspect·history 적재·score_raw=0 유지(표시·순위만). 근거: 덕신 7/3 실측(−17%·MA20 +8%·11:05부터 2%+ 4방) vs 상지건설(1방, 비발동). **추적 풀 = 폭발 레지스트리(6일+장기 30일) ∪ youtong 이력 10거래일**(`.youtong_registry.json` `history[date]` 롤링, `_yt_track` 유사레코드 — 덕신·가온전선처럼 고가+22%는 넘겼지만 회전율 90% AND에서 탈락해 레지스트리에 없는 사각지대 보완. **폭발 게이트 자체는 불변** — 회장님 확장 보류 결정 2026-07-02). youtong 이력·장기추적분은 급소/저점매집 전용(기존 reaccum 밴드 의미론 불변)·MA20 위인 동안만.
- **곧 폭발할 후보(youtong)** — **위로 올라오며 분출하는 종목**: **09:30 이후**(`--youtong-start` 0930, 그 전 무시),
  **현재 등락률 ≥7% AND 유통주식 회전율 ≥50%(상한 없음) AND 09:30 이후 5분봉 양봉(몸통%≥1.5%) 스파크 ≥1회**.
  ⚠️ **폭발(고가≥22% AND 회전율≥90%)로 승격해도 youtong에서 삭제하지 않고 유지**(2026-06-29 회장님 지시 "삭제말고 냅둬" — 후보가 실제 폭발=적중이라 추적 유지). `/forecast`에도 병행 노출되며 youtong엔 **`exploded` 플래그·🔥 폭발 배지**로 구분. **`/youtong`에 게시.**
  싼 게이트(등락률·회전율)는 같은 up 랭킹 루프에서 수집, 5분봉 스파크 확정·**종일 지속**은 `prepare_youtong`이
  처리(`.youtong_registry.json` — 한 번 포착되면 장 마감까지 유지, 현재가 실시간 갱신·"처음 포착 HH:MM" 보존,
  밴드 이탈/하락해도 안 사라짐). 분봉은 신규 후보만 1회 조회(`_minute_bars_with_fallback` UN→J, 비용 가드).
  임계: `--youtong-change-pct`(7) `--youtong-turnover-min`(50) `--youtong-start`(0930) `--youtong-spark-min`(1). 표시·참고용(통계 무관).

> **통계 격리 원칙(드리프트 방지):** reaccum/explosion/youtong·strategy_sim·change_band 등 "실험·표시 전용"
> 데이터는 전부 `score_raw=0`으로 core 적중률·가중치 튜닝과 분리한다. 화면 표시 ≠ 통계 반영.

## Architecture: 파이프라인

```
[유니버스/스캔 소스] 시장별(코스피/코스닥) 등락률 TOP-N(네이버 up 랭킹)만. (거래대금 순위·합집합 폐지)
   ▼
[폭발 캐치] 고가등락률 ≥22% AND 당일 거래량/유통주식수 ≥90% → registry(.explosion_registry.json)
            + 당일 폭발 리스트(explosions[], /forecast 게시). 최근 6거래일 폭발만 추적.
            (registry = 오늘 라이브 + 지난 6일 소급 백필[등락률 상위 ∪ 레지스트리 재검증] — 전일 폭발 후보 풀 보강)
   ▼ (같은 스캔 루프에서 동시 수집)
[곧 폭발 후보] 09:30↑ · 현재 등락률 ≥7% AND 유통회전율 ≥50% AND 09:30↑ 5분 양봉 스파크 ≥1 AND 미폭발
              → youtong[](/youtong 게시). 종일 지속(.youtong_registry.json — 포착 후 마감까지 유지).
   ▼
[정밀 판정·종목별, KIS 공식 API]
   재매집: minute_bars_today → 14:30~장종료 5분봉 양봉(몸통%≥1.5%) 2회 이상 AND 현재 등락률 −5~+7%. 전일 폭발 종목만.
   ▼
[조건 가점/기록] event_calendar(D-10 정적 캘린더+규칙) × theme_map(뉴스·업종 테마 매칭)
              + score_material(S/A/B/C/D/N 재료등급, 기록·전진검증 전용)
   ▼
[점수] 재매집 변별 점수 = base62 + re_count(0~10, 5분 스파크 수)
            +re_body(0~6, 최대 몸통%)+peak_turnover(0~10, 폭발일 회전율)+re_turnover(0~6, 당일 회전율),
            min(95, 합) — 표시·정렬 전용(score_raw=0).
            **회전율은 '유통주식수 기준·거래량'**(당일 거래량/유통주식수). 유동비율(발행주식수)은
            `float_ratio.py`가 wisereport(`navercomp.wisereport.co.kr` "발행주식수/유동비율") 스크랩·캐시
            (data/float_ratio.json, 7일). suspect에 turnover_pct·peak_turnover_pct·float_ratio·turnover_basis 노출.
            **폭발일 마감강도**(reaccum.peak_ibs=(종가−저가)/(고가−저가)·peak_uppertail=(고가−종가)/종가%)도 registry·
            history에 적재(7일 표본 실증: 약마감[윗꼬리 큰]이 익일 연속성↑·상한가류 강마감은 식음↑ 경향). **소표본이라
            점수·게이트 미반영, 표시·전진검증 전용**(history에 쌓아 향후 검증 후 점수 반영 여부 결정).
   fade/shakeout: raw 가중합(통계 반영)
   forecast: 동결 모델 "3일내 +7% 터치" 과거 실측 확률 라벨(표시 전용)
   ▼
publish.py → web/data/radar.json → 변경 시에만 git push → Vercel 재빌드(~30초)
            → 재반등 봉이면 텔레그램 알림(Mac만)
```

- **빈 레이더(수상종목 0)도 유효 상태**로 게시 ("오늘은 레이더 깨끗"). 당일 폭발 0종목도 정상.
- `score_breakdown`을 JSON에 그대로 실어 웹에서 점수 해부도로 투명 공개.
- ⚠️ **`/forecast`는 더 이상 analyzer 종가베팅이 아니라 '당일 폭발 종목' 리스트**(publish.py가 만든 `radar.json`의
  `explosions[]`). `analyzer/`(종가베팅·`/api/predictions`) cron은 폐지(코드는 잔존, 미사용). `screener.py`·`prompts/` 레거시.

## Scripts 카탈로그 (`scripts/`)

| 파일 | 역할 |
|------|------|
| `kis_client.py` | **KIS 공식 API 클라이언트** (표준라이브러리만). 토큰 발급/캐시(.kis_token.json, 1일 유효, 1분 1회 발급 제한 — 쿨다운 내장), 일봉/현재가/당일분봉/투자자수급. 토큰 무효(401/EGW00121/123) 시 자동 재발급. 분봉은 **당일 봉만**(날짜 필터 = 휴장일 가드). |
| `radar.py` | 스캐너 CLI. 스캔 소스 = 시장별 네이버 up(등락률) 랭킹뿐(`--explosion-scan-n` 기본 50). **폭발**: `--explosion-high-pct`(22) `--explosion-vol-turnover`(90=거래량/유통주식수%) `--explosion-window`(6). **재매집(반등)**: `--reignition-body-pct`(1.5=5분 양봉 몸통%) `--reignition-span-min`(5) `--reignition-min-count`(2) `--reignition-start`(1430=스파크 집계 시작) `--reaccum-change-min`(-5) `--reaccum-change-max`(7) — 14:30↑ 양봉 스파크 ≥2 AND 현재 등락률 −5~+7%. **곧 폭발 후보(youtong)**: `--youtong-change-pct`(7) `--youtong-turnover-min`(50, 상한 없음) `--youtong-start`(0930) `--youtong-spark-min`(1). `_explain_cause()`가 종목뉴스 raw 10건을 캐시(`data/material_cache`)해 `score_news`(표시뉴스)와 `score_material`(S/A/B/C/D/N 전진검증 등급)을 같이 산출한다. 분봉 UN→J 폴백=`_minute_bars_with_fallback`(reaccum 공용). 레지스트리는 오늘 라이브+지난 6일 소급 백필(`backfill_window_explosions`, 등락률상위∪레지스트리재검증, `window_scanned` 비용가드). `--reaccum-seed`(data/reaccum_seed.json) `--reaccum-max`(12) `--no-reaccum`/`--no-reaccum-visible` `--telegram-seed`/`--no-telegram-seed`/`--telegram-channel`/`--telegram-max-age`(360분). stdout JSON `{events, explosions[], youtong[], suspects[]}`. suspect에 `pattern`("reaccum")·`reignition`(5분 스파크·count)·`forecast`·`material`. 데이터 수집 장애 시 exit 3. |
| `rank_policy.py` | `rank4-v3`(현행, 발효 `20260724`) 버킷·정렬·경험칙·최근 국면 prior·모델 발효일의 코드 SSOT. radar와 backtest가 공용으로 import한다. |
| `radar_audit.py` | 회차 내 관찰 합집합, 가격·회전율·기술값·전 시간대 스파크, 복수 게이트와 API/결측 상태를 모아 scan/minute JSON으로 넘긴다. 저장 실패는 레이더 판정 밖에서 격리한다. |
| `radar_json_store.py` | 표준라이브러리 전용 로컬 JSON 저장소. 원자 쓰기, scan 불변 파일명, 분봉 병합, decision, JSONL, secret 제거, manifest 재생성·검증을 담당한다. SQL/SQLite 없음. |
| `event_calendar.py` | D-10 이벤트: `data/macro_events.json`(정적, **연 1회 수동 갱신**) + 규칙(옵션만기=둘째 목, 미 고용=첫 금). |
| `theme_map.py` | 이벤트 category(금리/반도체/환율/유가/전쟁/실적/수급) ↔ 종목 뉴스·업종 정규식 매칭. |
| `publish.py` | radar 전역순서를 유지한 채 게시컷을 적용하고 `precut/published/first/latest` 순위를 history에 원자 저장한다. 실제 게시 배열도 로컬 `published_run` 불변 scan으로 남기고 KRX 15:18·KRX close·NXT 19:50·operational EOD decision을 기준시각 이전 최신본에서 파생한다. 이후 `web/data/radar.json`을 변경 시에만 commit+push한다. |
| `telegram_notify.py` | **텔레그램 알림** (봇 `@signalpyo_bot`, 표준라이브러리만). 두 종류, 메시지·디둡파일 분리로 한 채팅서 구분: ① **재매집 5분 스파크**(`notify_reignitions`) — "🚨 …재반등 봉", **완료+신선한 봉만**(`_bar_complete`, span_min=publish가 radar params로 전달, 기본 5분 경계; 완성 후 `REIGNITION_MAX_AGE_MIN`=30분 지난 옛 봉 제외)·봉 단위 디둡(`date:code:HH:MM`, `.telegram_notified.json`). ⚠️ **`change_basis=="NXT"` 종목(마감 후 NXT 야간가로 밴드 재진입)은 통째 스킵** — reignition_bars가 전부 정규장 옛 봉이라 '신선한 재분출'이 아님(post-close 첫 회차에 15:00~15:30 봉이 ≤30분이라 뒷북 발송되던 근원 차단; 정규장 중엔 항상 KRX라 정상 알림 무영향). ② **곧 폭발 후보(youtong)**(`notify_youtong`) — "⚡ …곧 폭발 후보 / 포착 HH:MM"(현재 등락률·유통 회전율·거래대금·first_seen), **종목·일자 1회 디둡**(code 키, `.youtong_notified.json`), 최초 포착 시 1통(종일 지속이라 처음 1회만). **⚠ 기본 OFF(2026-07-02 텔레그램 개편 — 포착 시점 이미 +20% 고위험군·소음, 15:15 🎯 종베 알림으로 대체. `TELEGRAM_YOUTONG=1`로 부활)**. ⚠️ **`exploded=True`(폭발 승격해 youtong 유지 중) 종목은 스킵** — '곧 폭발'이 아니라 이미 폭발이라 의미 역전, /forecast·🔥배지로 커버. 둘 다 fail-safe(실패해도 publish 진행)·`send`/`load_env`/`_load_state` 공용. `_load_state`는 손상 파일도 빈 상태로 안전 처리. 시크릿 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`는 **Mac `.env`에만**. |
| `night_alert.py` | **NXT 시간외(야간) 급락 텔레그램 경고** (cron 16~20시 30분 간격, Mac). 오늘 레이더 후보(`web/data/radar.json` suspects) + 추적 watchlist(KV, best-effort)의 야간가(네이버 `overMarketPriceInfo`)를 정규장 종가와 대조 → **−3%↓면 텔레그램 1회 경고**(종목·일자 디둡 `.night_alert_notified.json`). `telegram_notify`의 `send`/`load_env`/`_load_state` 재사용. 가격 데이터는 네이버(시크릿 불필요), 송신만 `TELEGRAM_*`(Mac). 표시·경고 전용(점수·통계 무관). |
| `radar_backtest.py` | **자가 검증·개선** (cron 17:20). 익일 일봉 대조(적중=익일종가>신호일종가) → reaccum 후보 **마감 후 AI(Kimi) 익일예측 기록**(`ai_predict`, `RADAR_AI_PREDICT=0`로 비활성, history에 `ai_pred{prob_up,direction}`) → 점수대 보정표(n≥20) → n≥30 시 가중치 자동 튜닝(±30% bounded, `data/radar_weights.json`) → **`change_band_stats`**(등락률 구간별 익일 상승확률) → **`peak_turnover_band_stats`**(폭발일 회전율 구간별 익일 상승확률) → **`material_bands`/`material_signal_bands`**(재료등급별·재료×매우좋음/흔들기 조합별 익일 성과) → **`strategy_sim_stats`**(분할매매 실현성적, 아래) → `web/data/performance.json` → `--push`. 통계는 **raw 점수만** 사용. 25일 초과 미평가 만료. |
| `track_eval.py` | **검색 종목 📌추적 일일 검증** (cron 17:30). Upstash KV(`track:watchlist`)에서 추적 코드 읽기 → 각 종목 `/api/stock/{code}`(룰 종합판정) + `/api/stock/{code}/ai`(Kimi 상승확률) 기록 → 익일 일봉 평가 → `web/data/track_performance.json`(룰 vs AI 4분면). radar performance와 **별도 파일**. 시크릿: `KV_REST_API_URL`/`KV_REST_API_READ_ONLY_TOKEN`. |
| `ai_click_eval.py` | **AI '클릭 예측' 임계 보정** (cron 17:35). 웹 `/api/stock/{code}/ai`가 클릭 시 KV(`aipred:{date}` 해시, 종목·일자당 1건)에 적재한 상승확률을 읽어 `data/ai_click_history/{date}.json` 기록 → 익일 일봉 채점(익일종가>신호일종가) → **확률 구간 보정표 + Brier + 최적 임계 탐색**(`threshold_sweep`, 균형정확도 최대 T 권고) → `web/data/ai_click_performance.json` → `--push`. track_eval(추적목록)과 **별도 표본군**(클릭 전수). 임계 자동 적용 X — 권고치 확인 후 `ai.ts` 수동 변경(재앵커링 방지). KV 읽기 토큰만 필요. |
| `team1_collect.py` | 네이버 수집 유틸(랭킹/코드해석/종목뉴스/컨센서스). radar 재사용. ⚠️ 네이버 `transactionAmount`/`tradingVolume` 랭킹은 2026-06 폐지(404) — `up`/`down`만 동작. |
| `team2_relevance.py` | 뉴스 재료필터(별칭 매칭·호악재·중요도) + `score_material()` 재료등급(S/A/B/C/D/N) 결정론 분류. radar 재사용. |
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
  실전 rate ~20건/초(0.06초 간격). ⚠ 폭발 스캔 소스는 **네이버 up(등락률) 랭킹뿐** — 거래대금 순위
  (volume-rank `FHPST01710000`)·합집합 유니버스는 폐지(2026-06-23). 폭발은 거래대금이 아니라 거래량/유통주식수로 판정.
  - ⚠️ **시장구분(`FID_COND_MRKT_DIV_CODE`) 분리 정책 — 가격=J / 거래대금·수급=UN**:
    NXT(넥스트레이드) 거래가 종목별 과반인 경우가 많아(화신 6/19 KRX 1,685억 + NXT 2,996억 = UN 4,681억)
    KRX 단독(J)은 거래대금을 과소집계 → 폭발 게이트 false negative. 그러나 **UN 종가는 NXT 시간외가 섞여
    KRX 공식 종가와 1~6% 어긋나(실측)** MA·등락률·고가게이트·익일평가에 쓰면 왜곡된다. 그래서:
    · **가격(OHLC)은 항상 J(KRX 공식)** — `daily_prices`/`price_now` 기본 J. (예외: 분봉은 UN이지만 정규장
      시간창 가드로 NXT 장 밖 봉을 잘라 가드 통과 봉은 UN==J — 실측 J봉수==UN봉수, OHLC 왜곡 없음.)
    · **거래대금·거래량·수급(money)만 UN(통합)** — `kis_client.MONEY_MARKET`(기본 UN, `KIS_MARKET=J`로 환원).
      레이더는 `daily_prices_jmoney_un`/`price_now_jmoney_un`(J 가격 + UN 거래대금 덮어쓰기, 2콜 병합)을 사용.
    폭발 게이트의 **당일 거래량(volume)도 UN(통합)** — `price_now_jmoney_un`이 UN 거래량을 덮어쓴 값을 사용
    (유통주식수 대비 90% 회전율 판정). **분봉(reignition)도 UN** — `minute_bars_today(market=MONEY_MARKET)`,
    정규장 시간창 가드(SESSION_OPEN~CLOSE)로 NXT 장전·야간 봉 배제(가드 통과 정규장 봉은 UN==J 실측 — OHLC
    왜곡 없음). 반등은 **5분봉 양봉 스파크 횟수**만 보고 거래대금 게이트가 없어 UN/J 스케일 보정이 불필요(개편 단순화).
- **네이버**(스캔 소스·뉴스): `m.stock.naver.com/api/stocks/{up|down}/{KOSPI|KOSDAQ}?page=N&pageSize=100`,
  종목뉴스 `api/news/stock/{code}`, autocomplete `ac.stock.naver.com`. 재료등급용 종목뉴스는 `data/material_cache/YYYYMMDD.json`에 raw news만 TTL 캐시한다.
- **정적 캘린더**: `data/macro_events.json` — FOMC(확정)/CPI·금통위·삼성 잠정실적(추정).
  `estimated:true`는 추정일. **연초에 새해 일정으로 갱신 필요.**
- **Upstash KV**: 추적 watchlist(`track:watchlist`). track_eval이 읽기 토큰으로 조회.

## 게시 자동화 (cron — **Mac 프로덕션**)

`bash scripts/install_cron.sh`로 일괄 설치(idempotent, **기본 DRY**). 실발주는 테스트·계좌 대조 후
`bash scripts/install_cron.sh --live`를 명시해야 한다. 웹 OFF는 신규 매수만 중단하고 기존 포지션 청산은 유지한다. 핵심 잡:

```
1,11,21,31,41,51 9-20 * * 1-5  publish.py                 # 10분 간격, :01 오프셋(당일 폭발+재매집 게시). 9~20시=정규장+NXT 애프터마켓(마감 후 reaccum 현재 등락률을 NXT 야간가로 재평가)
20 17 * * 1-5                  radar_backtest.py --push   # 익일 적중·AI예측·strategy_sim·change_band
30 17 * * 1-5                  track_eval.py --push       # 검색 추적 종목 룰 vs AI
35 17 * * 1-5                  ai_click_eval.py --push    # AI 클릭 예측 익일 채점·임계 보정
37 17 * * 1-5                  phase_eval.py --push       # AI 국면 판정 익일 채점
5,35 16-20 * * 1-5             night_alert.py             # NXT 야간 급락(-3%↓) 텔레그램 경고(막판 포착)
```
> ⚠️ analyzer 종가베팅 잡(`analyzer/run.py`·`analyzer/backtest.py`)은 폐지됨(2026-06-23 개편).

- "변경 시에만 push"로 Vercel 무료 한도 내 안정. **PC가 켜져 있어야 함.**
- ⚠️ **cron(특히 publish 10분 간격)을 바꾸면 Mac에서 `install_cron.sh` 재실행 필요.** 인자 없는 재설치는 DRY다.
- 재매집 스파크는 5분봉이라 텔레그램 알림은 봉 완성(:05/:10/…/:00) 후 다음 publish 회차에 전송(지연 ≤~10분).
- KRX 공휴일: 분봉 날짜 필터 덕에 양봉 0 → 수상종목 0으로 안전(stale 게시 없음).

## 공개 REST API (읽기 전용)

- `GET /api/radar` — 레이더 전체 상태 `{generated_at, market_session, events[], explosions[], youtong[], suspects[], params}`.
  엣지 캐시 30초. `explosions[]`=당일 폭발 종목(/forecast), `youtong[]`=곧 폭발할 후보(/youtong). suspect에 `calibrated_prob`(raw 점수대 표본 n≥20일 때),
  `reignition`(5분 스파크·count)·`forecast`·`material`(뉴스/공시 재료등급, 정렬·자동매매 미반영) 포함.
- `/forecast` — **당일 폭발 종목** 페이지(SSG + 60초 폴링). 데이터 = `radar.json`의 `explosions[]`. 라이브 행은 현재가/등락률
  실시간, 백필 행(랭킹 밀림)은 현재가 실시간 조회·"장중 폭발(랭킹 밀림)" 배지. UI=`components/forecast/ExplosionList.tsx`.
  **순위(폭발순위기준.md, `radar.py` `_forecast_rank_key` SSOT)**: ① 회전율 90~130 밴드를 최상위(밴드 내 당일 거래대금
  내림차순) ② 130 초과는 그 아래, 회전율 오름차순(클수록 뒤로 — 저유동 품절주 펌프)·거래대금 보조. 라이브/백필 구분 없이
  순수 기준만(웹은 배열순 렌더 `rank=i+1`). 게이트(고가≥22 AND 회전율≥90)는 불변 — 90~130/130초과는 순위만 가름.
- `/youtong` — **곧 폭발할 후보**(위로 올라오며 분출) 페이지(SSG + 60초 폴링). 데이터 = `radar.json`의 `youtong[]`
  (09:30↑·현재 등락률≥7% AND 유통회전율≥50%(상한없음) AND 09:30↑ 5분봉 양봉 스파크≥1. 폭발 승격해도 유지·🔥배지). **종일 지속**
  (포착 후 마감까지 유지·"처음 포착 HH:MM" 배지·현재가 실시간). 회전율 내림차순, 앰버 액센트. 빈 상태도 유효.
  UI=`components/youtong/YoutongList.tsx`, 임계 문구는 `params.youtong_*`. **10분 publish cron 재사용**(별도 잡 없음).
  최초 포착 시 **텔레그램 알림**("⚡ …곧 폭발 후보 / 포착 HH:MM", `notify_youtong`, 종목·일자 1회 디둡 — Mac만).
- `/performance` — 자가 검증 대시보드. 데이터 = `web/data/performance.json`. 패널: TrendChart(적중률 추세),
  CalibrationTable(점수대 보정), WeightsPanel(가중치), AiPredictionPanel(AI 방향별 적중·Brier),
  **ChangeBandTable**(등락률 구간별 익일 상승확률), **PeakTurnoverBandTable**(폭발일 회전율 구간별 익일 상승확률),
  **HitBandTable**(범용 — reignition_count_bands=5분 스파크 횟수별·peak_ibs_bands=폭발일 마감강도(IBS)별 익일 상승확률, material_bands=뉴스·공시 재료등급별, material_signal_bands=재료등급×매우좋음/흔들기 조합별, 주식분석.md 가설 전진검증), **StrategySimPanel**(분할매매 실현성적),
  **TrackPerformancePanel**(검색 추적 룰 vs AI 4분면, 데이터 = `track_performance.json`),
  ThemeStatsTable·SparkFlowMatrix. 모든 패널 `min_n` 게이트.
- `GET /api/predictions` — (레거시) analyzer 종가베팅용. cron 폐지로 데이터 정체 — `/forecast`는 이제 미사용.
- `GET /api/track` — 추적 watchlist 등록/조회(KV).
- `GET /api/stock/{code}` — **온디맨드 종목 분석 리포트**(룰베이스, LLM 미사용). 네이버 공개 API 7종
  병렬 호출 → 주가·기술지표·수급·분봉 스파크·재무·재료뉴스·이벤트 민감도·종합판정. 엣지 캐시 180초.
  시크릿 불필요(KIS 미사용 — Vercel 무시크릿 유지).
  - **거래대금·거래량은 통합(KRX+NXT)** — `totalInfos`의 `accumulatedTradingValue`(`parseEok`로 억 환산)·
    `accumulatedTradingVolume`을 `price.tradingValue/tradingVolume`로 노출(레이더 카드와 동일 기준, AI 프롬프트에도 포함).
    단 가격·MA·`volumeVs20d`는 일별 candles(siseJson=**KRX 단독·공식 종가**) 기반 그대로(통합 일별 이력은 네이버 공개 API에 없음).
    **거래대금회전율은 '유통주식수 기준'**(거래대금/유통시총) — `fetchFloatRatio`(wisereport 스크랩, best-effort)로
    유동비율을 받아 `price.turnoverPct`·`floatRatio`·`turnoverBasis` 노출(실패 시 시총 기준 폴백). 카드·AI 프롬프트 반영.
  - **NXT 시간외 야간 괴리 배지** — `basic.overMarketPriceInfo`(애프터마켓 종가 `overPrice`, ~20:00)를 `price.afterMarket`로
    노출. **당일 정규장 종가 대비 %를 직접 계산**(네이버는 전일 종가 대비로 줌)해 "장 마감 후 −X%·익일 갭 주의"를
    경고(화신 6/19: KRX 14,330 → NXT 야간 13,500 = −5.8%). 정규장 중(marketStatus=OPEN)엔 비노출(전일 시간외 혼동 방지).
    표시·AI 프롬프트 전용 — 지표·평가는 KRX 종가 유지.
  ⚠ 분봉 소스 fchart(`sise.nhn?timeframe=minute`) 함정: 시/고/저 "null"(종가만 유효), **거래량은
  당일 누적값**(분당=차분 필요), ~6세션치 응답(KST 당일 필터 필수), 08:30~ 장전 봉 포함.
  스파크 = `web/lib/stock/sparks.ts`(radar.py 1:1 포팅, **산식 변경 시 동기화**).
  `GET /api/stock/search?q=` — 자동완성 프록시(ac.stock.naver.com, CSP 때문에 경유 필수).
- `GET /api/stock/{code}/ai` — **AI(LLM) 심층 분석** (Moonshot `kimi-k2.6`, LLM 사용처는 이 `/ai`와 아래 `/ask` 둘뿐).
  룰베이스 리포트 전체를 직렬화해 Kimi에 전달 → **익일 상승 확률 `prob_up`(0~100)** 추정.
  방향(상승/하락/관망)은 코드가 파생(≥54/≤46 — **임계값 프롬프트 노출 금지**, 재앵커링 방지).
  `MOONSHOT_SAMPLES`(기본 3) 병렬 호출 → **중앙값 합의**(self-consistency). 버튼 클릭 시에만 호출.
  성공 30분/에러 60초 CDN 캐시 + 쿼리스트링 차단 + in-flight 디둡.
  **클릭 예측 기록**: 응답 직전 KV에 `HSETNX aipred:{date} {code}`로 상승확률을 1건 적재(fail-safe·
  KV 미설정 시 skip → 무시크릿 동작 불변). `ai_click_eval.py`가 익일 채점·임계 보정에 사용.
  ⚠ kimi-k2.6 함정: temperature 지정 시 400(1만 허용) / 확률 0~1로 줄 때 정규화 /
  **reasoning(기본값)은 15~120초+ → Vercel 타임아웃** → `thinking:{type:"disabled"}`로 5~20초.
  `MOONSHOT_THINKING=enabled`로 깊은 추론(이때 `maxDuration=300` Fluid Compute 필요).
  시크릿: `MOONSHOT_API_KEY`(+BASE_URL/MODEL) — `web/.env.local` + Vercel.
- `GET /api/stock/{code}/phase` — **AI 국면 판정(식음 vs 고점)**. 룰베이스 게이트가 애매한 구간(폭발 직후·
  조정 중)에서 **재매집(식음 후 재상승) vs 분산(고점) vs 중립**을 판정. `lib/stock/phase.ts`가 `buildStockReport`
  (데이터)+`gatherRumors`(토론방·텔레그램 찌라시)+`serializeForPrompt`+`callKimiJson`(구조화 JSON) 재사용 —
  /ai·/ask 엔진 공유. 찌라시는 **미확인 루머**로 프롬프트에 명시(작전 허위정보 경계, 데이터·수급·뉴스 우선).
  /ai와 동일 GET+30분 CDN 캐시+in-flight 디둡. UI=`PhaseCard.tsx`(`StockReportView`에서 `verdict && !tradeStop`).
  반환 `{phase, confidence, reasons[], risks[], narrative, sourceCounts}`. 시크릿 MOONSHOT_*(무KIS).
  판정은 **3축 종합 — ①펀더멘털·가치(밸류·실적·애널 목표가, 가장 무겁게: "차트상 가격 고점 ≠ 가치 고평가",
  저평가+성장이면 분산 단정 금지) ②재료·테마 ③수급·차트.** 주봉 구조(`technical.weeklyStructure` — 일봉을
  주차 집계: 직전 8주 신고가 돌파%·종가 주봉레인지 위치(윗꼬리)·거래량 배수·이번주 진행 거래일수)도 입력
  (serializeForPrompt 공유라 `/ai`에도 반영). 펀더멘털 근거는 데이터 있을 때만(환각 금지).
- `POST /api/stock/{code}/ask` — **AI 자유질문(유통물량·재료 전문가 + 찌라시 RAG)**. 사용자 질문을 그 종목의
  실제 데이터 + 수집 글(뉴스·토론방·텔레그램)을 근거로 Kimi가 답함(`/ai`와 별개 엔드포인트).
  ⚠️ **음봉(하락)일 때 매집/흔들기/분산 판별이 핵심** — 꼬리·수급 교과서 판정은 개인 주도 테마(폭발→음봉눌림→익일 급등)를
  거꾸로 보므로, **유통회전율 역대급 + 직전 폭발 연속성 + 재료 생존**을 최우선 신호로 둠. SYSTEM_PROMPT 페르소나·판별 프레임 +
  `serializeForPrompt`(공용)의 신규 3섹션 **[시장 레짐]**(코스피/코스닥 당일 등락 — 음봉이 시장 탓인지 구분)·
  **[유통·회전율 정밀]**(거래량/유통주식수 회전율·역대 순위·백분위·누적손바뀜)·**[음봉 판별 신호]**(음봉별 꼬리·회전·수급·
  직전폭발 → 재분출후보/매집후보/분산우려/중립 라벨)로 구현. 엔진 = `lib/stock/turnover.ts`(`computeFloatTurnover`·
  `computeDownCandles`, **`scripts/float_ratio.py:vol_turnover`와 회전율 산식 동기화**) + `naver.ts`의 `fetchFloat`(유동비율+
  상장주식수)·`fetchIndex`(지수). `/ai`·`/phase`도 같은 섹션 공유.
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
- 폭발/후보 UI: `components/forecast/ExplosionList.tsx`(/forecast 당일 폭발), `components/youtong/YoutongList.tsx`(/youtong
  곧 폭발 후보). 둘 다 60초 폴링·SSG 초기값. 홈(`app/page.tsx`) 네비 카드(폭발🔥·곧 폭발⚡·성과📈).
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
- `.kis_token.json`·`.telegram_notified.json`·`.youtong_notified.json`·`.youtong_registry.json`·`open_api/`·`apikey.md`·`kimiapi.md`·`kis_devlp.yaml` 모두 gitignore.
- Vercel 시크릿은 **MOONSHOT_* + KV_REST_API_*** (서버 온리). KIS/네이버/텔레그램 키는 로컬 파이프라인 전용으로 Vercel 불필요.
