// Typed fetch wrappers for the Custodian API.
//
// Where the API lives:
//   * EXPO_PUBLIC_API_BASE, when set at build time, wins.
//   * A production web build is served by the API itself, so it calls its own origin.
//   * Otherwise http://localhost:8000 (web dev server, iOS simulator).
// The native app can also point at another server from the sign-in screen.
//
// Auth: the web build uses the httpOnly session cookie (credentials: "include"); the native
// app sends the token it keeps in the device keychain as a Bearer header.

import { Platform } from "react-native";

import type {
  AuthResponse,
  Billing,
  ChatResponse,
  Conversation,
  ConversationSummary,
  Memory,
  MemoryType,
  Permission,
  PermissionEvent,
  PrivacySummary,
  RedactMode,
  Skill,
  Task,
  TaskInput,
  TaskRun,
  ToolCallLog,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return "Something went wrong.";
}

export function defaultApiBase(): string {
  const fromEnv = process.env.EXPO_PUBLIC_API_BASE;
  if (fromEnv && fromEnv.trim()) return normalizeBase(fromEnv);
  if (Platform.OS === "web" && !__DEV__) return "";
  return "http://localhost:8000";
}

export function normalizeBase(url: string): string {
  const trimmed = url.trim().replace(/\/+$/, "");
  if (!trimmed) return "";
  return /^https?:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`;
}

let apiBase = defaultApiBase();
let authToken: string | null = null;
let unauthorizedHandler: (() => void) | null = null;

export function configureApi(options: { base?: string; token?: string | null }): void {
  if (options.base !== undefined) apiBase = normalizeBase(options.base);
  if (options.token !== undefined) authToken = options.token;
}

export function getApiBase(): string {
  return apiBase;
}

export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

type RequestOptions = { method?: "GET" | "POST" | "PATCH" | "DELETE"; body?: unknown; timeoutMs?: number };

async function request<T>(path: string, { method = "GET", body, timeoutMs = 30_000 }: RequestOptions = {}): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (authToken) headers.Authorization = `Bearer ${authToken}`;

  let res: Response;
  try {
    res = await fetch(`${apiBase}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "include",
      signal: controller.signal,
    });
  } catch {
    throw new ApiError(
      0,
      controller.signal.aborted
        ? "The server took too long to answer. Try again."
        : `Can't reach the Custodian server${apiBase ? ` at ${apiBase}` : ""}. Check that it's running and reachable.`,
    );
  } finally {
    clearTimeout(timer);
  }

  if (res.status === 401 && path !== "/auth/login") unauthorizedHandler?.();
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const data = (await res.json()) as { detail?: unknown };
      if (typeof data.detail === "string") detail = data.detail;
      else if (Array.isArray(data.detail)) {
        detail = data.detail
          .map((d: { msg?: string }) => d.msg)
          .filter(Boolean)
          .join("; ");
      }
    } catch {
      // not JSON; keep the generic message
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// Auth --------------------------------------------------------------------------------

export const login = (passcode: string) =>
  request<AuthResponse>("/auth/login", {
    method: "POST",
    body: { passcode, client: Platform.OS === "web" ? "web" : "native" },
  });
export const logout = () => request<void>("/auth/logout", { method: "POST" });
export const me = () => request<AuthResponse>("/auth/me");

// Chat --------------------------------------------------------------------------------

/** `offTheRecord` starts a private chat; a continued chat keeps the setting it started with. */
export const sendMessage = (message: string, conversationId?: number | null, offTheRecord = false) =>
  request<ChatResponse>("/chat", {
    method: "POST",
    body: { message, conversation_id: conversationId ?? null, private: offTheRecord },
    timeoutMs: 120_000,
  });
export const listConversations = (channel?: "chat" | "task", limit = 30) =>
  request<ConversationSummary[]>(`/conversations?limit=${limit}${channel ? `&channel=${channel}` : ""}`);
export const getConversation = (id: number) => request<Conversation>(`/conversations/${id}`);
export const deleteConversation = (id: number) => request<void>(`/conversations/${id}`, { method: "DELETE" });

// Tools & Data ------------------------------------------------------------------------

export const getPermissions = () => request<Permission[]>("/permissions");
export const setPermission = (toolId: number, scope: string, granted: boolean) =>
  request<Permission>(`/permissions/${toolId}`, { method: "POST", body: { scope, granted } });
export const getPermissionEvents = (limit = 20) => request<PermissionEvent[]>(`/permissions/events?limit=${limit}`);

export const getMemories = () => request<Memory[]>("/memories");
export const addMemory = (content: string, type: MemoryType) =>
  request<Memory>("/memories", { method: "POST", body: { content, type } });
export const deleteMemory = (id: number) => request<void>(`/memories/${id}`, { method: "DELETE" });

export const getToolCalls = (limit = 25) => request<ToolCallLog[]>(`/tool-calls?limit=${limit}`);

// Scheduled tasks -------------------------------------------------------------------------

export const getSkills = () => request<Skill[]>("/skills");
export const getTasks = () => request<Task[]>("/tasks");
export const createTask = (input: TaskInput) => request<Task>("/tasks", { method: "POST", body: input });
export const updateTask = (
  id: number,
  changes: Partial<Pick<Task, "status" | "cron_schedule" | "timezone">> & { skill?: string },
) => request<Task>(`/tasks/${id}`, { method: "PATCH", body: changes });
export const deleteTask = (id: number) => request<void>(`/tasks/${id}`, { method: "DELETE" });
export const runTask = (id: number) => request<TaskRun>(`/tasks/${id}/run`, { method: "POST", timeoutMs: 180_000 });
export const getTaskRuns = (id: number) => request<TaskRun[]>(`/tasks/${id}/runs`);

// Privacy -----------------------------------------------------------------------------------

export const getPrivacy = () => request<PrivacySummary>("/privacy");
export const updatePrivacy = (changes: { retention_days?: number | null; redact_secrets?: RedactMode }) =>
  request<PrivacySummary>("/privacy/settings", { method: "PATCH", body: changes });
export const exportData = () => request<Record<string, unknown>>("/privacy/export", { timeoutMs: 120_000 });
export const eraseData = () =>
  request<{ erased: Record<string, number> }>("/privacy/erase", { method: "POST", body: { confirm: "ERASE" }, timeoutMs: 120_000 });

// Plan & usage ------------------------------------------------------------------------------

export const getBilling = () => request<Billing>("/billing");
export const activateLicense = (key: string) => request<Billing>("/billing/license", { method: "POST", body: { key } });
export const removeLicense = () => request<Billing>("/billing/license", { method: "DELETE" });
