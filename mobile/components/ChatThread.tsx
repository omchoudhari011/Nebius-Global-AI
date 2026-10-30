// The message list. Your messages sit on the right in a soft peach bubble; Custodian's sit on
// the left on a white card beside its orb, with a line saying what each reply used. The
// list follows new messages, and failed sends get a Retry button. Under each reply: read it
// aloud, and rate it (FeedbackSheet). An empty chat shows `empty` (the Welcome screen).

import Ionicons from "@expo/vector-icons/Ionicons";
import { LinearGradient } from "expo-linear-gradient";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { FlatList, Pressable, View } from "react-native";

import { toolLabel } from "../lib/format";
import { stopSpeech, toggleSpeech } from "../lib/media";
import { radius, softShadow, space, themed, useTokens } from "../lib/tokens";
import type { Feedback, Used } from "../lib/types";
import { FeedbackSheet } from "./FeedbackSheet";
import { Orb } from "./Orb";
import { Text } from "./Typography";
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

const useStyles = themed((t) => ({
  list: { paddingHorizontal: space.lg, paddingVertical: space.lg, gap: space.md, flexGrow: 1 },
  row: { maxWidth: "86%", gap: 6 },
  rowUser: { alignSelf: "flex-end", alignItems: "flex-end" },
  rowAssistant: { alignSelf: "flex-start" },
  bubble: { paddingHorizontal: 16, paddingVertical: 12, borderRadius: radius.lg },
  bubbleUser: {
    borderBottomRightRadius: 6,
    ...softShadow(t, 2),
  },
  bubbleAssistant: {
    borderBottomLeftRadius: 6,
    ...softShadow(t, 1),
  },
  textUser: { color: t.ink, fontSize: 16, lineHeight: 23 },
  textAssistant: { color: t.ink, fontSize: 16, lineHeight: 23 },
  assistantLine: { flexDirection: "row", alignItems: "flex-end", gap: space.sm },
  under: { paddingLeft: 38, gap: 6 },
  failed: { color: t.danger, fontSize: 13 },
  retry: { color: t.danger, fontSize: 13, fontWeight: "700", textDecorationLine: "underline" },
  provenance: { flexDirection: "row", flexWrap: "wrap", gap: 4 },
  thinking: { flexDirection: "row", alignItems: "center", gap: space.sm, alignSelf: "flex-start", paddingVertical: space.xs },
  thinkingBubble: {
    borderRadius: radius.lg,
    borderBottomLeftRadius: 6,
    paddingHorizontal: 16,
    paddingVertical: 10,
    ...softShadow(t, 1),
  },
  thinkingText: { color: t.accentText, fontSize: 14, fontWeight: "700", letterSpacing: 0.5 },
  actions: { flexDirection: "row", gap: 6, marginLeft: -4, marginTop: 2 },
  action: { width: 34, height: 34, alignItems: "center", justifyContent: "center", borderRadius: radius.pill, backgroundColor: t.surface, ...softShadow(t, 1) },
}));

function Provenance({ used }: { used: Used }) {
  const s = useStyles();
  const chips: { key: string; label: string; tone: Parameters<typeof Chip>[0]["tone"] }[] = [];
  const matched = used.memories.filter((m) => m.matched !== false).length;
  if (matched) chips.push({ key: "m", label: `Remembered ${matched} ${matched === 1 ? "thing" : "things"}`, tone: "neutral" });
  if (used.earlier.length) {
    chips.push({ key: "e", label: `From ${used.earlier.length} earlier ${used.earlier.length === 1 ? "chat" : "chats"}`, tone: "neutral" });
  }
  used.tools.forEach((tool) =>
    chips.push({
      key: `t${tool.id}`,
      label: `${toolLabel(tool.tool, tool.scope)} ${tool.status === "succeeded" ? "✓" : tool.status.replace("_", " ")}`,
      tone: statusTone(tool.status),
    }),
  );
  if (used.remembered.length) {
    chips.push({ key: "r", label: `Saved ${used.remembered.length} new ${used.remembered.length === 1 ? "memory" : "memories"}`, tone: "accent" });
  }
  const checks = used.checks;
  if (checks?.status === "corrected") chips.push({ key: "c", label: "Double-checked and corrected", tone: "accent" });
  if (checks?.status === "flagged") chips.push({ key: "c", label: "Couldn't verify everything", tone: "warning" });
  if (checks?.larger_model) chips.push({ key: "l", label: "Thought harder", tone: "accent" });
  if (checks?.secrets_withheld) {
    chips.push({ key: "s", label: `Kept ${checks.secrets_withheld} ${checks.secrets_withheld === 1 ? "secret" : "secrets"} private`, tone: "success" });
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
  empty,
  onRetry,
  onFeedback,
}: {
  items: ChatItem[];
  pending?: boolean;
  empty?: ReactNode;
  onRetry?: (item: ChatItem) => void;
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
        ListEmptyComponent={!pending && empty ? () => <>{empty}</> : null}
        ListFooterComponent={
          pending ? (
            <View style={s.thinking} accessibilityLiveRegion="polite" accessibilityLabel="Custodian is thinking">
              <Orb size={28} mood="thinking" />
              <LinearGradient
                colors={t.gradients.assistantBubble}
                start={{ x: 0, y: 0 }}
                end={{ x: 1, y: 0 }}
                style={s.thinkingBubble}
              >
                <Text style={s.thinkingText}>Thinking…</Text>
              </LinearGradient>
            </View>
          ) : null
        }
        renderItem={({ item }) => {
          const mine = item.role === "user";
          return (
            <View style={[s.row, mine ? s.rowUser : s.rowAssistant]}>
              {mine ? (
                <LinearGradient
                  colors={t.gradients.userBubble}
                  start={{ x: 0, y: 0 }}
                  end={{ x: 1, y: 1 }}
                  style={[s.bubble, s.bubbleUser]}
                >
                  <Text selectable style={s.textUser}>
                    {item.content}
                  </Text>
                </LinearGradient>
              ) : (
                <View style={s.assistantLine}>
                  <Orb size={28} still />
                  <LinearGradient
                    colors={t.gradients.assistantBubble}
                    start={{ x: 0, y: 0 }}
                    end={{ x: 0.9, y: 1 }}
                    style={[s.bubble, s.bubbleAssistant, { flexShrink: 1 }]}
                  >
                    <Text selectable style={s.textAssistant}>
                      {item.content}
                    </Text>
                  </LinearGradient>
                </View>
              )}
              <View style={mine ? { alignItems: "flex-end", gap: 6 } : s.under}>
                {item.failed ? (
                  <Text style={s.failed} accessibilityRole="alert">
                    {item.error ?? "That didn't send."}{" "}
                    {onRetry ? (
                      <Text accessibilityRole="button" style={s.retry} onPress={() => onRetry(item)}>
                        Try again
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
