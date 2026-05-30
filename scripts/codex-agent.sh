#!/usr/bin/env bash
# Codex 팀원 러너
# 사용법:
#   scripts/codex-agent.sh <프롬프트파일> [입력파일]
#   echo '<입력 JSON>' | scripts/codex-agent.sh <프롬프트파일>
#
# 예:
#   scripts/codex-agent.sh prompts/03_팀원3_퀀트분석.md teamlead_in.json
#   cat 팀원2_출력.json | scripts/codex-agent.sh prompts/03_팀원3_퀀트분석.md
#
# 환경변수(선택):
#   SCHEMA=schemas/03_팀원3.schema.json   # 출력 JSON 스키마 강제
#   SANDBOX=read-only|workspace-write     # 기본 read-only (게시 에이전트는 workspace-write)
#   MODEL=gpt-5.5                         # 모델 override
#   OUT=result.json                       # 최종 결과만 이 파일로 저장 (transcript 제외)

set -euo pipefail

PROMPT_FILE="${1:?프롬프트 파일 경로가 필요합니다 (예: prompts/03_팀원3_퀀트분석.md)}"
INPUT_FILE="${2:-}"

if [[ ! -f "$PROMPT_FILE" ]]; then
  echo "오류: 프롬프트 파일을 찾을 수 없습니다: $PROMPT_FILE" >&2
  exit 1
fi

# 입력 데이터: 인자로 받은 파일 또는 stdin
if [[ -n "$INPUT_FILE" ]]; then
  INPUT_DATA="$(cat "$INPUT_FILE")"
elif [[ ! -t 0 ]]; then
  INPUT_DATA="$(cat)"
else
  INPUT_DATA=""
fi

SYSTEM_PROMPT="$(cat "$PROMPT_FILE")"

# 시스템 프롬프트 + 입력 데이터를 하나의 지시문으로 결합
FULL_PROMPT="$SYSTEM_PROMPT

---
## 입력 데이터
\`\`\`json
$INPUT_DATA
\`\`\`

위 역할/지침/제약/출력형식을 정확히 준수하여 결과를 생성하십시오."

# 옵션 구성
# --disable memories: 파이프라인 실행 중 전역 메모리 쓰기 부작용/노이즈 방지
ARGS=( exec --skip-git-repo-check --disable memories -s "${SANDBOX:-read-only}" )
[[ -n "${MODEL:-}" ]] && ARGS+=( -m "$MODEL" )
[[ -n "${SCHEMA:-}" ]] && ARGS+=( --output-schema "$SCHEMA" )
[[ -n "${OUT:-}" ]] && ARGS+=( --output-last-message "$OUT" )

# 프롬프트는 stdin으로 전달 (대용량 입력 시 'Argument list too long' 방지)
printf '%s' "$FULL_PROMPT" | codex "${ARGS[@]}"
