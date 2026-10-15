// The message list. Your bubbles sit on the right in the accent colour (white text,
// 6:1 contrast); replies sit on the left with a provenance line saying what they used.
// The list follows new messages, and failed sends get a Retry button. Under each reply:
// read it aloud, and rate it (FeedbackSheet).

import Ionicons from "@expo/vector-icons/Ionicons";
import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, FlatList, Pressable, Text, View } from "react-native";

import { toolLabel } from "../lib/format";
import { stopSpeech, toggleSpeech } from "../lib/media";
import { radius, space, themed, useTokens } from "../lib/tokens";
import type { Feedback, Used } from "../lib/types";
import { FeedbackSheet } from "./FeedbackSheet";
import { Chip, statusTone } from "./ui";

export type ChatItem = {
  key: string;
  role: "user" | "assistant";
  content: string;
  used?: Used;
  /** The server's id for a final reply, which can be rated. */
  messageId?: number;
  feedback?: Feedback | null;
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
  "Remind me to drink water at 4pm",
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
  actions: { flexDirection: "row", gap: 2, marginLeft: -6 },
  action: { width: 34, height: 30, alignItems: "center", justifyContent: "center", borderRadius: radius.sm },
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
  if (checks?.larger_model) chips.push({ key: "l", label: "Answered by the larger model", tone: "accent" });
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
  onFeedback,
}: {
  items: ChatItem[];
  pending?: boolean;
  onRetry?: (item: ChatItem) => void;
  onSuggestion?: (text: string) => void;
  onFeedback?: (messageId: number, feedback: Feedback | null) => void;
}) {
  const s = useStyles();
  const t = useTokens();
  const listRef = useRef<FlatList<ChatItem>>(null);
  const [speaking, setSpeaking] = useState<string | null>(null);
  const [rating, setRating] = useState<{ item: ChatItem; value: 1 | -1 } | null>(null);

  useEffect(() => stopSpeech, []);

  useEffect(() => {
    const id = setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 50);
    return () => clearTimeout(id);
  }, [items.length, pending]);

  return (
    <>
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
            {!mine && !item.failed ? (
              <View style={s.actions}>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={speaking === item.key ? "Stop reading aloud" : "Read aloud"}
                  style={s.action}
                  onPress={() => setSpeaking(toggleSpeech(item.key, item.content, () => setSpeaking(null)) ? item.key : null)}
                >
                  <Ionicons name={speaking === item.key ? "stop-circle-outline" : "volume-medium-outline"} size={18} color={t.inkSoft} />
                </Pressable>
                {item.messageId != null && onFeedback ? (
                  <>
                    <Pressable accessibilityRole="button" accessibilityLabel="Good reply" accessibilityState={{ selected: item.feedback?.rating === 1 }}
                      style={s.action} onPress={() => setRating({ item, value: 1 })}>
                      <Ionicons name={item.feedback?.rating === 1 ? "thumbs-up" : "thumbs-up-outline"} size={17}
                        color={item.feedback?.rating === 1 ? t.accent2 : t.inkSoft} />
                    </Pressable>
                    <Pressable accessibilityRole="button" accessibilityLabel="Bad reply" accessibilityState={{ selected: item.feedback?.rating === -1 }}
                      style={s.action} onPress={() => setRating({ item, value: -1 })}>
                      <Ionicons name={item.feedback?.rating === -1 ? "thumbs-down" : "thumbs-down-outline"} size={17}
                        color={item.feedback?.rating === -1 ? t.danger : t.inkSoft} />
                    </Pressable>
                  </>
                ) : null}
              </View>
            ) : null}
          </View>
        );
      }}
    />
    <FeedbackSheet
      messageId={rating?.item.messageId ?? null}
      rating={rating?.value ?? 1}
      current={rating?.item.feedback}
      onClose={() => setRating(null)}
      onSaved={(feedback) => rating?.item.messageId != null && onFeedback?.(rating.item.messageId, feedback)}
    />
    </>
  );
}
