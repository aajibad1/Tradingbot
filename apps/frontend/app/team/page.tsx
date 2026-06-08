"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, TeamMember, ApiError } from "@/lib/api";
import { isSignedIn } from "@/lib/auth";

const ROLES = ["admin", "operator", "analyst", "support"];

export default function Team() {
  const router = useRouter();
  const [members, setMembers] = useState<TeamMember[] | null>(null);
  const [err, setErr] = useState("");
  const [memberId, setMemberId] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("analyst");

  async function refresh() {
    try {
      setMembers(await api.team());
      setErr("");
    } catch (e) {
      // 403 = caller lacks MANAGE_TEAM; show a friendly message rather than a crash.
      setErr(e instanceof ApiError ? e.message : String(e));
    }
  }

  useEffect(() => {
    if (!isSignedIn()) router.replace("/");
    else refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function add() {
    setErr("");
    try {
      setMembers(await api.addMember(memberId, email, role));
      setMemberId("");
      setEmail("");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function remove(id: string) {
    setErr("");
    try {
      setMembers(await api.removeMember(id));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function changeRole(id: string, newRole: string) {
    setErr("");
    try {
      setMembers(await api.changeRole(id, newRole));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    }
  }

  return (
    <div className="panel">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Team</h1>
        <a className="muted" href="/dashboard">← Dashboard</a>
      </div>
      <p className="muted">Members of your tenant. Requires the manage_team permission.</p>

      {members && (
        <table>
          <thead><tr><th>User</th><th>Email</th><th>Roles</th><th></th></tr></thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.user_id}>
                <td>{m.user_id}</td>
                <td>{m.email ?? "—"}</td>
                <td>
                  {m.roles.includes("owner") ? (
                    "owner"
                  ) : (
                    <select value={m.roles[0] ?? "analyst"}
                            onChange={(e) => changeRole(m.user_id, e.target.value)}>
                      {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                  )}
                </td>
                <td>
                  {!m.roles.includes("owner") && (
                    <button className="secondary" onClick={() => remove(m.user_id)}>Remove</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3 style={{ marginTop: 16 }}>Invite member</h3>
      <div className="row">
        <input placeholder="user id" value={memberId} onChange={(e) => setMemberId(e.target.value)} />
        <input placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <button disabled={!memberId} onClick={add}>Invite</button>
      </div>

      {err && <p className="err">{err}</p>}
    </div>
  );
}
