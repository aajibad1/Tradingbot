"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, OnboardingView, DisclosuresView, ApiError } from "@/lib/api";
import { isSignedIn } from "@/lib/auth";

const COUNTRIES: Record<string, string[]> = {
  africa: ["NG", "ZA", "KE", "GH", "EG"],
  global: ["US", "GB", "DE", "SG"],
};

export default function Onboarding() {
  const router = useRouter();
  const [view, setView] = useState<OnboardingView | null>(null);
  const [disc, setDisc] = useState<DisclosuresView | null>(null);
  const [market, setMarket] = useState<"africa" | "global">("africa");
  const [country, setCountry] = useState("NG");
  const [fullName, setFullName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function refresh() {
    try {
      await api.createSession(); // idempotent: ensures the tenant exists
      const [v, d] = await Promise.all([api.onboarding(), api.disclosures()]);
      setView(v);
      setDisc(d);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function acceptDisclosures() {
    if (!disc) return;
    await run(async () => {
      await api.acceptDisclosures(disc.current_version);
      setDisc(await api.disclosures());
      return api.onboarding();
    });
  }

  useEffect(() => {
    if (!isSignedIn()) router.replace("/");
    else refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function run(fn: () => Promise<OnboardingView>) {
    setBusy(true);
    setErr("");
    try {
      setView(await fn());
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!view) return <div className="panel">Loading… {err && <span className="err">{err}</span>}</div>;

  const status = view.onboarding_status;
  const stepIndex =
    { account_created: 0, identity_pending: 1, identity_verified: 2 }[status] ??
    (["trading_ready", "review_pending", "restricted"].includes(status) ? 3 : 0);

  return (
    <div className="panel">
      <h1>Onboarding</h1>
      <div className="steps">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className={"step" + (i <= stepIndex ? " done" : "")} />
        ))}
      </div>
      <p className="muted">
        Status: <span className="badge">{status}</span> · KYC{" "}
        <span className="badge">{view.kyc_status}</span>
      </p>

      {status === "account_created" && (
        <div>
          <h3>1 · Choose market &amp; country</h3>
          <div className="row">
            <select
              value={market}
              onChange={(e) => {
                const m = e.target.value as "africa" | "global";
                setMarket(m);
                setCountry(COUNTRIES[m][0]);
              }}
            >
              <option value="africa">Africa</option>
              <option value="global">Global</option>
            </select>
            <select value={country} onChange={(e) => setCountry(e.target.value)}>
              {COUNTRIES[market].map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
            <button disabled={busy} onClick={() => run(() => api.selectRegion(market, country))}>
              Next
            </button>
          </div>
        </div>
      )}

      {status === "identity_pending" && (
        <div>
          <h3>2 · Identity (KYC)</h3>
          <p className="muted">Dev stub auto-verifies; a real KYC provider plugs in here.</p>
          <div className="row">
            <input placeholder="Full name" value={fullName} onChange={(e) => setFullName(e.target.value)} />
            <button disabled={busy || fullName.length < 2} onClick={() => run(() => api.submitKyc(fullName))}>
              Verify
            </button>
          </div>
        </div>
      )}

      {status === "identity_verified" && (
        <div>
          <h3>3 · Risk disclosures</h3>
          {disc && !disc.accepted ? (
            <div className="row">
              <span className="muted">Acknowledge the risk disclosures ({disc.current_version}) to continue.</span>
              <button disabled={busy} onClick={acceptDisclosures}>Accept disclosures</button>
            </div>
          ) : (
            <>
              <p className="muted">✓ Risk disclosures accepted.</p>
              <h3>4 · Submit for review</h3>
              <button disabled={busy} onClick={() => run(() => api.submitOnboarding())}>
                Run regional policy check
              </button>
            </>
          )}
        </div>
      )}

      {status === "trading_ready" && (
        <div>
          <h3 className="badge ok">Trading ready (paper)</h3>
          <p className="muted">Live trading still requires a plan + the validation gate.</p>
          <button onClick={() => router.push("/dashboard")}>Go to dashboard</button>
        </div>
      )}
      {status === "review_pending" && (
        <p className="badge warn">
          Pending review{view.required_controls.length ? ` — ${view.required_controls.join(", ")}` : ""}
        </p>
      )}
      {status === "restricted" && <p className="badge bad">Restricted in this jurisdiction.</p>}

      {err && <p className="err">{err}</p>}
    </div>
  );
}
