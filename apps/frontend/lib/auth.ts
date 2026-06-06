// Local-dev auth: core-api with AUTH_PROVIDER=local accepts a
// "local:<uid>[:<email>]" bearer. The dev sign-in stores it; Clerk replaces this
// in prod (the Authorization header shape is identical — a Bearer token).

const KEY = "traditbot.devToken";

export function signInLocal(email: string): void {
  const uid = "u_" + email.replace(/[^a-zA-Z0-9]/g, "_").toLowerCase();
  localStorage.setItem(KEY, `local:${uid}:${email}`);
}

export function signOut(): void {
  localStorage.removeItem(KEY);
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(KEY);
}

export function isSignedIn(): boolean {
  return getToken() !== null;
}
