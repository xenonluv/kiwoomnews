import { NextResponse } from "next/server";

import type { NextMarketAlertPreviewPayload } from "@/types/radar";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const KV_KEY = "radar:alert-preview:latest";
const KV_TIMEOUT_MS = 4_000;
const NO_STORE = {
  "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
  Pragma: "no-cache",
};

function emptyPayload(
  reason: string,
  configured: boolean
): NextMarketAlertPreviewPayload & { configured: boolean; reason: string } {
  const now = new Date().toISOString();
  return {
    schema_version: 1,
    date: "",
    generated_at: now,
    expires_at: now,
    verified: false,
    codes: {},
    configured,
    reason,
  };
}

function validDate(value: unknown): number | null {
  if (typeof value !== "string") return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * 별도 읽기 전용 KV에서 장막판 미리보기만 조회한다.
 * 메인 레이더 API 캐시와 자동매매 KV 권한을 공유하지 않는다.
 */
export async function GET() {
  const url = process.env.RADAR_PREVIEW_KV_REST_API_URL;
  const token = process.env.RADAR_PREVIEW_KV_REST_API_READ_ONLY_TOKEN;
  if (!url || !token) {
    return NextResponse.json(emptyPayload("preview_kv_not_configured", false), {
      headers: NO_STORE,
    });
  }

  try {
    const response = await fetch(
      `${url.replace(/\/$/, "")}/get/${encodeURIComponent(KV_KEY)}`,
      {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
        signal: AbortSignal.timeout(KV_TIMEOUT_MS),
      }
    );
    if (!response.ok) throw new Error(`preview_kv_http_${response.status}`);
    const envelope = (await response.json()) as { result?: unknown; error?: string };
    if (envelope.error) throw new Error("preview_kv_error");
    if (typeof envelope.result !== "string") {
      return NextResponse.json(emptyPayload("preview_empty", true), {
        headers: NO_STORE,
      });
    }

    const payload = JSON.parse(envelope.result) as NextMarketAlertPreviewPayload;
    if (payload.schema_version !== 1 || !payload.codes || typeof payload.codes !== "object") {
      throw new Error("preview_schema_invalid");
    }
    const now = Date.now();
    const topExpiry = validDate(payload.expires_at);
    if (topExpiry === null || topExpiry <= now) {
      return NextResponse.json(emptyPayload("preview_expired", true), {
        headers: NO_STORE,
      });
    }

    const codes = Object.fromEntries(
      Object.entries(payload.codes).filter(([, record]) => {
        const expiry = validDate(record.expires_at);
        return expiry !== null && expiry > now;
      })
    );
    return NextResponse.json({ ...payload, codes, configured: true }, { headers: NO_STORE });
  } catch {
    return NextResponse.json(emptyPayload("preview_unavailable", true), {
      headers: NO_STORE,
    });
  }
}
