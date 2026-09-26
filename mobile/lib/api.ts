// fetch wrappers — same 4-endpoint contract the backend exposes. Step 1 — see docs/PLAN.md.
// A phone can't resolve `localhost` as the laptop; set EXPO_PUBLIC_API_BASE to the
// machine's LAN IP when testing on a physical device via Expo Go.

const API_BASE = process.env.EXPO_PUBLIC_API_BASE ?? "http://localhost:8000";

export async function sendMessage(text: string) {
  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: text }),
  });
  return res.json() as Promise<{ reply: string; used: Record<string, unknown> }>;
}

export async function getPermissions() {
  const res = await fetch(`${API_BASE}/permissions`);
  return res.json();
}

export async function setPermission(toolId: string, granted: boolean) {
  const res = await fetch(`${API_BASE}/permissions/${toolId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ granted }),
  });
  return res.json();
}
