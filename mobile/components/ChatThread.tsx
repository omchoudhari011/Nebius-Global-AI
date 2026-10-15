// The message list. Your bubbles sit on the right in the accent colour (white text,
// 6:1 contrast); replies sit on the left with a provenance line saying what they used.
// The list follows new messages, and failed sends get a Retry button.

import { useEffect, useRef } from "react";
import { ActivityIndicator, FlatList, Pressable, Text, View } from "react-native";

import { toolLabel } from "../lib/format";
import { radius, space, themed, useTokens } from "../lib/tokens";
import type { Used } from "../lib/types";
import { Chip, statusTone } from "./ui";

export type ChatItem = {
  key: string;
  role: "user" | "assistant";
  content: string;
  used?: Used;
  failed?: boolean;
  error?: string;
};

// Kept for components that only need the message shape.
export type Message = ChatItem;

const SUGGESTIONS = [
  "What's on my calendar today?",
  "Remember that I prefer meetings before noon",
  "Take a note: call the plumber about the kitchen tap",
  "Give me a summary of my day",
];

const useStyles = themed((t) => ({
  list: { paddingHorizontal: space.lg, paddingVertical: space.md, gap: space.sm, flexGrow: 1 },
  row: { maxWidth: "86%", gap: 6 },
  rowUser: { alignSelf: "flex-end", alignItems: "flex-end" },
  rowAssistant: { alignSelf: "flex-start" },
  bubble: { paddingHorizontal: 14, paddingVertical: 10, borderRadius: radius.lg },
  bubbleUser: { backgroundColor: t.accent, borderBottomRightRadius: radius.sm },
  bubbleAssistant: { backgroundColor: t.surface, borderWidth: 1, borderColor: t.border, borderBottomLeftRadius: radius.sm },
  textUser: { color: t.onAccent, fontSize: 16, lineHeight: 22 },
  textAssistant: { color: t.ink, fontSize: 16, lineHeight: 22 },
  failed: { color: t.danger, fontSize: 13 },
  retry: { color: t.danger, fontSize: 13, fontWeight: "700", textDecorationLine: "underline" },
  provenance: { flexDirection: "row", flexWrap: "wrap", gap: 4 },
  thinking: { flexDirection: "row", alignItems: "center", gap: space.sm, alignSelf: "flex-start", paddingVertical: space.sm },
  thinkingText: { color: t.inkSoft, fontSize: 14 },
  empty: { flex: 1, justifyContent: "center", alignItems: "center", gap: space.md, paddingVertical: space.xl },
  emptyTitle: { color: t.ink, fontSize: 18, fontWeight: "700", textAlign: "center" },
  emptyText: { color: t.inkSoft, fontSize: 14, textAlign: "center", maxWidth: 360 },
  suggestions: { flexDirection: "row", flexWrap: "wrap", justifyContent: "center", gap: space.sm, maxWidth: 520 },
  suggestion: { borderWidth: 1, borderColor: t.border, backgroundColor: t.surface, borderRadius: radius.pill, paddingHorizontal: 14, paddingVertical: 8 },
  suggestionText: { color: t.ink, fontSize: 14 },
}));

function Provenance({ used }: { used: Used }) {
  const s = useStyles();
  const chips: { key: string; label: string; tone: Parameters<typeof Chip>[0]["tone"] }[] = [];
  const matched = used.memories.filter((m) => m.matched !== false);
  matched.slice(0, 3).forEach((m) => chips.push({ key: `m${m.id}`, label: `Memory #${m.id}`, tone: "neutral" }));
  if (matched.length > 3) chips.push({ key: "m+", label: `+${matched.length - 3} memories`, tone: "neutral" });
  if (used.earlier.length) {
    chips.push({ key: "e", label: `${used.earlier.length} from earlier chats`, tone: "neutral" });
  }
  used.tools.forEach((tool) =>
    chips.push({
      key: `t${tool.id}`,
      label: `${toolLabel(tool.tool, tool.scope)} ${tool.status === "succeeded" ? "✓" : tool.status}`,
      tone: statusTone(tool.status),
    }),
  );
  if (used.remembered.length) {
    chips.push({ key: "r", label: `Saved ${used.remembered.length} ${used.remembered.length === 1 ? "memory" : "memories"}`, tone: "accent" });
  }
  const checks = used.checks;
  if (checks?.status === "corrected") chips.push({ key: "c", label: "Corrected before sending", tone: "accent" });
  if (checks?.status === "flagged") chips.push({ key: "c", label: "Unverified claim flagged", tone: "warning" });
  if (checks?.secrets_withheld) {
    chips.push({ key: "s", label: `${checks.secrets_withheld} ${checks.secrets_withheld === 1 ? "secret" : "secrets"} withheld from the model`, tone: "success" });
  }
  if (!chips.length) return null;
  const summary = `Used: ${chips.map((c) => c.label).join(", ")}`;
  return (
    <View style={s.provenance} accessibilityLabel={summary}>
      {chips.map((c) => (
        <Chip key={c.key} label={c.label} tone={c.tone} />
      ))}
    </View>
  );
}

export function ChatThread({
  items,
  pending,
  onRetry,
  onSuggestion,
}: {
  items: ChatItem[];
  pending?: boolean;
  onRetry?: (item: ChatItem) => void;
  onSuggestion?: (text: string) => void;
}) {
  const s = useStyles();
  const t = useTokens();
  const listRef = useRef<FlatList<ChatItem>>(null);

  useEffect(() => {
    const id = setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 50);
    return () => clearTimeout(id);
  }, [items.length, pending]);

  return (
    <FlatList
      ref={listRef}
      data={items}
      keyExtractor={(item) => item.key}
      contentContainerStyle={s.list}
      keyboardShouldPersistTaps="handled"
      keyboardDismissMode="interactive"
      onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: false })}
      ListEmptyComponent={
        pending ? null : (
          <View style={s.empty}>
            <Text style={s.emptyTitle}>What can I help with?</Text>
            <Text style={s.emptyText}>
              I remember what you tell me and only use your calendar and notes when you switch them on in Tools & Data.
            </Text>
            {onSuggestion ? (
              <View style={s.suggestions}>
                {SUGGESTIONS.map((text) => (
                  <Pressable key={text} accessibilityRole="button" style={s.suggestion} onPress={() => onSuggestion(text)}>
                    <Text style={s.suggestionText}>{text}</Text>
                  </Pressable>
                ))}
              </View>
            ) : null}
          </View>
        )
      }
      ListFooterComponent={
        pending ? (
          <View style={s.thinking} accessibilityLiveRegion="polite">
            <ActivityIndicator size="small" color={t.accent2} />
            <Text style={s.thinkingText}>Custodian is thinking…</Text>
          </View>
        ) : null
      }
      renderItem={({ item }) => {
        const mine = item.role === "user";
        return (
          <View style={[s.row, mine ? s.rowUser : s.rowAssistant]}>
            <View style={[s.bubble, mine ? s.bubbleUser : s.bubbleAssistant]}>
              <Text selectable style={mine ? s.textUser : s.textAssistant}>
                {item.content}
              </Text>
            </View>
            {item.failed ? (
              <Text style={s.failed} accessibilityRole="alert">
                {item.error ?? "Not sent."}{" "}
                {onRetry ? (
                  <Text accessibilityRole="button" style={s.retry} onPress={() => onRetry(item)}>
                    Retry
                  </Text>
                ) : null}
              </Text>
            ) : null}
            {item.used ? <Provenance used={item.used} /> : null}
          </View>
        );
      }}
    />
  );
}
