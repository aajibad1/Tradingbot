// Typed client for core-api. Every call carries the bearer token (local dev
// token now, Clerk session JWT later — same header).
import { getToken } from "./auth";

const BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8080";

export type Market = "africa" | "global";

export interface Profile {
  user_id: string;
  email: string | null;
  tenant_id: string;
  market: Market | null;
  region: string | null;
  onboarding_status: string;
  kyc_status: string;
  plan: string;
  subscription_status: string;
  live_enabled: boolean;
  roles: string[];
  created_at: string;
}

export interface Entitlements {
  plan: string;
  subscription_status: string;
  paper_trading: boolean;
  live_trading: boolean;
  markets: string[];
  max_live_capital_usd: number;
}

export interface OnboardingView {
  market: Market | null;
  region: string | null;
  onboarding_status: string;
  kyc_status: string;
  required_controls: string[];
  next_actions: string[];
}

export interface Balance {
  asset: string;
  available: number;
  reserved: number;
}

export interface TeamMember {
  user_id: string;
  email: string | null;
  roles: string[];
}

export interface DashboardView {
  profile: Profile;
  entitlements: Entitlements;
  balances: Balance[];
}

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* non-JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const api = {
  createSession: () => req<Profile>("/v1/sessions", { method: "POST" }),
  me: () => req<Profile>("/v1/me"),
  onboarding: () => req<OnboardingView>("/v1/onboarding"),
  selectRegion: (market: Market, country: string) =>
    req<OnboardingView>("/v1/onboarding/region", {
      method: "POST",
      body: JSON.stringify({ market, country }),
    }),
  submitKyc: (full_name: string) =>
    req<OnboardingView>("/v1/onboarding/kyc", {
      method: "POST",
      body: JSON.stringify({ full_name }),
    }),
  submitOnboarding: () => req<OnboardingView>("/v1/onboarding/submit", { method: "POST" }),
  dashboard: () => req<DashboardView>("/v1/dashboard"),
  deposit: (asset: string, amount: string) =>
    req<unknown>("/v1/funding/deposit", {
      method: "POST",
      body: JSON.stringify({ asset, amount }),
    }),
  liveEnable: () => req<Profile>("/v1/trading/live-enable", { method: "POST" }),
  team: () => req<TeamMember[]>("/v1/team"),
  addMember: (member_id: string, email: string, role: string) =>
    req<TeamMember[]>("/v1/team/members", {
      method: "POST",
      body: JSON.stringify({ member_id, email, role }),
    }),
  removeMember: (id: string) =>
    req<TeamMember[]>(`/v1/team/members/${encodeURIComponent(id)}`, { method: "DELETE" }),
};

export { ApiError };
