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
  Activity,
  HudSnapshot,
  BackupStatus,
  VersionInfo,
  PushInfo,
  Approval,
  LearningStatus,
  Prefs,
  RemoteReply,
  Routine,
  AppNotification,
  DocumentFile,
  Feedback,
  Features,
  Integration,
  Reminder,
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
  Session,
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
export const getSessions = () => request<Session[]>("/auth/sessions");
export const revokeSession = (id: number) => request<void>(`/auth/sessions/${id}`, { method: "DELETE" });
export const revokeOtherSessions = () => request<{ signed_out: number }>("/auth/sessions", { method: "DELETE" });

// Chat --------------------------------------------------------------------------------

/** `offTheRecord` starts a private chat; a continued chat keeps the setting it started with. */
export const sendMessage = (message: string, conversationId?: number | null, offTheRecord = false, deep = false, documentIds: number[] = []) =>
  request<ChatResponse>("/chat", {
    method: "POST",
    body: { message, conversation_id: conversationId ?? null, private: offTheRecord, deep, document_ids: documentIds },
    timeoutMs: 200_000, // a PC action can wait up to two minutes for you to allow it (components/ApprovalPrompt)
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
export const getVersion = (refresh = false) =>
  request<VersionInfo>(`/version${refresh ? "?refresh=true&wait=true" : ""}`, { timeoutMs: 90_000 });
export const getBackups = () => request<BackupStatus>("/privacy/backups");
export const backUpNow = () => request<BackupStatus>("/privacy/backups", { method: "POST", timeoutMs: 120_000 });
export const exportData = () => request<Record<string, unknown>>("/privacy/export", { timeoutMs: 120_000 });
export const eraseData = () =>
  request<{ erased: Record<string, number> }>("/privacy/erase", { method: "POST", body: { confirm: "ERASE" }, timeoutMs: 120_000 });

// Plan & usage ------------------------------------------------------------------------------

export const getBilling = () => request<Billing>("/billing");
export const activateLicense = (key: string) => request<Billing>("/billing/license", { method: "POST", body: { key } });
export const removeLicense = () => request<Billing>("/billing/license", { method: "DELETE" });

// Day-to-day features -------------------------------------------------------------------------

export const getFeatures = () => request<Features>("/features");
/** Start loading the server's local model while the user types (no-op for a cloud model). */
export const warm = () => request<void>("/warm", { method: "POST" });
export const getDesktopStatus = () => request<{ connected: boolean; device: string | null }>("/desktop/status");
export const getHud = () => request<HudSnapshot>("/hud");
export const getPush = () => request<PushInfo>("/push");
export const setUpPush = () => request<PushInfo>("/push", { method: "POST" });
export const stopPush = () => request<void>("/push", { method: "DELETE" });
export const testPush = () => request<void>("/push/test", { method: "POST" });

export const getNotifications = (unread = false) => request<AppNotification[]>(`/notifications${unread ? "?unread=true" : ""}`);
export const markNotificationRead = (id: number) => request<void>(`/notifications/${id}/read`, { method: "POST" });
export const markAllNotificationsRead = () => request<{ marked: number }>("/notifications/read-all", { method: "POST" });

export const getReminders = () => request<Reminder[]>("/reminders");
export const cancelReminder = (id: number) => request<void>(`/reminders/${id}`, { method: "DELETE" });

export const getDocuments = () => request<DocumentFile[]>("/documents");
export const uploadDocument = (name: string, contentType: string, base64: string) =>
  request<DocumentFile>("/documents", { method: "POST", body: { name, content_type: contentType, data: base64 }, timeoutMs: 240_000 });
export const deleteDocument = (id: number) => request<void>(`/documents/${id}`, { method: "DELETE" });

export const rateMessage = (messageId: number, rating: 1 | -1, correction: string | null, share: boolean) =>
  request<Feedback>(`/messages/${messageId}/feedback`, { method: "POST", body: { rating, correction, share } });
export const clearRating = (messageId: number) => request<void>(`/messages/${messageId}/feedback`, { method: "DELETE" });

export const getIntegrations = () => request<Integration[]>("/integrations");
export const startGoogle = () => request<{ url: string }>("/integrations/google/start");
export const disconnectGoogle = () => request<void>("/integrations/google", { method: "DELETE" });
export const startAccount = (provider: "spotify" | "microsoft") => request<{ url: string }>(`/integrations/${provider}/start`);
export const disconnectAccount = (provider: "spotify" | "microsoft" | "homeassistant" | "telegram") =>
  request<void>(`/integrations/${provider}`, { method: "DELETE" });
export const connectHomeAssistant = (url: string, token: string) =>
  request<{ account: string }>("/integrations/homeassistant", { method: "POST", body: { url, token } });
export const linkTelegram = () =>
  request<{ code: string; bot: string | null; expires_in: number; how: string }>("/integrations/telegram/link", { method: "POST" });

// Jarvis ---------------------------------------------------------------------------------------

export const getApprovals = () => request<Approval[]>("/approvals", { timeoutMs: 10_000 });
export const decideApproval = (id: number, allow: boolean) =>
  request<{ id: number; allowed: boolean }>(`/approvals/${id}`, { method: "POST", body: { allow } });
/** One command for the PC from the phone ("lock my pc"); waits while it asks you to allow it. */
export const remote = (command: string) => request<RemoteReply>("/remote", { method: "POST", body: { command }, timeoutMs: 180_000 });
export const getPrefs = () => request<Prefs>("/settings/prefs");
export const setPrefs = (changes: Partial<Prefs>) => request<Prefs>("/settings/prefs", { method: "PATCH", body: changes });
export const getRoutines = () => request<Routine[]>("/routines");
export const saveRoutine = (name: string, steps: string[]) => request<Routine>("/routines", { method: "POST", body: { name, steps } });
export const deleteRoutine = (id: number) => request<void>(`/routines/${id}`, { method: "DELETE" });
export const runRoutine = (id: number) => request<ChatResponse>(`/routines/${id}/run`, { method: "POST", timeoutMs: 180_000 });
export const getActivity = (q = "", days = 7) =>
  request<Activity[]>(`/activity?days=${days}${q.trim() ? `&q=${encodeURIComponent(q.trim())}` : ""}`);
export const undoActivity = (id: number) => request<{ undone: string }>(`/activity/${id}/undo`, { method: "POST" });
export const getLearning = () => request<LearningStatus>("/learning");

/** Sends a recording as the raw request body and returns what was said. */
export async function transcribe(audio: Blob | ArrayBuffer, contentType: string): Promise<string> {
  const headers: Record<string, string> = { Accept: "application/json", "Content-Type": contentType };
  if (authToken) headers.Authorization = `Bearer ${authToken}`;
  let res: Response;
  try {
    res = await fetch(`${apiBase}/voice/transcribe`, { method: "POST", headers, body: audio as BodyInit, credentials: "include" });
  } catch {
    throw new ApiError(0, "Can't reach the Custodian server to transcribe that.");
  }
  if (res.status === 401) unauthorizedHandler?.();
  const data = (await res.json().catch(() => ({}))) as { text?: string; detail?: string };
  if (!res.ok) throw new ApiError(res.status, data.detail ?? `Transcription failed (${res.status})`);
  return data.text ?? "";
}
