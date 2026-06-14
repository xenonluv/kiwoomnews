#!/usr/bin/env python3
"""Moonshot Kimi verifier for radar candidates.

The radar pipeline calculates all numbers deterministically. Kimi only reviews
the supplied evidence for context, contradictions, and risk flags.
"""
import json
import os
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class KimiConfigError(RuntimeError):
    pass


class KimiUnavailableError(RuntimeError):
    pass


def _load_env():
    for name in (".env", os.path.join("web", ".env.local")):
        path = os.path.join(REPO, name)
        if not os.path.exists(path):
            continue
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def enabled(mode="auto"):
    """Return whether Kimi verification should run for this process."""
    _load_env()
    if os.environ.get("RADAR_KIMI_VERIFY", "").strip() == "0":
        return False
    if mode == "off":
        return False
    if mode == "on":
        return True
    return bool(os.environ.get("MOONSHOT_API_KEY", "").strip())


def _config():
    _load_env()
    api_key = os.environ.get("MOONSHOT_API_KEY", "").strip()
    if not api_key:
        raise KimiConfigError("MOONSHOT_API_KEY is not configured")
    return {
        "api_key": api_key,
        "base_url": os.environ.get("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1").rstrip("/"),
        "model": os.environ.get("MOONSHOT_MODEL", "kimi-k2.6"),
    }


SYSTEM_PROMPT = """당신은 한국 주식 단기 트레이딩 리스크 검증관입니다.
입력으로 제공되는 수치는 모두 코드가 계산한 값입니다. 수치를 새로 계산하지 말고, 급락이 실제 매도 붕괴인지 흔들기/물량 흡수인지 맥락과 모순을 검토하세요.

반드시 JSON만 반환하세요:
{
  "verdict": "CONFIRM" | "WATCH" | "REJECT",
  "confidence": 0~100 정수,
  "reason": "한 문장",
  "risk_flags": ["1~3개"],
  "manual_check": "사람이 장마감 전 확인할 한 가지"
}

판단 기준:
- CONFIRM: 급락 후 저가 방어, 막판 회복, 거래대금, 수급/뉴스 맥락이 서로 크게 모순되지 않음.
- WATCH: 일부 흡수 흔적은 있으나 뉴스/수급/분봉 중 확인 부족.
- REJECT: 명확한 악재, 종가 저가권 붕괴, 막판 재하락, 투매성 거래량 등 갭하락 위험이 큼.
투자 권유 표현은 쓰지 마세요."""


def _validate(raw):
    if not isinstance(raw, dict):
        return None
    verdict = raw.get("verdict")
    if verdict not in ("CONFIRM", "WATCH", "REJECT"):
        return None
    try:
        confidence = int(round(float(raw.get("confidence"))))
    except (TypeError, ValueError):
        return None
    reason = str(raw.get("reason") or "").strip()[:240]
    manual_check = str(raw.get("manual_check") or "").strip()[:160]
    risks = raw.get("risk_flags")
    if not isinstance(risks, list):
        risks = []
    risks = [str(x).strip()[:120] for x in risks if str(x).strip()][:3]
    if not reason:
        return None
    return {
        "status": "ok",
        "verdict": verdict,
        "confidence": max(0, min(100, confidence)),
        "reason": reason,
        "risk_flags": risks,
        "manual_check": manual_check,
    }


def verify_candidate(candidate, timeout=60):
    cfg = _config()
    evidence = {
        "code": candidate.get("code"),
        "name": candidate.get("name"),
        "sector": candidate.get("sector"),
        "theme": candidate.get("theme"),  # 상위 테마(금리/반도체/…) — AI에 동반군 맥락 제공
        "pattern": candidate.get("pattern"),
        "price": candidate.get("price"),
        "change_pct": candidate.get("change_pct"),
        "high_pct": candidate.get("high_pct"),
        "fade_pct": candidate.get("fade_pct"),
        "value_eok": candidate.get("value_eok"),
        "ma10_margin_pct": candidate.get("ma10_margin_pct"),
        "score_raw": candidate.get("score_raw"),
        "score_breakdown_raw": candidate.get("score_breakdown_raw"),
        "shake": candidate.get("shake"),
        "deep_shake": candidate.get("deep_shake"),
        "spark": candidate.get("spark"),
        "flow": candidate.get("flow"),
        "news_titles": [n.get("title") for n in candidate.get("news", [])[:6] if n.get("title")],
        "events": candidate.get("matched_events", [])[:4],
    }
    body = json.dumps({
        "model": cfg["model"],
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(evidence, ensure_ascii=False)},
        ],
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        cfg["base_url"] + "/chat/completions",
        data=body,
        headers={
            "content-type": "application/json",
            "authorization": "Bearer " + cfg["api_key"],
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except (urllib.error.URLError, TimeoutError) as e:
        raise KimiUnavailableError(str(e)) from e
    try:
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except Exception as e:
        raise KimiUnavailableError("invalid Kimi response") from e
    valid = _validate(parsed)
    if not valid:
        raise KimiUnavailableError("Kimi response schema validation failed")
    valid["model"] = cfg["model"]
    return valid
