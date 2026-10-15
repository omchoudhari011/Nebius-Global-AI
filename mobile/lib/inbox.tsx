// The inbox and the server's feature list, shared by every tab: polls every 30 s while
// signed in, announces new notifications (web), keeps phone reminders scheduled, and exposes
// the unread count for the bell.

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AppState } from "react-native";

import * as api from "./api";
import { announce, enableNotifications, markAnnounced, syncReminders } from "./notify";
import type { AppNotification, Features } from "./types";

type InboxValue = {
  features: Features | null;
  items: AppNotification[];
  unread: number;
  refresh: () => Promise<void>;
  markAllRead: () => Promise<void>;
};

const InboxContext = createContext<InboxValue | null>(null);
const POLL_MS = 30_000;

export function InboxProvider({ children }: { children: ReactNode }) {
  const [features, setFeatures] = useState<Features | null>(null);
  const [items, setItems] = useState<AppNotification[]>([]);
  const first = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const [f, list, reminders] = await Promise.all([api.getFeatures(), api.getNotifications(), api.getReminders()]);
      setFeatures(f);
      setItems(list);
      if (first.current) {
        first.current = false;
        markAnnounced(list); // what was waiting when the app opened shows in the inbox, not as pop-ups
        await enableNotifications().catch(() => false);
      } else {
        announce(list);
      }
      await syncReminders(reminders).catch(() => undefined);
    } catch {
      // offline or signed out: try again on the next tick
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    const sub = AppState.addEventListener("change", (state) => state === "active" && void refresh());
    return () => {
      clearInterval(timer);
      sub.remove();
    };
  }, [refresh]);

  const markAllRead = useCallback(async () => {
    await api.markAllNotificationsRead();
    setItems((prev) => prev.map((item) => ({ ...item, read_at: item.read_at ?? new Date().toISOString() })));
  }, []);

  const value = useMemo(
    () => ({ features, items, unread: items.filter((i) => !i.read_at).length, refresh, markAllRead }),
    [features, items, refresh, markAllRead],
  );
  return <InboxContext.Provider value={value}>{children}</InboxContext.Provider>;
}

export function useInbox(): InboxValue {
  const value = useContext(InboxContext);
  if (!value) throw new Error("useInbox must be used inside <InboxProvider>");
  return value;
}
