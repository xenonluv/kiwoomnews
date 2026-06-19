// 찌라시·루머 수집 계층 — 종목토론방(네이버) + 텔레그램 공개채널 검색.
// 모두 공개 HTML(시크릿 불필요). 미확인 루머 원문을 "그대로" 가져와 AI에 근거로 넘기고,
// AI 답변의 인용이 이 원문에 실제 있는지 ask.ts가 사후 대조(환각 차단)한다.
// 전부 best-effort: 실패하면 해당 소스만 빈 배열로 강등(답변 자체는 계속).

const UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36";

export interface RumorItem {
  text: string; // 글 제목/내용 원문 (사후 대조의 기준)
  date: string | null; // 작성 시각(있으면)
  url: string | null; // 원문 링크(있으면) — 사용자가 직접 열어 검증
}

async function fetchText(url: string, timeoutMs = 6000): Promise<string> {
  const res = await fetch(url, {
    cache: "no-store",
    signal: AbortSignal.timeout(timeoutMs),
    headers: { "User-Agent": UA, Accept: "text/html,*/*" },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${url}`);
  return res.text();
}

function decodeEntities(s: string): string {
  return s
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&");
}

function stripTags(s: string): string {
  return decodeEntities(s.replace(/<br\s*\/?>/gi, " ").replace(/<[^>]+>/g, "")).trim();
}

/**
 * 네이버 종목토론방 글 제목 — 개미 루머·찌라시의 주 무대.
 * 글 행은 <a href="/item/board_read.naver?..." ... title="제목"> 형태.
 * 제목 + href(원문 링크)를 발췌(본문 미수집).
 * ⚠ 스팸·욕설·허위 다수 — AI엔 "미확인 루머"로만 전달.
 */
export async function fetchBoardTitles(code: string, limit = 15): Promise<RumorItem[]> {
  const html = await fetchText(`https://finance.naver.com/item/board.naver?code=${code}`, 6000);
  const out: RumorItem[] = [];
  const seen = new Set<string>();
  for (const m of html.matchAll(/<a\b([^>]*read\.naver[^>]*)>/g)) {
    const t = /title="([^"]+)"/.exec(m[1]);
    if (!t) continue;
    const text = stripTags(t[1]);
    if (text.length < 2 || seen.has(text)) continue;
    seen.add(text);
    const h = /href="([^"]+)"/.exec(m[1]);
    const href = h ? decodeEntities(h[1]) : null;
    const url = href ? (href.startsWith("http") ? href : `https://finance.naver.com${href}`) : null;
    out.push({ text, date: null, url });
    if (out.length >= limit) break;
  }
  return out;
}

/**
 * 텔레그램 공개채널 내 종목 검색 — t.me/s/{channel}?q={검색어} 웹 미리보기.
 * 검색어 매칭 메시지를 원문으로 수집. 종목명/코드가 실제 포함된 것만(방어적 재필터).
 */
export async function fetchTelegramMentions(
  name: string,
  code: string,
  channel = "FastStockNews",
  limit = 10
): Promise<RumorItem[]> {
  const url = `https://t.me/s/${channel}?q=${encodeURIComponent(name)}`;
  const html = await fetchText(url, 7000);
  const texts = [...html.matchAll(/<div class="tgme_widget_message_text[^"]*"[^>]*>([\s\S]*?)<\/div>/g)];
  const times = [...html.matchAll(/<time[^>]*datetime="([^"]+)"/g)].map((m) => ({
    pos: m.index ?? 0,
    ts: m[1],
  }));
  // 메시지 래퍼의 data-post="채널/ID" → 영구링크. 텍스트 div 앞에서 열린다(가장 가까운 선행).
  const posts = [...html.matchAll(/data-post="([^"]+)"/g)].map((m) => ({
    pos: m.index ?? 0,
    slug: m[1],
  }));
  const out: RumorItem[] = [];
  for (const m of texts) {
    const text = stripTags(m[1]);
    if (!text || (!text.includes(name) && !text.includes(code))) continue;
    // 메시지 위치 이후 가장 가까운 time 태그
    const pos = m.index ?? 0;
    const t = times.find((x) => x.pos >= pos);
    // 이 텍스트 div 직전(가장 가까운 선행)의 data-post = 그 메시지 링크
    let slug: string | null = null;
    for (const p of posts) {
      if (p.pos <= pos) slug = p.slug;
      else break;
    }
    out.push({ text: text.slice(0, 400), date: t?.ts ?? null, url: slug ? `https://t.me/${slug}` : null });
    if (out.length >= limit) break;
  }
  return out;
}

/** 토론방 + 텔레그램 동시 수집(best-effort). 실패 소스는 빈 배열. */
export async function gatherRumors(
  name: string,
  code: string
): Promise<{ board: RumorItem[]; telegram: RumorItem[] }> {
  const [b, t] = await Promise.allSettled([
    fetchBoardTitles(code),
    fetchTelegramMentions(name, code),
  ]);
  return {
    board: b.status === "fulfilled" ? b.value : [],
    telegram: t.status === "fulfilled" ? t.value : [],
  };
}
