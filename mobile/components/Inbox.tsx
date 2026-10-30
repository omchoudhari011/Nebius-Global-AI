// The bell in the chat header, with the unread count, and the inbox it opens: reminders that
// went off and scheduled tasks that finished. Opening a task's result shows that chat.

import Ionicons from "@expo/vector-icons/Ionicons";
import { LinearGradient } from "expo-linear-gradient";
import { useState } from "react";
import { FlatList, Modal, Pressable, View } from "react-native";
import { Text } from "./Typography";

import { formatWhen } from "../lib/format";
import { useInbox } from "../lib/inbox";
import { neuExtruded, radius, space, themed, useTokens } from "../lib/tokens";
import type { AppNotification } from "../lib/types";
import { Button } from "./ui";

const useStyles = themed((t) => ({
  bell: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  badge: {
    position: "absolute", top: 4, right: 2, minWidth: 18, height: 18, borderRadius: 9, paddingHorizontal: 4,
    backgroundColor: t.danger, alignItems: "center", justifyContent: "center",
  },
  badgeText: { color: "#fff", fontSize: 11, fontWeight: "800" },
  backdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.55)", justifyContent: "flex-start", padding: space.lg, paddingTop: 72 },
  sheet: { borderRadius: radius.xl, padding: space.xl, gap: space.md, width: "100%", maxWidth: 520, maxHeight: "80%", alignSelf: "center", ...neuExtruded(t, 2) },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  title: { color: t.ink, fontSize: 18, fontWeight: "700" },
  item: { paddingVertical: space.sm, borderBottomWidth: 1, borderBottomColor: t.border, flexDirection: "row", gap: space.md },
  itemTitle: { color: t.ink, fontSize: 15, fontWeight: "600" },
  itemBody: { color: t.inkSoft, fontSize: 13, lineHeight: 18 },
  unreadDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: t.accent, marginTop: 6 },
  empty: { color: t.inkSoft, fontSize: 14, paddingVertical: space.lg, textAlign: "center" },
}));

export function InboxButton({ onOpenConversation }: { onOpenConversation: (id: number) => void }) {
  const s = useStyles();
  const t = useTokens();
  const { items, unread, markAllRead } = useInbox();
  const [open, setOpen] = useState(false);

  function openItem(item: AppNotification) {
    const match = /^conversation:(\d+)$/.exec(item.ref ?? "");
    if (match) {
      setOpen(false);
      onOpenConversation(Number(match[1]));
    }
  }

  return (
    <>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={unread ? `Notifications, ${unread} unread` : "Notifications"}
        style={s.bell}
        onPress={() => setOpen(true)}
      >
        <Ionicons name={unread ? "notifications" : "notifications-outline"} size={22} color={unread ? t.accent2 : t.inkSoft} />
        {unread ? (
          <LinearGradient colors={t.gradients.gaugeRed} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.badge}>
            <Text style={s.badgeText}>{unread > 9 ? "9+" : unread}</Text>
          </LinearGradient>
        ) : null}
      </Pressable>
      <Modal visible={open} transparent animationType="fade" onRequestClose={() => setOpen(false)}>
        <Pressable style={s.backdrop} onPress={() => setOpen(false)} accessibilityLabel="Close notifications">
          <LinearGradient
            colors={t.gradients.card}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={[s.sheet, { overflow: "hidden" }]}
          >
            <View style={s.header}>
              <Text style={s.title}>Notifications</Text>
              {unread ? <Button compact variant="ghost" label="Mark all read" onPress={() => void markAllRead()} /> : null}
            </View>
            <FlatList
              data={items}
              keyExtractor={(item) => String(item.id)}
              ListEmptyComponent={<Text style={s.empty}>Nothing yet. Reminders and finished tasks show up here.</Text>}
              renderItem={({ item }) => (
                <Pressable style={s.item} onPress={() => openItem(item)} accessibilityRole="button">
                  <View style={[s.unreadDot, item.read_at ? { backgroundColor: "transparent" } : null]} />
                  <View style={{ flex: 1, gap: 2 }}>
                    <Text style={s.itemTitle}>
                      {item.kind === "reminder" ? "⏰ " : ""}
                      {item.title}
                    </Text>
                    {item.body ? <Text style={s.itemBody} numberOfLines={3}>{item.body}</Text> : null}
                    <Text style={s.itemBody}>{formatWhen(item.created_at)}</Text>
                  </View>
                </Pressable>
              )}
            />
          </LinearGradient>
        </Pressable>
      </Modal>
    </>
  );
}
