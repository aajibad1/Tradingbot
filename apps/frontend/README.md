# TraditBot frontend

Next.js (App Router) onboarding + dashboard for the multi-tenant arbitrage SaaS,
wired to `core-api`.

## Run

```bash
cd apps/frontend
cp .env.example .env.local      # set NEXT_PUBLIC_API_BASE if core-api isn't on :8080
npm install
npm run dev                     # http://localhost:3000
```

Run `core-api` locally first (AUTH_PROVIDER unset = local mode):

```bash
cd services/core-api && PYTHONPATH=../..:. uvicorn main:app --port 8080
```

## Auth

Dev mode uses a `local:<uid>:<email>` bearer (matches core-api's local AUTH_PROVIDER).
Swap in Clerk for production — the Authorization header shape is unchanged, so only
`lib/auth.ts` (token source) changes.

## Pages
- `/` — dev sign-in
- `/onboarding` — region → KYC → policy stepper (drives the core-api state machine)
- `/dashboard` — profile, entitlements, balances; paper deposit
