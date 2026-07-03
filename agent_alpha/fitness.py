"""종가베팅 적합도 점수 v4 (SSOT — quant 저장·calibrate 검증이 공유하는 단일 파이썬 산식).

⚠ web/components/alpha/AlphaList.tsx 의 `closeBetFitness` 와 **1:1 동기화**한다(산식 변경 시 양쪽 같이).
목표 지표 = 익일 고가 도달(+7% 익절 터치). 2026-07-02 4각도 감사 + 2인 심판 "수정승인" 판결 반영
(39표본·4거래일 — 순열검정상 우연 통과율 97.9%라 어떤 축도 '검증됨' 아님. 재앙회피용 잠정 휴리스틱이며,
날짜 내 순위상관 ≈0 — 상위 정밀 서열이 아니라 하위권(함정) 회피가 실효. ~07/25 라벨 성숙 전 튜닝 동결).

기준 50 + 가감 (0~100 클램프):
  유형        reaccum +10 / youtong 0 / explosion −50
              ⚠ explosion −50은 실증(고가터치 67%=기저급) 아닌 **실행성** 벌점 — 상한가류 종가 체결불가·익일 갭 리스크.
  유동성결핍   (거래대금<50억 OR 2일회전율<40%) → −15 **한 번만** (같은 현상 이중처벌 금지 — 감사 판결)
  거래대금     ≥1000억 +10 (실증 최강: 날짜보정 +13.2%p·LODO 4/4. 50~150억 벌점은 부호 반대로 삭제됨)
  2일회전율    유동성결핍 외 가감 없음 (밴드 지그재그=노이즈 판정, 전삭제)
  당일등락     ≤−10% +15(유일한 실증 가점: 터치 100%·+12.9%p) / 0~+8% +12 / −10~0 +8
              / +8~15 −20(실증 데드존 −41.7%p) / +15~22 −30 / +22↑ −40
              ⚠ +15↑ 벌점은 실증(해당 밴드 고가터치는 오히려 기저 상회) 아닌 **실행성** — 종가 추격매수 불가·갭 위험.
  스파크       약스파크(0<최대몸통<3%) −8 (최견고 음신호: −16.8%p·LODO 0/4 — '찔끔 불꽃'=가짜 모멘텀)
              **강스파크(3%↑) 2회 이상 +8** (회장님 지시 2026-07-02 "이 프로그램은 고가를 먹는 것" —
              과거 4/4 익일 +7% 고가 터치·날짜보정 +21.9%p. 단 종가는 2/4가 −23~−29% 참사 = 순수 익절용 신호.
              n=4 소표본 경고 유지, by_spark_strength 관찰축으로 계속 검증)
              강스파크 1회·무스파크 = 0 (1회 가점은 감사 기각 — 무스파크 우위 관측, 서열 판정 유보).
  마감강도     강마감(close_strength≥0.6) −5 (실증 −4.5%p·LODO 0/4 + 코어 peak_ibs 관찰과 방향 정합)
  숨은외인     lv≥1 −5 (미약 −1.5%p이나 제거 시 백테스트 개악 — 유지, by_hidden_foreign 관찰축 재판정)
  ── 폭락 제외 (2026-07-02 회장님 직접 지시 "당장 폭락하는 종목은 감점으로 제외" — 동결 예외) ──
  과확장붕괴   run_6d_pct ≥ +100% AND 당일 음수 → −30 (금호건설형: 5연상 뒤 붕괴는 눌림이 아니라 추락.
              39표본 백테스트 오폭 0건 — 과거 눌림 승자들은 전부 run6 +42% 이하)
  연속하락     down_streak ≥ 4일 → −15 (광주신세계형: 4일째 연속 하락 = 미반전 추세.
              임계 4일 근거: 승자군 최대 연속하락 3일(광주 7/1 3일차 → 익일 +15.9% 승리), 4일↑ 과거 오폭 0건)
  ⚠ peak_dd(고점 이탈폭)는 점수 미반영 — 승자들이 고점 −26~−47%에서 나와(서산·모헨즈·강동) 일괄 감점은 역효과.
  20일선     ma20_gap_pct < 0 (현재가가 일봉 20일선 아래=역배열) → −20 (회장님 지시 2026-07-03
              "적어도 20일선 위에는 있어야". 검증: 과거 38표본 전원 20일선 위 = 오폭 0건 완전 무해,
              첫 위반자 희림 7/2(−6.3%)가 7/3 −8%대로 실증. 역배열=위쪽 전부 매물벽. by_ma20 관찰축 전진검증)
결측 필드는 가점/감점 없이 통과(날조 금지).
"""


def _hidden_foreign(row):
    """키움 속 숨은 외인매집 강도(0~3). quant 저장값 우선, 결측(옛 행)이면 동일식 재계산."""
    hf = row.get("hidden_foreign_level")
    if hf is not None:
        return hf
    fn, gq, kc = row.get("frgn_net"), row.get("glob_net_qty"), row.get("kiwoom_buy_concentration")
    if fn is None or gq is None or kc is None:
        return 0
    if fn <= 0 or abs(gq) >= abs(fn) * 0.1 or kc < 0.3:
        return 0
    return 3 if fn >= 100000 else 2 if fn >= 30000 else 1


def close_bet_breakdown(row):
    """정량 행(dict) → (종베 점수 0~100, 근거 칩 [(라벨, 가감)]). 라벨은 웹 closeBetFitness 칩과 1:1."""
    reasons = []
    s = 50

    def add(k, v):
        nonlocal s
        s += v
        reasons.append((k, v))

    mt = row.get("mover_type")
    if mt == "reaccum":
        add("재매집", 10)
    elif mt == "explosion":
        add("폭발(추격불가)", -50)
    # 유동성 결핍(통합) — 둘 다 해당해도 한 번만 −15
    v = row.get("value_eok")
    t = row.get("turnover_2d_pct")
    if (v is not None and v < 50) or (t is not None and t < 40):
        add("유동성결핍", -15)
    # 거래대금 대형 가점
    if v is not None and v >= 1000:
        add("대금1000억↑", 10)
    # 당일등락
    c = row.get("change_pct")
    if c is not None:
        cv = 15 if c <= -10 else 8 if c < 0 else 12 if c < 8 else -20 if c < 15 else -30 if c < 22 else -40
        add(f"당일{c:+.0f}%", cv)
    # 약스파크 벌점 (최대몸통 0<x<3% — 강1회/무스파크는 중립)
    mx = row.get("spark_max_body_pct")
    if mx is not None and 0 < mx < 3.0:
        add("약스파크", -8)
    # 강스파크(3%↑) 2회 이상 +8 — 저장값(spark_strong_count) 우선, 옛 행은 spark_bars로 재계산(웹 미러와 1:1)
    ssc = row.get("spark_strong_count")
    if ssc is None and row.get("spark_source") not in (None, "none") and row.get("spark_bars") is not None:
        ssc = sum(1 for b in row["spark_bars"] if (b.get("body_pct") or 0) >= 3.0)
    if ssc is not None and ssc >= 2:
        add("강스파크x2", 8)
    # 강마감
    cs = row.get("close_strength")
    if cs is not None and cs >= 0.6:
        add("강마감", -5)
    if _hidden_foreign(row) >= 1:
        add("외인매집", -5)
    # 폭락 제외 (회장님 지시) — 과확장 붕괴·연속 하락 중인 종목은 눌림 가점이 있어도 상위 진입 차단
    r6 = row.get("run_6d_pct")
    if r6 is not None and r6 >= 100 and c is not None and c < 0:
        add("과확장붕괴", -30)
    ds = row.get("down_streak")
    if ds is not None and ds >= 4:
        add(f"연속하락{ds}일", -15)
    # 20일선 아래(역배열) — 최소 요건 게이트급 벌점 (회장님 지시 2026-07-03)
    mg = row.get("ma20_gap_pct")
    if mg is not None and mg < 0:
        add("20일선아래", -20)
    # KRX 시장경보 '지정' 벌점 (회장님 지시 2026-07-03: 투자경고 지정 종목이 85점 1위 추천 — 적어도 후순위로).
    # 경고 지정 후 재상승 시 매매정지 지정 리스크 = 실행성 벌점(위험은 사실상 진입 금지급).
    # 주의 −10·경고지정예측(alert_forecast) −25도 아래에서 감점(회장님 지시 2026-07-03 — 덕신 7/2
    # 주의+요건충족 무감점 1위 → 익일 −17.8% 재발 차단). 해제 예정(alert_release)만 가산.
    # 단 '내일 해제 예정'(alert_release=True, KRX 해제공식 예측)이면 반대로 최대 가산점 — 해제=재료(회장님 지시).
    an = row.get("alert_now")
    if an == "경고":
        if row.get("alert_release"):
            add("경고해제예정", 20)
        else:
            add("투자경고", -30)
    elif an == "위험":
        add("투자위험", -60)
    elif an == "주의":
        # 투자주의 −10 (회장님 지시 2026-07-03 — "투자주의도 확인 안 하고 종베하다 물림").
        # 온건치: 주의는 급등주에 매우 흔한 지정이라 강벌점이면 승자(6/30 금호전기류 +18%)도 걸러짐.
        add("투자주의", -10)
    # 경고지정 예측 −25 — "경고예고 예상"/"경고지정 요건충족"이면 내일 KRX 지정 → 매물폭탄·정지 리스크.
    # 지정 전날 종베가 최악의 타이밍(덕신 7/2 실측: 주의+요건충족 무감점 1위 → 익일 −17.8%·고가 −2.6%).
    # ⚠ 가드 필수(장식 아님): alert_watch는 '경고예고 예상' 티어만 레벨 억제하고 '경고지정 요건충족'은
    # 경고 지정 종목에도 채운다(실측) — 가드 없으면 경고 −30에 −25가 중복돼 −55 이중감점.
    if row.get("alert_forecast") and an not in ("경고", "위험"):
        add("경고지정예측", -25)
    return max(0, min(100, s)), reasons


def close_bet_fitness(row):
    """정량 행(dict) → 종베 적합도 점수(int 0~100). v4. (근거 칩까지 필요하면 close_bet_breakdown.)"""
    return close_bet_breakdown(row)[0]
