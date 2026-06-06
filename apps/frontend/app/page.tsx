"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { isSignedIn, signInLocal } from "@/lib/auth";

export default function Home() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (isSignedIn()) router.replace("/onboarding");
    else setReady(true);
  }, [router]);

  if (!ready) return null;

  return (
    <div className="panel">
      <h1>Sign in</h1>
      <p className="muted">
        Dev mode: enter an email to create a local session. Clerk replaces this in
        production — the backend already verifies whatever IdP issues the token.
      </p>
      <div className="row">
        <input
          placeholder="you@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <button
          disabled={!email.includes("@")}
          onClick={() => {
            signInLocal(email);
            router.push("/onboarding");
          }}
        >
          Continue
        </button>
      </div>
    </div>
  );
}
