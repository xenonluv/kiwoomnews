# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 최종 갱신: **2026-06-28 /alpha 개선**(스파크 임계 2.0→1.5·세션기반 분봉·강도색/정렬·키움 속 외인 배지) · 2026-06-27 agent_alpha 사이드카 추가 (이전: 2026-06-23 폭발 정의 전면 개편 / `패치0618.md`).
> ⚠️ 폭발/식음/반등 정의는 "탐지 트랙"·"Architecture"가 현행. **격리 실험모듈 agent_alpha/·`/alpha`는 별도 섹션 참조.**

## Project Status

**"이벤트 매집 레이더"** — 10일 내 자명한 글로벌 증시 이벤트(FOMC·CPI·실적)를 앞두고 **큰돈이
들어와 매집·재반등이 의심되는 종목**을 자동 탐지해 웹에 게시하는 시스템. 순수 Python 파이프라인
(레이더 본체는 LLM 미사용)이 데이터를 만들고, Next.js 사이트가 Vercel에 라이브
(https://stocknews-cyan.vercel.app, `xenonluv/stocknews` push 시 자동 재배포, Root Directory=`web`).

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
  당일 폭발(signal_date==peak_date)은 `/forecast`에만, 수상종목은 '과거 폭발 + 오늘 재분출'. **🎯 매수급소(2026-07-03 회장님 지시)**: ① 추적 풀 확장 — 폭발 후 6일 지나도 **20일선 위인 동안 계속 추적**(`--reaccum-track-days` 30 캡, `_extended_track` 플래그·MA20 아래로 깨지면 탈락) ② **당일 14:30↑ 몸통 ≥2.0% 양봉 스파크 ≥2회(`--geupso-body-pct`/`--geupso-min-count`) = 매수급소** — 등락률 밴드(−5~+7%) **무제한**(밴드 밖은 급소만 통과, 14:30 전엔 기존 조기 컷 유지). suspect에 `geupso`/`geupso_bars` 적재, suspects 정렬 급소 최상단, 웹 SuspectCard 대형 🎯 배지, 텔레그램 제목 "🎯 매수급소"로 구분. 근거: 덕신(2%+ 스파크 5.67/3.11% 2개=익일 폭락에도 매집 받침) vs 상지건설(1.51% 1개=받침 없음, 7/1 상한가→식음) 실측 — 스파크는 '큰손이 아직 있다'는 지문, 그 지문이 찍힌 날이 매수 급소.
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
[조건 가점] event_calendar(D-10 정적 캘린더+규칙) × theme_map(뉴스·업종 테마 매칭)
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
| `radar.py` | 스캐너 CLI. 스캔 소스 = 시장별 네이버 up(등락률) 랭킹뿐(`--explosion-scan-n` 기본 50). **폭발**: `--explosion-high-pct`(22) `--explosion-vol-turnover`(90=거래량/유통주식수%) `--explosion-window`(6). **재매집(반등)**: `--reignition-body-pct`(1.5=5분 양봉 몸통%) `--reignition-span-min`(5) `--reignition-min-count`(2) `--reignition-start`(1430=스파크 집계 시작) `--reaccum-change-min`(-5) `--reaccum-change-max`(7) — 14:30↑ 양봉 스파크 ≥2 AND 현재 등락률 −5~+7%. **곧 폭발 후보(youtong)**: `--youtong-change-pct`(7) `--youtong-turnover-min`(50, 상한 없음) `--youtong-start`(0930) `--youtong-spark-min`(1) — 싼 게이트는 up 랭킹 루프서 수집, `prepare_youtong`이 09:30↑ 5분봉 양봉 스파크로 확정 + `.youtong_registry.json`로 종일 지속(현재가 재조회). 분봉 UN→J 폴백=`_minute_bars_with_fallback`(reaccum 공용). 레지스트리는 오늘 라이브+지난 6일 소급 백필(`backfill_window_explosions`, 등락률상위∪레지스트리재검증, `window_scanned` 비용가드). `--reaccum-seed`(data/reaccum_seed.json) `--reaccum-max`(12) `--no-reaccum`/`--no-reaccum-visible` `--telegram-seed`/`--no-telegram-seed`/`--telegram-channel`/`--telegram-max-age`(360분). stdout JSON `{events, explosions[], youtong[], suspects[]}`. suspect에 `pattern`("reaccum")·`reignition`(5분 스파크·count)·`forecast`. 데이터 수집 장애 시 exit 3. |
| `event_calendar.py` | D-10 이벤트: `data/macro_events.json`(정적, **연 1회 수동 갱신**) + 규칙(옵션만기=둘째 목, 미 고용=첫 금). |
| `theme_map.py` | 이벤트 category(금리/반도체/환율/유가/전쟁/실적/수급) ↔ 종목 뉴스·업종 정규식 매칭. |
| `publish.py` | radar → `web/data/radar.json` → 변경 시에만 commit+push. flock 락, `--dry-run`, `--max`. radar 인자 그대로 전달. `explosions[]`·`youtong[]`은 radar.py 출력 그대로 패스스루. 매 회차 `data/radar_history/`에 검증용 이력(raw 점수) 기록. 게시 후보 중 **재반등 봉 발생 시 `telegram_notify.notify_reignitions` 호출** + **youtong 진입 시 `notify_youtong` 호출**(각각 별도 try·토큰 없으면 조용히 skip. ⚠ youtong 알림은 **기본 OFF** — 2026-07-02 텔레그램 개편(포착 시점 이미 +20% 고위험군), `TELEGRAM_YOUTONG=1`로 부활. 🚨 재반등 스파크는 유지 — 14:30 이후에만 발생하는 회장님 핵심 신호). |
| `telegram_notify.py` | **텔레그램 알림** (봇 `@signalpyo_bot`, 표준라이브러리만). 두 종류, 메시지·디둡파일 분리로 한 채팅서 구분: ① **재매집 5분 스파크**(`notify_reignitions`) — "🚨 …재반등 봉", **완료+신선한 봉만**(`_bar_complete`, span_min=publish가 radar params로 전달, 기본 5분 경계; 완성 후 `REIGNITION_MAX_AGE_MIN`=30분 지난 옛 봉 제외)·봉 단위 디둡(`date:code:HH:MM`, `.telegram_notified.json`). ⚠️ **`change_basis=="NXT"` 종목(마감 후 NXT 야간가로 밴드 재진입)은 통째 스킵** — reignition_bars가 전부 정규장 옛 봉이라 '신선한 재분출'이 아님(post-close 첫 회차에 15:00~15:30 봉이 ≤30분이라 뒷북 발송되던 근원 차단; 정규장 중엔 항상 KRX라 정상 알림 무영향). ② **곧 폭발 후보(youtong)**(`notify_youtong`) — "⚡ …곧 폭발 후보 / 포착 HH:MM"(현재 등락률·유통 회전율·거래대금·first_seen), **종목·일자 1회 디둡**(code 키, `.youtong_notified.json`), 최초 포착 시 1통(종일 지속이라 처음 1회만). **⚠ 기본 OFF(2026-07-02 텔레그램 개편 — 포착 시점 이미 +20% 고위험군·소음, 15:15 🎯 종베 알림으로 대체. `TELEGRAM_YOUTONG=1`로 부활)**. ⚠️ **`exploded=True`(폭발 승격해 youtong 유지 중) 종목은 스킵** — '곧 폭발'이 아니라 이미 폭발이라 의미 역전, /forecast·🔥배지로 커버. 둘 다 fail-safe(실패해도 publish 진행)·`send`/`load_env`/`_load_state` 공용. `_load_state`는 손상 파일도 빈 상태로 안전 처리. 시크릿 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`는 **Mac `.env`에만**. |
| `night_alert.py` | **NXT 시간외(야간) 급락 텔레그램 경고** (cron 16~20시 30분 간격, Mac). 오늘 레이더 후보(`web/data/radar.json` suspects) + 추적 watchlist(KV, best-effort)의 야간가(네이버 `overMarketPriceInfo`)를 정규장 종가와 대조 → **−3%↓면 텔레그램 1회 경고**(종목·일자 디둡 `.night_alert_notified.json`). `telegram_notify`의 `send`/`load_env`/`_load_state` 재사용. 가격 데이터는 네이버(시크릿 불필요), 송신만 `TELEGRAM_*`(Mac). 표시·경고 전용(점수·통계 무관). |
| `radar_backtest.py` | **자가 검증·개선** (cron 17:20). 익일 일봉 대조(적중=익일종가>신호일종가) → reaccum 후보 **마감 후 AI(Kimi) 익일예측 기록**(`ai_predict`, `RADAR_AI_PREDICT=0`로 비활성, history에 `ai_pred{prob_up,direction}`) → 점수대 보정표(n≥20) → n≥30 시 가중치 자동 튜닝(±30% bounded, `data/radar_weights.json`) → **`change_band_stats`**(등락률 구간별 익일 상승확률 — `change_basis=="NXT"` 야간가 표본은 제외, hit이 KRX 종가 기준이라 기준 불일치 방지; 구표본 None=KRX) → **`peak_turnover_band_stats`**(폭발일 회전율 구간별 익일 상승확률 — peak_turnover 비중 검증, reaccum 실험 풀) → **`strategy_sim_stats`**(분할매매 실현성적, 아래) → `web/data/performance.json` → `--push`. 통계는 **raw 점수만** 사용. 25일 초과 미평가 만료. |
| `track_eval.py` | **검색 종목 📌추적 일일 검증** (cron 17:30). Upstash KV(`track:watchlist`)에서 추적 코드 읽기 → 각 종목 `/api/stock/{code}`(룰 종합판정) + `/api/stock/{code}/ai`(Kimi 상승확률) 기록 → 익일 일봉 평가 → `web/data/track_performance.json`(룰 vs AI 4분면). radar performance와 **별도 파일**. 시크릿: `KV_REST_API_URL`/`KV_REST_API_READ_ONLY_TOKEN`. |
| `ai_click_eval.py` | **AI '클릭 예측' 임계 보정** (cron 17:35). 웹 `/api/stock/{code}/ai`가 클릭 시 KV(`aipred:{date}` 해시, 종목·일자당 1건)에 적재한 상승확률을 읽어 `data/ai_click_history/{date}.json` 기록 → 익일 일봉 채점(익일종가>신호일종가) → **확률 구간 보정표 + Brier + 최적 임계 탐색**(`threshold_sweep`, 균형정확도 최대 T 권고) → `web/data/ai_click_performance.json` → `--push`. track_eval(추적목록)과 **별도 표본군**(클릭 전수). 임계 자동 적용 X — 권고치 확인 후 `ai.ts` 수동 변경(재앵커링 방지). KV 읽기 토큰만 필요. |
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

## agent_alpha 사이드카 (전진수집기 + LLM 알파) — 격리 모듈

> 추가: **2026-06-27**. 코어와 **완전 격리된 별도 폴더** `agent_alpha/`. 오너의 실제 매매신호
> (**14:30↑ 5분봉 양봉 스파크 + 거래원 키움집중 + 투자자별 교차 + 유통회전율**)는 과거 데이터가 없어
> 백테스트 불가(157k 백테스트가 "고회전 음봉→익일상승" 극단을 반증) → **오늘부터 라이브 적재해 전진검증**하는
> 게 유일한 길. 거기에 LLM 재료·찌라시 추론을 결합하고 `/alpha`에서 **실측 익일확률**로 가시화한다.

**핵심 축 = 유통주식 회전율(거래량/유통주식수).** "고회전=상승"이 아니라(반증됨) **회전율을 스파크·받힘·수급·
거래원과 교차해 "어떤 조합일 때 통하나"를 측정**하는 게 목적. 모든 산출물은 `score_raw=0`과 동급 — **표시·실험
전용, core 적중률·가중치 튜닝에 절대 미반영**.

⚠️ **격리/삭제안전 원칙 (불변):**
1. 코어 `scripts/`·기존 web 파일은 agent_alpha를 **절대 참조 안 함** — `grep -rn agent_alpha scripts` = **빈결과**.
2. agent_alpha는 `scripts/kis_client.py`·`scripts/float_ratio.py`를 **읽기전용 import**만(Option A — 토큰캐시·레이트 공유).
3. agent_alpha 쓰기는 `agent_alpha/data/`에만(gitignore). **유일 예외 = `publish_alpha.py`가 `web/data/alpha.json` 생성/커밋/푸시.**
   코어 `data/float_ratio.json`은 `get_float_and_listed(code, cache=)`에 자체 캐시 dict를 넘겨 디스크쓰기 회피.
4. **삭제** = `rm -rf agent_alpha` + cron 블록(`install_cron.sh --uninstall`) + 웹신규파일(`web/{app/alpha,components/alpha,
   lib/alpha,app/api/alpha,types/alpha.ts,data/alpha.json}`) 삭제. 코어 기능 그대로.

| 파일 | 역할 |
|------|------|
| `config.py` | sys.path 부트스트랩(agent_alpha/·sources/·agents/·repo/scripts) + 상수(스파크 14:30·몸통1.5%·≥2회, turnover_2d/종가강도 밴드, `CALIB_MIN_N`=20) + 경로(forward/judgments/calibration/float캐시/notified/alpha.json) + `today_yyyymmdd`/`now_iso`/`ensure_dirs`. |
| `kis_extra.py` | **거래원(증권사 창구) 조회** — 코어에 없는 TR `FHKST01010600`을 `_call`로 추가(읽기전용). 매수/매도 상위 5창구 + 키움 집중도 + 외국계 순매수(`glob_yn`). 당일 스냅샷만(과거 불가). **거래원 행 전무 시 0.0 날조 대신 `None`**(원칙: 창구≠주체, 약신호). |
| `sources/movers.py` | 코어 `radar`/registry에서 당일 폭발·youtong·reaccum mover 풀 수집(읽기전용). |
| `sources/sparks_min.py` | 코어 `radar.aggregate_minute_bars`/`reignition_bars`/`_has_live_bars` 미러 + UN→J 분봉 폴백. `spark_1430(code)`→(count,max_body,bars,source). 몸통%≥`SPARK_BODY_PCT`(1.5). ⚠ **분봉은 `_session_bars`로 '가장 최근 거래세션' 기준 수집**(코어 `minute_bars_today`의 벽시계 today 필터 대신 응답의 최근 거래일로 필터) — agent_alpha는 EOD 전진수집기라 quant의 row date(=마지막 일봉)와 같은 거래일 분봉을 봐야 일관. **주말·마감 한참후 수집에도 금요일 분봉으로 스파크 산정**(today 필터면 통째로 비어 source="none" 미측정 됨). 코어 `minute_bars_today`(실전 stale 가드)는 무수정. source="none"=진짜 라이브봉 없음(상폐·정지 등). |
| `sources/quant.py` | **(code,date) 정량행** — 캔들(종가강도·실꼬리)·**유통회전율(turnover_pct·turnover_2d_pct)**·14:30스파크·투자자별·거래원·레짐. 결측은 **전부 null(날조 금지)** — OHLC 0·investor 전부0·거래원 빈응답을 가짜값으로 안 채움. **`hidden_foreign_level`(키움 속 외인매집 강도 0~3·null=결측)·`combined_score`(스파크+외인매집 합산)를 SSOT로 산출·저장** — 웹/calibrate는 이 저장값을 읽음(산식 단일화). |
| `sources/news.py`·`rumors.py`·`regime.py` | 종목뉴스·찌라시(토론방/텔레그램, 미확인 루머)·시장레짐(코스피/코스닥 등락). best-effort. |
| `agents/llm.py` | Moonshot(kimi) 최소 클라이언트(urllib·thinking disabled·json_object·temperature 금지). 키 없으면 no-op. |
| `agents/analyst.py` | 1차 LLM 판단 — catalyst·real_likelihood·sustainability·manipulation_risk·prob_up·confidence·evidence. `_p01`이 0~1/0~100 양규약 수용(코어 ai.ts 정합). |
| `agents/redteam.py` | 적대적 검증 — 찌라시가 개미 유인 작전/허위 덫인가 → `redteam_flag`(문자열 'false' 오판 방지 안전파싱). |
| `collect.py` | **EOD 수집(cron 15:10 장중 잠정·15:40 마감후 확정)** — mover 풀 → quant 행 → `data_ok` 필터 → `forward/{sig_date}.json`(미라벨). sig_date=행 date 최빈값. (15:10은 15:15 잠정게시가 수집 완료 후 돌도록 5분 선행 — 회장님 종가베팅 매수시간 확보 지시 2026-07-01.) |
| `loop.py` | **장중 LLM 루프(cron 10분)** — mover에 analyst/redteam judgment 결합 → judgments + 고신뢰 텔레그램. |
| `label.py` | **익일 라벨(cron 09:10)** — forward 미라벨 행에 익일봉(J 공식) 종가·hit(익일종가>신호종가)·next_return 채움. 윈도(40거래일) 신호일 미커버 시 보류, 30일+ 미라벨은 만료(hit=None). `forward_samples.jsonl` 재생성. |
| `calibrate.py` | **채점·보정(cron 17:45)** — 라벨 표본으로 정량밴드(turnover_2d × 스파크 × 종가강도 × 음봉)별 **실측 익일확률** + LLM Brier → `calibration.json`. **min_n=20 게이트**(부족 셀 "관찰중", 전 셀 보고=체리피킹 금지). 스파크축은 측정행(source≠none)만. 축: `by_turnover2d_eumbong`·`by_spark_eumbong_hi_turnover`·`by_close_strength_eumbong`·**`by_spark_count`(14:30 스파크 횟수 단독·전체)**·**`by_hidden_foreign`(키움 속 외인매집 해당/미해당)**·**`by_combined_score`(레거시 합산점수 밴드)**·**`by_change_pct`/`by_mover_type`/`by_close_bet_band`/`by_close_bet_rank`(종베 v4 정렬 검증 — 순위축 정렬키는 웹과 1:1: 점수desc·value_eok desc·code)**·**`by_value_band`/`by_spark_strength`(무·약<3%·강≥3%)/`by_liquidity_deficit`(v4 채점축 대칭 관찰축)**·`cells`. hidden_foreign/combined은 quant 저장값(SSOT) 우선·옛 행만 재계산(단 close_bet_fitness는 **항상 재계산** — 산식 개정 시 stale 방지). **모든 셀 `_stat`에 종가베팅 가정 익일고가 지표: `avg_high`(종가 대비 평균 익일 고가 등락)·`touch7_rate`(익일 +7% 고가 터치율=익절 도달%)**. 신규 축은 데이터 소급(forward에 원천 다 적재됨, label.py가 `next_high_pct` 기록). |
| `notify.py` | 자체 텔레그램 "🧠 [알파]" — 고신뢰(confidence≥0.6) 판단만 종목·일자 1회 디둡. 코어 `telegram_notify.send`/`load_env` 재사용. 기존 🚨/⚡와 분리. ⚠ **기본 OFF**(2026-07-02 텔레그램 개편 — 장중 소음 제거, `ALPHA_LLM_TELEGRAM=1`로 부활). |
| `notify_close_bet.py` | **🎯 [종베] 오늘의 TOP 후보 텔레그램** (2026-07-02 개편의 신설 핵심 알림). publish_alpha.run()이 게시 직후 호출(별도 cron 없음 — 15:15 잠정·15:43 확정 잡 재사용). 오늘 movers를 **웹과 1:1 정렬**(fitness desc·value_eok desc·code)로 TOP2 발송(부적합<45 제외, 전원 부적합이면 "오늘 후보 없음" 1통). **시간게이트 14:55~16:00**(17:47 보정 실행 차단, `CLOSE_BET_FORCE=1` 테스트 우회) + 구성서명 디둡(`data/.close_bet_notified.json`) — 15:43 실행은 TOP 구성이 바뀐 경우만 "🔄 확정 변경" 후속 1통. 폭락제외 발동 수·"익일 +7% 익절/−5% 손절·매수추천 아님" 고정 표기. fail-safe(실패해도 publish 진행). |
| `alert_watch.py` | **KRX 시장경보 감시·예측** (회장님 지시 2026-07-03). `alert_now`=네이버 marketAlertType(주의/경고/위험 현재 지정) + `forecast_warning`=KIND 공시 공식(5일 +60%·15일 +100%·3일 +100%·15일 신고가·지수 배수 5x/3x)으로 **마감 직전 "내일 경고예고/지정 뜰지" 계산**(15:10 수집가=가상 종가). 무경보 상태면 "경고예고 예상"으로 캡(KRX 절차). quant가 `alert_now`/`alert_forecast` 저장 → /alpha 카드 대형 배지(⚠️투자주의·🚨경고예고 예상) + 15:15 종베 텔레그램 표기. **점수·정렬 무반영**(정보 배지 — 폭락 시 추가매수 기회 관점). 투자주의(1단계)는 계좌 기반 유형이라 예측 불가(현재 상태만). 덕신이피씨 7/2 소급 재현 검증(5일 +62% → 예측 적중). fail-safe. |
| `publish_alpha.py` | **web 게시(cron 15:15 잠정·15:43 확정·17:47 보정)** — **최신 1거래일(오늘) forward만**(`RECENT_DAYS=1` — 어제·오늘 혼재 방지, 회장님 지시 2026-06-29) + calibration → `web/data/alpha.json`. 익일결과 라벨·검증은 calibrate(forward_samples 전체)가 담당. 변경판정=**git HEAD 커밋본 기준**, pathspec 한정 commit + `GIT_LOCK` 공유 직렬화 + 변경 시에만 push. `--dry-run` 미기록. 게시 직후 `notify_close_bet.notify()` 호출(🎯 종베 TOP 후보 텔레그램 — fail-safe). |
| `install_cron.sh` | agent_alpha 자체 cron 설치/제거(`# AGENT_ALPHA_BEGIN/END` 네임스페이스 블록, 멱등·`--uninstall`·`--dry-run`). 코어 cron 무손상. |

- **전진수집 레코드**: `(code,date)` 1행 — 캔들·**유통회전율(turnover_pct·turnover_2d_pct)**·14:30스파크(count·max_body·source)·
  투자자별(frgn/orgn/prsn_net)·거래원(kiwoom_buy_concentration·glob_net_qty·top_buyers/sellers)·레짐·LLM 판단·**익일라벨**.
- **정직한 한계(UI에도 박음)**: 전진데이터 **수 주~수개월** 쌓여야 결론 — 그 전 `/alpha` calibration은 "관찰중(n부족)".
  스파크는 **미증명 가설**. 거래원=EOD 스냅샷·창구≠주체. 단일레짐·공개신호 엣지 얇음 → 안 되면 `rm -rf`.

```bash
python3 agent_alpha/collect.py                  # EOD 수집(당일 분봉 필요·시크릿 필요)
python3 agent_alpha/publish_alpha.py --dry-run  # alpha.json 미리보기(미기록)
python3 agent_alpha/label.py                    # 익일 라벨 + forward_samples.jsonl 재생성
python3 agent_alpha/calibrate.py                # calibration.json 산출
bash    agent_alpha/install_cron.sh --dry-run   # 설치될 cron 블록 미리보기
```

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
  종목뉴스 `api/news/stock/{code}`, autocomplete `ac.stock.naver.com`.
- **정적 캘린더**: `data/macro_events.json` — FOMC(확정)/CPI·금통위·삼성 잠정실적(추정).
  `estimated:true`는 추정일. **연초에 새해 일정으로 갱신 필요.**
- **Upstash KV**: 추적 watchlist(`track:watchlist`). track_eval이 읽기 토큰으로 조회.

## 게시 자동화 (cron — **Mac 프로덕션**)

`bash scripts/install_cron.sh`로 일괄 설치(idempotent). 핵심 잡:

```
1,11,21,31,41,51 9-20 * * 1-5  publish.py                 # 10분 간격, :01 오프셋(당일 폭발+재매집 게시). 9~20시=정규장+NXT 애프터마켓(마감 후 reaccum 현재 등락률을 NXT 야간가로 재평가)
20 17 * * 1-5                  radar_backtest.py --push   # 익일 적중·AI예측·strategy_sim·change_band
30 17 * * 1-5                  track_eval.py --push       # 검색 추적 종목 룰 vs AI
35 17 * * 1-5                  ai_click_eval.py --push    # AI 클릭 예측 익일 채점·임계 보정
37 17 * * 1-5                  phase_eval.py --push       # AI 국면 판정 익일 채점
5,35 16-20 * * 1-5             night_alert.py             # NXT 야간 급락(-3%↓) 텔레그램 경고(막판 포착)
```
> ⚠️ analyzer 종가베팅 잡(`analyzer/run.py`·`analyzer/backtest.py`)은 폐지됨(2026-06-23 개편).

**agent_alpha 자체 cron** (`bash agent_alpha/install_cron.sh`, 네임스페이스 블록 — 코어 cron 무수정·무손상):

```
1,11,…,51 9-15 * * 1-5  agent_alpha/loop.py            # 장중 LLM 판단(재료·찌라시·조작) → judgments
10,40 15 * * 1-5        agent_alpha/collect.py         # EOD 전진수집(15:10 장중잠정·15:40 마감확정, 당일 분봉 필요)
10 9  * * 1-5           agent_alpha/label.py           # 익일 라벨(다음 거래일 아침)
45 17 * * 1-5           agent_alpha/calibrate.py       # 채점·보정 → calibration.json
15,43 15 / 47 17 * * 1-5 agent_alpha/publish_alpha.py  # /alpha 게시(15:15 잠정·15:43 확정·17:47 보정, 변경 시에만 push)
```
> 제거: `bash agent_alpha/install_cron.sh --uninstall`. 로그: `/tmp/agent_alpha_*.log`. cron이 `/usr/bin/python3`(3.9) 사용.

- "변경 시에만 push"로 Vercel 무료 한도 내 안정. **PC가 켜져 있어야 함.**
- ⚠️ **cron(특히 publish 10분 간격)을 바꾸면 Mac에서 `install_cron.sh` 재실행 필요.**
- 재매집 스파크는 5분봉이라 텔레그램 알림은 봉 완성(:05/:10/…/:00) 후 다음 publish 회차에 전송(지연 ≤~10분).
- KRX 공휴일: 분봉 날짜 필터 덕에 양봉 0 → 수상종목 0으로 안전(stale 게시 없음).

## 공개 REST API (읽기 전용)

- `GET /api/radar` — 레이더 전체 상태 `{generated_at, market_session, events[], explosions[], youtong[], suspects[], params}`.
  엣지 캐시 30초. `explosions[]`=당일 폭발 종목(/forecast), `youtong[]`=곧 폭발할 후보(/youtong). suspect에 `calibrated_prob`(raw 점수대 표본 n≥20일 때),
  `reignition`(5분 스파크·count)·`forecast` 포함.
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
- `GET /api/alpha` — **agent_alpha 사이드카** 출력 `web/data/alpha.json` 패스스루(`lib/alpha/repository.ts` SSOT). 엣지 캐시.
  `{generated_at, date, movers[], calibration, disclaimer}`. mover에 `file_date`(출처 파일 sig_date·React key축)·유통회전율
  ·14:30스파크·거래원·LLM 판단·익일라벨. **코어 `radar`와 무관**(별도 데이터·삭제안전). 위 "agent_alpha 사이드카" 참조.
- `/alpha` — **알파 전진검증** 페이지(SSG + 60초 폴링). 데이터 = `alpha.json`. mover 카드(유통회전율 2일·종가강도·14:30스파크·
  거래원 키움집중·외인/기관 교차·LLM catalyst/조작위험·prob_up·익일결과) + **CalibrationPanel**(turnover_2d×스파크×받힘×음봉
  + **14:30 스파크 횟수 단독** + **🕵 키움 속 외인매집(해당/미해당)** + **종합점수(스파크+외인매집)별 — 정렬 순위 검증** 실측 익일확률·**평균 익일고가·+7% 익절터치율**(종가베팅→다음날 고가 도달폭), **min_n 게이트**=부족 셀 "관찰중"). 종목 익일배지에 종가등락 + **고가 도달폭**(`next_high_pct`) 표시.
  UI=`components/alpha/AlphaList.tsx`. 빈 상태 유효. **측정·실험·매수추천 아님**.
  - **📋 어제 결과 섹션**(오늘 종목과 분리): 직전 거래일(`yesterday_results`) 종목 + 익일 등락·고가·적중(라벨 완료분).
    label cron이 **마감 후 16:00**에 전일 신호 결과를 즉시 채점(`_next_bar` inc_today≥1600=종가 확정 후) → 16:00경 자동 갱신. 익일등락 내림차순.
  - **종가베팅 적합도 v4 정렬·색**(산식 SSOT=`agent_alpha/fitness.py:close_bet_fitness`, 웹 미러=`closeBetFitness` — **1:1 동기화 필수**): 카드 정렬=**점수(0~100) 내림차순 · 동점 거래대금(value_eok) 내림차순 · code** (calibrate `by_close_bet_rank`와 동일 키 — 구 |회전2d−115| 타이브레이크는 스윗스팟 유산이라 폐기). 점수=기준 50 + 가감: **유형**(reaccum +10 / youtong 0 / **explosion −50** — 실행성 벌점: 상한가류 종가 체결불가·익일 갭 리스크, 고가터치 통계는 기저급) + **유동성결핍**((대금<50억 OR 2일회전<40%) → **−15 한 번만** — 씨피시스템 함정, 이중처벌 금지) + **거래대금**(≥1000억 +10 — 실증 최강 +13.2%p·LODO 4/4) + **당일등락**(≤−10% 깊은눌림 +15=유일한 실증 가점(터치 100%) / 0~+8% +12 / −10~0 +8 / +8~15 −20=실증 데드존(−41.7%p) / +15~22 −30 / +22%+ −40 — +15%↑는 실행성(추격불가) 근거, 회장님 지시) + **스파크 세기**(최대몸통 0<x<3% '약스파크' **−8** — 최견고 음신호(−16.8%p·LODO 0/4) / **강스파크(3%↑) 2회↑ +8** — 회장님 지시 2026-07-02 "고가를 먹는 프로그램": 과거 4/4 익일 +7% 터치·+21.9%p, 단 종가는 2/4 참사=순수 익절 신호·n=4 소표본 / 강1회·무스파크 0 — 무>강 관측, 서열 판정은 `by_spark_strength`(무/약/강1/강2+) 관찰축 성숙 후. **탐지 게이트(1.5%)와 랭킹 신호(세기)의 임계 분리**) + **강마감**(종가강도≥0.6 −5) + **숨은외인**(lv≥1 −5) + **20일선**(현재가 < 일봉 20일선(역배열) **−20** — 회장님 지시 2026-07-03 "적어도 20일선 위". 과거 38표본 전원 위=오폭 0건, 첫 위반자 희림 7/2가 7/3 하락 실증. `ma20_gap_pct`, 이력<20일 신규상장은 결측 통과. `by_ma20` 관찰축 전진검증) + **폭락제외**(과확장붕괴: run_6d≥+100% & 당일음수 −30 / 연속하락 4일↑ −15 — 2026-07-02 회장님 지시 "폭락 중인 종목 상위 차단"(금호건설 5연상붕괴·광주신세계 4일 연속하락 사례). 39표본 오폭 0건 검증, 승자군 최대 연속하락 3일. ⚠ peak_dd(고점 이탈폭)는 승자들이 −26~−47%에서 나와 **점수 미반영·관찰 전용**). 2일회전율 밴드 가감은 전삭제(지그재그=노이즈). 색=75+적합·빨강굵게/60+중간·주황/45+약·앰버/그외 부적합·회색. 순위 배지는 **부적합(<45) 종목이면 1~3위여도 강조색 미적용**(티어 우선·과신 방지), 헤더에 "순위상관≈0 — 하위권 회피용" 명시. ⚠ **2026-07-02 4각도 감사+2인 심판 "수정승인" 판결 반영(39표본·4거래일). 순열검정상 우연통과율 97.9% — 어떤 축도 통계 검증 아님, 재앙회피용 잠정 휴리스틱. ~07/25 표본 성숙 전 튜닝 동결.** 관찰축 `by_value_band`/`by_spark_strength`/`by_liquidity_deficit`가 채점축을 자동 전진검증.
  - **🕵 키움 속 숨은 외국인 매집 배지**(`hiddenForeign`): 투자자별 **외인 순매수(+) AND 외국계창구 순매수<외인×10% AND 키움 매수집중≥30%** → 외국인이 외국계 창구를 안 거치고 키움 등 리테일 창구로 숨어 매집한 흔적(의심). 외인 순매수 규모로 밝은 오렌지 강도색(10만+진한/3만+중간/그외 연한). **"창구≠주체" 한계 명시(툴팁)·거래원/투자자 결측 시 미표시**(날조 방지). 표시 전용(통계 무관).
- `/performance` — 자가 검증 대시보드. 데이터 = `web/data/performance.json`. 패널: TrendChart(적중률 추세),
  CalibrationTable(점수대 보정), WeightsPanel(가중치), AiPredictionPanel(AI 방향별 적중·Brier),
  **ChangeBandTable**(등락률 구간별 익일 상승확률), **PeakTurnoverBandTable**(폭발일 회전율 구간별 익일 상승확률),
  **HitBandTable**(범용 — reignition_count_bands=5분 스파크 횟수별·peak_ibs_bands=폭발일 마감강도(IBS)별 익일 상승확률, 주식분석.md 가설 전진검증), **StrategySimPanel**(분할매매 실현성적),
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
  곧 폭발 후보). 둘 다 60초 폴링·SSG 초기값. 홈(`app/page.tsx`) 네비 카드(폭발🔥·곧 폭발⚡·알파🧠·성과📈).
- 알파 UI: `components/alpha/AlphaList.tsx`(/alpha 전진검증 mover 카드 + CalibrationPanel). 60초 폴링·SSG. **agent_alpha 사이드카**
  전용(코어 무관·삭제안전, 위 "agent_alpha 사이드카" 참조). 빈/관찰중 상태 유효, "측정·실험" 면책.
  종가베팅 적합도순 정렬·색·점수배지·근거칩(`closeBetFitness`/`fitnessTier`) + 키움 속 숨은 외국인 매집 밝은 오렌지 배지(`hiddenForeign`/`HF_BADGE`) — 위 `/alpha` API 항목 참조. 모두 웹 표시 전용(alpha.json·정렬 데이터 무수정).
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
