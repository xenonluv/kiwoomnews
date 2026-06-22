// 뉴스 재료필터 — scripts/team2_relevance.py 포팅 (결정론 규칙).
// 시황/지수 노이즈 제거 + 호재/악재 판별 + 중요도(1~10) 산출.

import type { NewsSection } from "@/types/stock";

// 종목명 별칭 (공식명 ↔ 뉴스 통용 표기) — team2_relevance.MANUAL_ALIAS 동일
const MANUAL_ALIAS: Record<string, string[]> = {
  NAVER: ["네이버"],
  삼성에스디에스: ["삼성SDS", "삼성 SDS"],
  LG씨엔에스: ["LG CNS", "엘지씨엔에스", "LGCNS"],
  LG전자: ["엘지전자"],
  LG디스플레이: ["엘지디스플레이", "LGD"],
  LG이노텍: ["엘지이노텍"],
  LG: ["엘지"],
  삼성전자: ["삼전"],
  SK텔레콤: ["SKT", "SK 텔레콤", "에스케이텔레콤"],
  SK하이닉스: ["하이닉스", "SK 하이닉스"],
  카카오뱅크: ["카뱅"],
  현대차: ["현대자동차"],
};

export function makeAliases(name: string): string[] {
  const set = new Set([name, name.replace(/\s/g, ""), name.replace(/(우|우B)$/u, "")]);
  for (const a of MANUAL_ALIAS[name] ?? []) set.add(a);
  return [...set].filter((a) => a && a.length >= 2).map((a) => a.toLowerCase());
}

function mentions(text: string, aliases: string[]): boolean {
  if (aliases.length === 0) return true;
  const t = text.toLowerCase();
  return aliases.some((a) => {
    if (!t.includes(a)) return false;
    // 영문 약어(lg·sk 등)는 무관 단어 내부 우연일치(flagship·risk·task) 방지 — 영숫자 경계 요구.
    // 한글 별칭은 \b가 무의미하므로 substring 그대로(종목명은 충분히 변별적).
    if (/^[a-z0-9]+$/.test(a)) return new RegExp(`(?<![a-z0-9])${a}(?![a-z0-9])`).test(t);
    return true;
  });
}

// 강한 시황/일반(노이즈) — 제목에 있으면 무조건 제외
const HARD =
  /\[마감|\[개장|\[이 시각|데이터랩|뉴스초점|미리보는|마감\s*시황|검색\s*상위|인기\s*검색|빚투|신용잔고|예탁금|오늘의 메모|기업 공시 \[|부고|인사 |사외이사|본사 수도권|주간 증시|애프터마켓|리밸런싱|정기변경|코스피|코스닥|증시|지수/u;
// 약한 시황 — 재료 키워드 없으면 제외
const WRAP = /시황|개장|장\s*마감|특징주|오후 시황|오전 시황/u;
// 재료(호재 성향)
const POS =
  /호실적|실적|영업이익|순이익|매출|흑자|수주|계약|공급|납품|출시|신제품|신고가|상한가|급등|투자|유치|협력|제휴|인수|합병|수출|목표주가|상향|승인|허가|임상|특허|점유율|1위|최대|최고|돌파|수혜|확대|성장|호조|반등/u;
// 악재
const NEG =
  /적자|급락|폭락|하락|감소|소송|횡령|불성실|상장폐지|유상증자|하향|매도|손실|리콜|결함|철회|부진|악재|반토막|하한가|영업정지|제재|벌금|배임/u;
// 강한 재료(제목 가중)
const STRONG =
  /실적|영업이익|순이익|매출|흑자|적자|수주|계약|공급|신고가|상한가|급등|급락|수출|유치|인수|합병|목표주가|승인|허가|임상|특허|1위|최대 수주/u;

interface RawNews {
  title: string;
  summary: string;
  datetime: string;
  url: string | null;
  office: string | null;
}

interface Classified {
  relevant: boolean;
  sentiment: "호재" | "악재" | "혼재" | "중립";
  strong: boolean;
}

function classify(item: RawNews, aliases: string[]): Classified {
  const title = item.title ?? "";
  const text = `${title} ${item.summary ?? ""}`;
  if (HARD.test(title)) return { relevant: false, sentiment: "중립", strong: false };
  if (!mentions(text, aliases)) return { relevant: false, sentiment: "중립", strong: false };

  const wrap = WRAP.test(title);
  const posT = POS.test(title);
  const negT = NEG.test(title);
  const posB = POS.test(text);
  const negB = NEG.test(text);
  const strongTitle = STRONG.test(title);

  let score = 0;
  if (strongTitle) score += 2;
  else if (posT || negT) score += 1;
  if (posB || negB) score += 1;
  if (wrap && !(posT || negT)) score -= 3; // 재료 없는 순수 시황

  const sentiment = posB && negB ? "혼재" : negB ? "악재" : posB ? "호재" : "중립";
  return { relevant: score >= 1, sentiment, strong: strongTitle };
}

/** 뉴스 리스트 → 재료필터 통과분 + 감성/중요도 요약 (team2_relevance.score_news 동일 산식). */
export function scoreNews(news: RawNews[], aliases: string[]): NewsSection {
  const items: NewsSection["items"] = [];
  let strongCnt = 0;
  let posCnt = 0;
  let negCnt = 0;
  let relevantCnt = 0;

  for (const it of news) {
    const c = classify(it, aliases);
    if (c.relevant) {
      relevantCnt += 1;
      if (c.strong) strongCnt += 1;
      if (c.sentiment === "호재") posCnt += 1;
      else if (c.sentiment === "악재") negCnt += 1;
    }
    items.push({
      title: it.title,
      url: it.url,
      office: it.office,
      datetime: it.datetime,
      sentiment: c.sentiment,
      relevant: c.relevant,
    });
  }

  let importance = Math.min(10, 3.0 + 1.5 * strongCnt + 0.5 * (relevantCnt - strongCnt));
  if (negCnt > posCnt) importance = Math.max(1, importance - 2.0);
  importance = Math.round(importance * 10) / 10;

  const sentiment =
    posCnt > negCnt ? "호재" : negCnt > posCnt ? "악재" : posCnt && negCnt ? "혼재" : "중립";

  return {
    // 재료 통과 뉴스 우선 정렬 후 상위 8건만 노출
    items: [...items].sort((a, b) => Number(b.relevant) - Number(a.relevant)).slice(0, 8),
    summary: {
      sentiment,
      importance,
      impact: importance >= 7 ? "상" : importance >= 5 ? "중" : "하",
      relevantCount: relevantCnt,
      posCount: posCnt,
      negCount: negCnt,
    },
  };
}
