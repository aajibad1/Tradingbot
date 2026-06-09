// Public platform status (status-service). No auth — read-only.
// NEXT_PUBLIC_STATUS_URL points at status-service; unset → no banner.

const STATUS_BASE = process.env.NEXT_PUBLIC_STATUS_URL || "";

export interface StatusComponent {
  name: string;
  status: "ok" | "degraded" | "down";
  detail: string;
  critical: boolean;
}

export interface PlatformStatus {
  status: "ok" | "degraded" | "down";
  components: StatusComponent[];
}

export async function fetchStatus(): Promise<PlatformStatus | null> {
  if (!STATUS_BASE) return null;
  try {
    // status-service returns 503 when down — read the body regardless of code.
    const res = await fetch(`${STATUS_BASE}/status`, { cache: "no-store" });
    return (await res.json()) as PlatformStatus;
  } catch {
    return null;
  }
}
