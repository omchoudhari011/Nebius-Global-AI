// Getting reminders in front of you.
//
// The server is the source of truth: it delivers due reminders to the in-app inbox (and to
// a phone through ntfy, if set up). On top of that:
//   * phones schedule every upcoming reminder as a local notification (expo-notifications),
//     so it goes off on time even if the phone can't reach the server then;
//   * the web app shows a browser notification for new inbox items while it's open.

import { Platform } from "react-native";

import type { AppNotification, Reminder } from "./types";

const PREFIX = "custodian-reminder-";

async function nativeNotifications() {
  return import("expo-notifications");
}

/** Asks for permission once; returns whether notifications can be shown. */
export async function enableNotifications(): Promise<boolean> {
  if (Platform.OS === "web") {
    if (typeof Notification === "undefined") return false;
    if (Notification.permission === "granted") return true;
    if (Notification.permission === "denied") return false;
    return (await Notification.requestPermission()) === "granted";
  }
  const Notifications = await nativeNotifications();
  Notifications.setNotificationHandler({
    handleNotification: async () => ({ shouldShowBanner: true, shouldShowList: true, shouldPlaySound: true, shouldSetBadge: false }),
  });
  const current = await Notifications.getPermissionsAsync();
  if (current.granted) return true;
  return (await Notifications.requestPermissionsAsync()).granted;
}

/** Phones: make the scheduled local notifications match the server's upcoming reminders. */
export async function syncReminders(reminders: Reminder[]): Promise<void> {
  if (Platform.OS === "web") return;
  const Notifications = await nativeNotifications();
  const wanted = new Map(reminders.filter((r) => new Date(r.due).getTime() > Date.now()).map((r) => [`${PREFIX}${r.id}-${r.due}`, r]));
  const scheduled = await Notifications.getAllScheduledNotificationsAsync();
  for (const item of scheduled) {
    if (item.identifier.startsWith(PREFIX) && !wanted.has(item.identifier)) {
      await Notifications.cancelScheduledNotificationAsync(item.identifier);
    }
  }
  const have = new Set(scheduled.map((item) => item.identifier));
  for (const [identifier, reminder] of wanted) {
    if (have.has(identifier)) continue;
    await Notifications.scheduleNotificationAsync({
      identifier,
      content: { title: "Reminder", body: reminder.text },
      trigger: { type: Notifications.SchedulableTriggerInputTypes.DATE, date: new Date(reminder.due) },
    });
  }
}

const shown = new Set<number>();

/** Web: show a browser notification for each new unread item (once per item, while open). */
export function announce(items: AppNotification[]): void {
  if (Platform.OS !== "web" || typeof Notification === "undefined" || Notification.permission !== "granted") return;
  for (const item of items) {
    if (item.read_at || shown.has(item.id)) continue;
    shown.add(item.id);
    new Notification(item.kind === "reminder" ? "Reminder" : "Custodian", { body: item.title, tag: `custodian-${item.id}` });
  }
}

/** Marks items as already announced (the ones that were there when the app opened). */
export function markAnnounced(items: AppNotification[]): void {
  items.forEach((item) => shown.add(item.id));
}
