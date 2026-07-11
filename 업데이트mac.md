# 🍎 Mac 업데이트 매뉴얼 (stocknews)

> **이 문서 = 최초 설치(`맥설치매뉴얼.md`) 이후 추가된 기능 + 업데이트 적용법.**
> 아직 한 번도 설치 안 했다면 먼저 `맥설치매뉴얼.md`로 기본 설치(git·.env·SSH·시간대·잠자기)부터 하세요.
> 이미 한 번 설치/운영 중이라면, **이 문서의 2번 한 줄이면 업데이트 끝**입니다.

---

## 1. 무엇이 새로 추가됐나 (업데이트 내용)

### A. 사이트(실시간 시그널) 개선 — `git pull`만으로 반영(Vercel 자동배포)
- **거래대금 500억 미만 종목 제외** (잡주 필터).
- **종목명 옆 당일 등락률** 표시(상승=빨강/하락=파랑).
- **시그널 카드 = 세련된 블루 글래스 + 상시 후광**, 후보 카드와 구분.
- **"상승추세" 국면 신설**(정배열+과열직전을 후하게 평가).
- **실시간 갱신 상태바**: 장중이면 "🟢 실시간 갱신 중(15분)", 장외면 "⏸ 장 마감".

### B. 🎯 내일 상승 예측 (신규 분석 에이전트 `analyzer/`)
- **오늘 종가 베팅 → 내일 오를 확률 높은 종목**을 예측하는 별도 프로그램.
- **기존 시스템 무영향**: 기존 REST API를 재사용(네이버 초과호출 방지), 결과는 신규 파일 `web/data/predictions.json`에만 기록.
- 구성: 종목수집(`collect`) → 기술지표(`indicators`: MACD/RSI/Stochastic/일목, **코드로 계산**) → 재료(`sentiment`) → 종합(`run`) → 적중률·보정(`backtest`).
- **새 웹 페이지 `/forecast`**: 장중 잠정 랭킹 + 14:20 종가베팅 확정 후보(진입가·목표·**손절가**·근거). 홈 상단 배너로 진입.
- **공개 API `/api/predictions`** (CORS 허용 — 다른 코드/브라우저에서 사용 가능).
- **적중률 검증 루프**: 매일 예측 ↔ 익일 실제를 대조해 적중률을 사이트에 공개하고, 표시 확률을 실제 적중률로 보정.

### C. 운영 안정화
- `publish.py`에 **pull-before-push** 추가 → 여러 머신/PC가 코드를 올려도 충돌 없이 공존.
- **레이트리밋·재시도**(net.py) → 네이버 24h 호출 안정.
- **cron 자동설치 스크립트**(`scripts/install_cron.sh`) → 명령 한 줄로 모든 자동화 설정.

---

## 2. 업데이트 적용 (Mac에서 — 핵심)

터미널에서:
```bash
cd ~/stocknews
git pull                      # 최신 코드 받기(analyzer 포함). 사이트 개선은 Vercel이 자동 배포.
bash scripts/install_cron.sh  # cron 일괄 설치(idempotent, 자동매매 기본 DRY)
```
- `git pull`: 새 코드(`analyzer/`, publish 보강 등)를 받습니다. **딱 한 번만 하면**, 이후엔 cron의 publish/run이 알아서 최신화합니다.
- `install_cron.sh`: 자동 실행 스케줄 3개를 설치합니다(아래 3번). 재실행해도 안전(중복 정리 후 재설치).

> 미리 보고 싶으면: `bash scripts/install_cron.sh --dry-run` (설치 안 하고 어떤 cron이 들어갈지만 출력).
> 실발주는 테스트·계좌 대조 뒤 `--live`를 명시해야 한다. 웹 OFF는 신규 매수만 중단하고 기존 포지션 청산은 유지한다.

---

## 3. 설치되는 cron (자동 실행, 평일 KST)

| 작업 | 시각 | 역할 |
|------|------|------|
| `scripts/publish.py` | 09~15시 **0·15·30·45분** | 실시간 시그널 게시 |
| `analyzer/run.py --push` | 09~15시 **7·22·37·52분** | 내일 상승 예측 게시 |
| `analyzer/backtest.py` | **08:50** | 적중률·보정표 갱신 |

> publish와 analyzer를 **7분 시차**로 배치해 동시 git push 충돌을 막습니다.
> 두 작업 모두 `web/data/`의 **다른 파일**(signals.json / predictions.json)을 갱신 → push → Vercel 자동 배포.

---

## 4. 최초 1회 환경 점검 (이미 했다면 건너뛰기)

`install_cron.sh` 실행 후 출력되는 경고를 확인하고, 안 돼 있으면:
```bash
# 시간대 = 서울(KST)  ← cron 시각이 한국 장중에 맞으려면 필수
sudo systemsetup -settimezone Asia/Seoul
date     # ... KST ... 확인

# Mac이 잠들지 않게(24시간 가동)
sudo pmset -a sleep 0

# 네이버 키(.env) 존재 확인 — 없으면 생성
ls .env || (cp .env.example .env && nano .env)   # NAVER_CLIENT_ID / SECRET 입력

# GitHub push 가능 확인 (Hi xenonluv ... 나오면 OK)
ssh -T git@github.com
```
> ⚠️ **macOS cron 권한**: 시스템 설정 → 개인정보 보호 및 보안 → **전체 디스크 접근 권한**에 `cron`(또는 터미널)을 추가해야 cron이 정상 동작합니다(안 그러면 조용히 실패).

---

## 5. 동작 확인

```bash
# (안전) 예측 미리보기 — 사이트 변경 없음
python3 analyzer/run.py --dry-run

# 설치된 cron 확인
crontab -l | grep -E "publish.py|analyzer/"

# 자동 실행 로그 (평일 장중에 채워짐)
tail -f /tmp/publish.log /tmp/forecast.log /tmp/backtest.log
```
- `게시 완료 … push` 또는 `변경 없음 … skip` = 정상.
- `push 실패 / rejected` 가 보이면 알려주세요(드물게 충돌 시).

---

## 6. 새 기능 어디서 보나

- 실시간 시그널: **https://stocknews-cyan.vercel.app**
- 🎯 내일 상승 예측: **https://stocknews-cyan.vercel.app/forecast** (홈 상단 배너로도 진입)
- 예측 API: `https://stocknews-cyan.vercel.app/api/predictions`

---

## 7. 정직한 한계 (꼭 이해)

- **"내일 상승 확률"은 처음엔 미보정 추정치**입니다(차트+재료+지속성 합치 점수).
- **적중률·보정은 데이터가 쌓여야 의미**가 생깁니다: 하루 1예측 → 익일 평가 구조라 **1~2주 cron이 돌아야** 사이트에 "적중률 XX%"가 뜨고 표시 확률이 실제에 수렴합니다.
- 예측은 확률 게임입니다(적중률 55~60%면 우수). **"확실"이 아니라 확률 우위 + 손절**. 종가베팅은 갭 리스크가 있으니 손절가를 지키세요.

---

## 8. 문제 해결

| 증상 | 해결 |
|------|------|
| `git pull` 충돌/거부 | `git stash && git pull && git stash pop` 또는 알려주기 |
| cron이 안 돎 | macOS 전체 디스크 접근 권한 허용(4번) · `pgrep cron` 확인 |
| /forecast가 비어있음 | 평일 장중에 analyzer cron이 한 번 돌면 채워짐. `python3 analyzer/run.py --dry-run`로 점검 |
| 적중률이 안 보임 | 정상(데이터 누적 전). 며칠 후 표시됨 |
| push 비밀번호 물음 | SSH 원격인지 확인: `git remote -v` → `git@github.com:...` 여야 함 |
| 시간대 경고 | `sudo systemsetup -settimezone Asia/Seoul` |

---

## 9. 한 장 요약

```bash
cd ~/stocknews
git pull                              # 최신 코드(analyzer 포함)
bash scripts/install_cron.sh          # cron 일괄 설치(자동매매 기본 DRY)
sudo systemsetup -settimezone Asia/Seoul   # (필요시) 시간대
sudo pmset -a sleep 0                  # (필요시) 잠자기 끄기
crontab -l | grep analyzer            # 설치 확인
```
→ 다음 평일 장중부터 **실시간 시그널 + 내일 상승 예측**이 자동 갱신됩니다. 자세한 구조는 `analyzer/README.md`·`CLAUDE.md` 참고.
