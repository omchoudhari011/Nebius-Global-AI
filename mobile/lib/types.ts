// Shapes returned by the Custodian API (backend/app/routers/*). Timestamps are ISO 8601 UTC.

export type MemoryType = "episodic" | "semantic" | "preference";
export type ToolStatus = "rejected" | "denied" | "running" | "succeeded" | "failed" | "timed_out";

export type User = { id: number; email: string; created_at: string };

export type ServerInfo = {
  version: string;
  model_backend: "stub" | "nebius" | string;
  model: string;
  timezone: string;
  scheduler: boolean;
};

/** One signed-in device (backend/app/auth.py). */
export type Session = {
  id: number;
  client: "web" | "native";
  device: string | null;
  created_at: string;
  last_seen_at: string;
  current: boolean;
};

export type AuthResponse = { user: User; server: ServerInfo; token?: string; expires_in?: number };

/** `matched` (memories in a reply's context only): picked for that message by keyword or as a
 * standing preference; false for the rest of the lifetime memory that was also included. */
export type UsedMemory = { id: number; type: MemoryType; content: string; matched?: boolean };

/** Something you said in an earlier conversation, found by keyword and put in a reply's context. */
export type EarlierMessage = { id: number; conversation_id: number; created_at: string; content: string };

export type UsedTool = {
  id: number;
  tool: string;
  function: string;
  scope: string;
  status: ToolStatus;
  error: string | null;
  latency_ms: number | null;
};

export type SkillRef = { id: number; name: string; version: number };

/** One problem the accuracy check found in a reply. `fixed`: the model's first draft had it
 * and the rewrite you see doesn't; otherwise the reply carries a "Custodian check:" note. */
export type CheckItem = { kind: string; note: string; fixed: boolean };

/** Custodian's checks on a reply (backend/app/grounding.py): were its claims backed by what
 * really happened, and how many secrets were withheld from the model. */
export type Checks = {
  status: "passed" | "corrected" | "flagged";
  items: CheckItem[];
  memories_not_saved: number;
  secrets_withheld: number;
  /** The larger model answered (think harder, or after the everyday model failed twice). */
  larger_model?: boolean;
};

/** What shaped one reply: the memories and earlier messages in its context, tool calls,
 * skill version, memories saved, and the checks it went through (null on older replies). */
export type Used = {
  memories: UsedMemory[];
  earlier: EarlierMessage[];
  tools: UsedTool[];
  skill: SkillRef | null;
  remembered: UsedMemory[];
  checks?: Checks | null;
};

export type ChatResponse = { reply: string; used: Used; conversation_id: number; message_id: number; private: boolean };

export type ConversationSummary = {
  id: number;
  channel: "chat" | "task";
  /** Off the record: never remembered or recalled, deleted a day after the last message. */
  private: boolean;
  started_at: string;
  last_message_at: string | null;
  preview: string;
  task: { id: number; name: string; run_id: number; run_status: TaskRunStatus } | null;
};

export type ConversationMessage = {
  id: number;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  used?: Used;
  feedback?: Feedback | null;
};

export type Conversation = {
  id: number;
  channel: "chat" | "task";
  private: boolean;
  started_at: string;
  task: {
    id: number;
    name: string;
    run_id: number;
    run_status: TaskRunStatus;
    error: string | null;
    started_at: string;
    finished_at: string | null;
  } | null;
  messages: ConversationMessage[];
};

export type Permission = {
  tool_id: number;
  tool_name: string;
  category: string | null;
  scope: string;
  description: string | null;
  granted: boolean;
  enabled: boolean;
  executor: string;
};

export type PermissionEvent = {
  id: number;
  tool_id: number;
  tool_name: string;
  scope: string;
  granted: boolean;
  changed_at: string;
};

export type Memory = {
  id: number;
  type: MemoryType;
  content: string;
  source_message_id: number | null;
  created_at: string;
  updated_at: string;
  expires_at: string | null;
};

export type ToolCallLog = {
  id: number;
  tool: string;
  function: string;
  scope: string;
  status: ToolStatus;
  args: unknown;
  error: string | null;
  latency_ms: number | null;
  conversation_id: number;
  channel: "chat" | "task";
  created_at: string;
  finished_at: string | null;
};

export type Skill = {
  id: number;
  name: string;
  version: number;
  description: string;
  tools: string[];
  created_at: string;
};

export type TaskRunStatus = "running" | "succeeded" | "failed";

export type TaskRun = {
  id: number;
  task_id: number;
  conversation_id: number | null;
  status: TaskRunStatus;
  error: string | null;
  started_at: string;
  finished_at: string | null;
};

export type Task = {
  id: number;
  name: string;
  skill: { id: number; name: string; version: number; active: boolean; latest_version: number | null };
  params: Record<string, string | number | boolean | null>;
  cron_schedule: string;
  timezone: string;
  status: "active" | "paused";
  next_run_at: string | null;
  created_at: string;
  last_run: Omit<TaskRun, "task_id"> | null;
};

export type TaskInput = {
  name: string;
  skill: string;
  cron_schedule: string;
  timezone?: string;
  status?: "active" | "paused";
};

// Privacy (backend/app/privacy.py) -------------------------------------------------------------

export type ServiceLocation = "offline" | "this device" | "local network" | "internet";
export type DataService = { name: string; host: string | null; location: ServiceLocation };
export type RedactMode = "auto" | "on" | "off";

export type PrivacySummary = {
  model: DataService;
  embeddings: DataService | null;
  /** Voice, photos, the larger model, phone pushes, Google: where else data goes. */
  other_services: (DataService & { purpose: string; sends: string })[];
  redaction: { mode: RedactMode; active: boolean };
  retention_days: number | null;
  private_chat_hours: number;
  stored: {
    conversations: number;
    messages: number;
    memories: number;
    tool_calls: number;
    tasks: number;
    calendar_events: number;
    notes: number;
  };
  oldest_message_at: string | null;
  encrypted: string[];
  plaintext: string[];
};

// Plan & usage (backend/app/plans.py, usage.py) ------------------------------------------------

export type Plan = {
  id: string;
  name: string;
  price: string;
  summary: string;
  semantic_search: boolean;
  max_memories: number | null;
  max_active_tasks: number | null;
  monthly_tokens: number | null;
  features: string[];
};

export type License = {
  id: string;
  plan: string;
  licensee: string;
  issued: string;
  expires: string | null;
  status: "active" | "grace" | "expired";
};

export type UsageTotals = {
  chat_turns: number;
  task_runs: number;
  model_calls: number;
  estimated_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  tokens: number;
  cost_usd: number | null;
};

/** `limit` null = no limit on this plan. */
export type Limit = { used: number; limit: number | null };

export type Billing = {
  plan: Plan;
  /** license: from a key · preview: development, no key needed · free: no license. */
  source: "license" | "preview" | "free";
  license: License | null;
  problem: string | null;
  limits: { memories: Limit; active_tasks: Limit; monthly_tokens: Limit; semantic_search: boolean };
  usage: {
    today: UsageTotals;
    month: UsageTotals;
    month_started: string;
    renews: string;
    estimated: boolean;
    prices_set: boolean;
  };
  plans: Plan[];
  purchase_url: string | null;
};

// Day-to-day features (backend/app/routers/everyday.py) -------------------------------------

/** Your verdict on a reply; `share` lets it become fine-tuning data on your server. */
export type Feedback = { rating: 1 | -1; correction: string | null; share: boolean };

/** What this server has set up, so the app shows only what works. */
export type Features = {
  voice: boolean;
  photos: boolean;
  larger_model: string | null;
  google: boolean;
  spotify?: boolean;
  microsoft?: boolean;
  telegram?: boolean;
  phone_push: boolean;
  desktop: boolean;
  unread: number;
  feedback: { good: number; bad: number; shared: number };
};

export type AppNotification = {
  id: number;
  kind: "reminder" | "task" | "system" | "watch" | "meeting" | "learning";
  title: string;
  body: string | null;
  ref: string | null;
  created_at: string;
  read_at: string | null;
};

export type Reminder = { id: number; text: string; due: string; repeat: "daily" | "weekdays" | "weekly" | null; status: string };

export type DocumentFile = {
  id: number;
  name: string;
  kind: "pdf" | "text" | "image";
  bytes: number;
  characters: number;
  preview: string;
  created_at: string;
  warning?: string | null;
};

export type Integration = {
  provider: "google" | "spotify" | "microsoft" | "homeassistant" | "telegram";
  label?: string;
  configured: boolean;
  connected: boolean;
  account: string | null;
  connected_at: string | null;
};

// Jarvis: approvals, the remote, routines, what Custodian did, and its settings ----------------

/** A PC action you asked for from the app or Telegram, waiting for your OK (GET /approvals). */
export type Approval = {
  id: number;
  title: string;
  details: string;
  origin: "app" | "telegram";
  seconds_left: number;
};

export type RemoteReply = { reply: string; conversation_id: number; desktop: boolean };

export type Routine = {
  id: number;
  name: string;
  phrases: string[];
  steps: string[];
  needs_approval: boolean;
  created_at: string;
  last_run_at: string | null;
};

export type Activity = {
  id: number;
  tool: string;
  function: string;
  status: string;
  args: unknown;
  error: string | null;
  when: string;
  can_undo: boolean;
  undo: string | null;
  undone_at: string | null;
};

export type VoiceLanguage = "auto" | "en" | "hi" | "mr";

/** GET /settings/prefs: Custodian's other settings (backend/app/settings_store.py). */
export type Prefs = {
  announce: boolean;
  quiet_from: string;
  quiet_until: string;
  briefing: boolean;
  meeting_heads_up: number;
  pc_index: boolean;
  pc_index_folders: string[];
  nightly_learning: boolean;
  remote_approvals: boolean;
  telegram_alerts: boolean;
  voice_language: VoiceLanguage;
};

export type LearningStatus = {
  enabled: boolean;
  new_examples: number;
  last_run: { at: string; outcome: string; summary: string } | null;
};
