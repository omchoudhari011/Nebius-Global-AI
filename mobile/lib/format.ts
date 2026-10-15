// Display helpers: dates in the device's locale and plain-English cron schedules.

import { Alert, Platform } from "react-native";

const dateTime = new Intl.DateTimeFormat(undefined, {
  weekday: "short",
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});
const timeOnly = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : dateTime.format(d);
}

/** "14:05" today, otherwise "Fri 2 Oct, 14:05". */
export function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toDateString() === new Date().toDateString() ? timeOnly.format(d) : dateTime.format(d);
}

const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

/** Common cron patterns in words; anything else is shown as-is. */
export function describeCron(cron: string): string {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return cron;
  const [minute, hour, dom, month, dow] = parts;
  if (minute === "0" && hour === "*" && dom === "*" && month === "*" && dow === "*") return "Every hour";
  const every = /^\*\/(\d+)$/.exec(minute);
  if (every && hour === "*" && dom === "*" && month === "*" && dow === "*") return `Every ${every[1]} minutes`;
  if (!/^\d+$/.test(minute) || !/^\d+$/.test(hour) || dom !== "*" || month !== "*") return cron;
  const at = `${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;
  if (dow === "*") return `Every day at ${at}`;
  if (dow === "1-5" || dow === "mon-fri") return `Weekdays at ${at}`;
  if (dow === "0,6" || dow === "6,0" || dow === "sat,sun") return `Weekends at ${at}`;
  if (/^[0-6]$/.test(dow)) return `Every ${DAYS[Number(dow)]} at ${at}`;
  return cron;
}

export function deviceTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function toolLabel(tool: string, scope?: string): string {
  const name = tool.charAt(0).toUpperCase() + tool.slice(1);
  return scope ? `${name} · ${scope}` : name;
}

/** Ask before a destructive action. Alert is a no-op on web, so web uses window.confirm. */
export function confirmAction(title: string, message: string, confirmLabel = "Delete"): Promise<boolean> {
  if (Platform.OS === "web") {
    return Promise.resolve(typeof window !== "undefined" && window.confirm(`${title}\n\n${message}`));
  }
  return new Promise((resolve) => {
    Alert.alert(
      title,
      message,
      [
        { text: "Cancel", style: "cancel", onPress: () => resolve(false) },
        { text: confirmLabel, style: "destructive", onPress: () => resolve(true) },
      ],
      { cancelable: true, onDismiss: () => resolve(false) },
    );
  });
}
