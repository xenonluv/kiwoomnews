# kiwoomnews-web

게시 웹사이트 + 공개 REST API. **Next.js(App Router) + TS + Tailwind + shadcn/ui** 다크 금융 대시보드.

## ⚠️ 실행 환경 주의 (중요)

이 프로젝트는 WSL 경로(`/home/xenonluv/kiwoomnews`)에 있습니다. **Windows 측 npm은 WSL의 UNC 경로(`\\wsl.localhost\...`)에서 동작하지 않습니다.** 반드시 **WSL 내부에서 Linux용 Node로 실행**하세요.

```bash
# 1) WSL에 Linux Node 설치 (최초 1회) — nvm 권장
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
exec $SHELL
nvm install 20            # Node 20 LTS

# 2) 의존성 설치 & 실행 (WSL 터미널에서)
cd ~/kiwoomnews/web
npm install
npm run dev               # http://localhost:3000
```

장막판 익일 투자주의 미리보기는 자동매매 KV와 분리된 저장소를 사용한다.
Mac 실행기에는 `RADAR_PREVIEW_KV_REST_API_URL`과 쓰기
`RADAR_PREVIEW_KV_REST_API_TOKEN`, Vercel에는 같은 URL과
`RADAR_PREVIEW_KV_REST_API_READ_ONLY_TOKEN`만 설정한다.

> 확인: `which node` 가 `/home/.../.nvm/...` 같은 Linux 경로여야 합니다.
> `/mnt/c/Program Files/nodejs/...` 가 나오면 Windows Node이므로 위 nvm 설치가 필요합니다.

## 구조
```
web/
├── app/
│   ├── globals.css        # 디자인 토큰 (SSOT) — 다크 금융, 한국 색 관례
│   ├── layout.tsx         # 다크 모드 + Pretendard
│   └── page.tsx           # 시그널 카드 데모 (실제론 /api/signals fetch)
├── components/
│   ├── ui/                # shadcn 코어: button, card, badge
│   └── signal/            # SignalCard, ProbabilityGauge, MarketStatusBadge, DisclaimerNote
├── lib/utils.ts           # cn()
└── types/signal.ts        # 게시 데이터 타입 (CEO 스키마와 정합)
```

## 디자인 시스템
사양: `../docs/02-design/design-system.md` · 컴포넌트 추가: `npx shadcn@latest add <name>`
