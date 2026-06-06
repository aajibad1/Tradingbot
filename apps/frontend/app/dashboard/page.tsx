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

  async function refresh() {
    try {
      setData(await api.dashboard());
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
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
          <button className="secondary" onClick={() => { signOut(); router.push("/"); }}>
            Sign out
          </button>
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
