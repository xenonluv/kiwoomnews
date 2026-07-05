import type { Metadata } from "next";
import "./globals.css";
import { PasswordGate } from "@/components/auth/PasswordGate";

const siteUrl =
  process.env.NEXT_PUBLIC_SITE_URL ?? "https://kiwoomnews.vercel.app";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: "KiwoomNews — 국내 주식 매매 시그널",
    template: "%s — KiwoomNews",
  },
  description:
    "국내 주식 매매 사전정보 취합. CEO 승인(상승확률 ≥ 85% · 안전 타점)된 시그널만 게시합니다.",
  applicationName: "KiwoomNews",
  keywords: ["주식", "매매 시그널", "국내 증시", "눌림목", "저점", "뉴스 분석"],
  robots: { index: false, follow: false }, // 개인용 — 검색엔진 노출 차단
  openGraph: {
    type: "website",
    locale: "ko_KR",
    siteName: "KiwoomNews",
    title: "KiwoomNews — 국내 주식 매매 시그널",
    description:
      "CEO 승인된 국내 주식 매매 시그널을 실시간 게시합니다.",
    url: siteUrl,
  },
  twitter: {
    card: "summary",
    title: "KiwoomNews — 국내 주식 매매 시그널",
    description: "CEO 승인된 국내 주식 매매 시그널을 실시간 게시합니다.",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko" className="dark">
      <body>
        <PasswordGate>{children}</PasswordGate>
      </body>
    </html>
  );
}
