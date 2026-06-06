import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "TraditBot — Arbitrage SaaS",
  description: "Multi-tenant crypto arbitrage platform (Africa + Global).",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="container">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong>TraditBot</strong>
            <a className="muted" href="/dashboard">Dashboard</a>
          </div>
          {children}
        </div>
      </body>
    </html>
  );
}
