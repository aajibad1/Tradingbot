"use client";
import { useEffect, useState } from "react";
import { fetchStatus, PlatformStatus } from "@/lib/status";

export default function StatusBanner() {
  const [s, setS] = useState<PlatformStatus | null>(null);

  useEffect(() => {
    let active = true;
    const tick = () => fetchStatus().then((r) => active && setS(r));
    tick();
    const id = setInterval(tick, 30_000); // poll every 30s
    return () => { active = false; clearInterval(id); };
  }, []);

  if (!s || s.status === "ok") return null; // only show when not healthy

  const unhealthy = s.components.filter((c) => c.status !== "ok").map((c) => c.name);
  const label = s.status === "down" ? "Service disruption" : "Degraded service";
  return (
    <div className={"banner " + (s.status === "down" ? "bad" : "warn")} role="status">
      ● {label}
      {unhealthy.length > 0 && <span className="muted"> — {unhealthy.join(", ")}</span>}
    </div>
  );
}
