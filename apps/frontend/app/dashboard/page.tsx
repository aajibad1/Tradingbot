"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, DashboardView, ApiError } from "@/lib/api";
import { isSignedIn, signOut } from "@/lib/auth";

export default function Dashboard() {
  const router = useRouter();
  const [data, setData] = useState<DashboardView | null>(null);
  const [err, setErr] = useState("");
  const [amount, setAmount] = useState("1000");
  const [liveMsg, setLiveMsg] = useState("");

  async function refresh() {
    try {
      setData(await api.dashboard());
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function goLive() {
    setLiveMsg("");
    try {
      await api.liveEnable();
      await refresh();
      setLiveMsg("Live trading enabled (still gated by the validation run).");
    } catch (e) {
      // The layered gate returns a precise reason: 403 (permission/plan) or 409 (onboarding).
      setLiveMsg(e instanceof ApiError ? `Blocked: ${e.message}` : String(e));
    }
  }

  useEffect(() => {
    if (!isSignedIn()) router.replace("/");
    else refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (err) return <div className="panel err">Error: {err}</div>;
  if (!data) return <div className="panel">Loading…</div>;

  const { profile, entitlements, balances } = data;

  async function deposit() {
    setErr("");
    try {
      await api.deposit("USD", amount);
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    }
  }

  return (
    <div>
      <div className="panel">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h1>Dashboard</h1>
          <div className="row">
            <a className="muted" href="/team">Team</a>
            <button className="secondary" onClick={() => { signOut(); router.push("/"); }}>
              Sign out
            </button>
          </div>
        </div>
        <p className="muted">
          {profile.email} · tenant {profile.tenant_id} · {profile.market ?? "—"}/
          {profile.region ?? "—"}
        </p>
        <div className="row">
          <span className="badge">{profile.onboarding_status}</span>
          <span className="badge">plan: {entitlements.plan}</span>
          <span className={"badge " + (profile.live_enabled ? "ok" : "warn")}>
            {profile.live_enabled ? "live-enabled" : "paper"}
          </span>
        </div>
        {!profile.live_enabled && (
          <div className="row" style={{ marginTop: 10 }}>
            <button onClick={goLive}>Go live</button>
            <span className="muted">
              Requires the ENABLE_LIVE_TRADING role, a live-capable plan, and
              trading_ready onboarding — and a passing validation run.
            </span>
          </div>
        )}
        {liveMsg && <p className={liveMsg.startsWith("Blocked") ? "err" : "muted"}>{liveMsg}</p>}
      </div>

      <div className="panel">
        <h3>Entitlements</h3>
        <table>
          <tbody>
            <tr><td>Paper trading</td><td>{entitlements.paper_trading ? "yes" : "no"}</td></tr>
            <tr><td>Live trading</td><td>{entitlements.live_trading ? "yes" : "no"}</td></tr>
            <tr><td>Markets</td><td>{entitlements.markets.join(", ") || "—"}</td></tr>
            <tr><td>Max live capital</td><td>${entitlements.max_live_capital_usd.toLocaleString()}</td></tr>
          </tbody>
        </table>
      </div>

      <div className="panel">
        <h3>Balances</h3>
        {balances.length === 0 ? (
          <p className="muted">No funds yet.</p>
        ) : (
          <table>
            <thead><tr><th>Asset</th><th>Available</th><th>Reserved</th></tr></thead>
            <tbody>
              {balances.map((b) => (
                <tr key={b.asset}>
                  <td>{b.asset}</td>
                  <td>{b.available.toLocaleString()}</td>
                  <td>{b.reserved.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="row" style={{ marginTop: 10 }}>
          <input value={amount} onChange={(e) => setAmount(e.target.value)} />
          <button onClick={deposit}>Deposit USD (paper)</button>
        </div>
      </div>
    </div>
  );
}
