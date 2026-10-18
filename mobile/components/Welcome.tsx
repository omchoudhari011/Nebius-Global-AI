// The start of a new chat: a greeting by name for the time of day, a few things Custodian
// remembers about you - it's a memory-first assistant, and this is where you see it - and
// some things to ask. In an off-the-record chat the memories stay hidden.

import Ionicons from "@expo/vector-icons/Ionicons";
import type { ComponentProps } from "react";
import { Pressable, View } from "react-native";

import { radius, softShadow, space, themed, useTokens } from "../lib/tokens";
import type { Memory } from "../lib/types";
import { Orb } from "./Orb";
import { Text } from "./Typography";

type IconName = ComponentProps<typeof Ionicons>["name"];

const SUGGESTIONS: { icon: IconName; label: string; text: string }[] = [
  { icon: "sunny-outline", label: "Plan my day", text: "Give me a summary of my day" },
  { icon: "heart-outline", label: "What do you remember about me?", text: "What do you remember about me?" },
  { icon: "musical-notes-outline", label: "Play some music", text: "Play some lofi music" },
  { icon: "alarm-outline", label: "Remind me at 4pm", text: "Remind me to drink water at 4pm" },
  { icon: "partly-sunny-outline", label: "Weather today", text: "What's the weather today?" },
];

const MEMORY_ICON: Record<Memory["type"], IconName> = {
  preference: "heart-outline",
  semantic: "sparkles-outline",
  episodic: "calendar-outline",
};

/** Memories are saved about "them"; spoken to you, they're about "you". */
export function toYou(text: string): string {
  const swaps: [RegExp, string][] = [
    [/\bThey're\b/g, "You're"], [/\bthey're\b/g, "you're"], [/\bThey are\b/g, "You are"], [/\bthey are\b/g, "you are"],
    [/\bThey\b/g, "You"], [/\bthey\b/g, "you"], [/\bTheir\b/g, "Your"], [/\btheir\b/g, "your"],
    [/\bthemselves\b/g, "yourself"], [/\bthem\b/g, "you"],
  ];
  return swaps.reduce((s, [pattern, word]) => s.replace(pattern, word), text);
}

/** Their first name, if a memory says it ("Their name is Om Choudhari." -> "Om"). */
export function firstName(memories: Memory[]): string | null {
  for (const m of memories) {
    // Their own name only: not "Their sister's name is Meera", not "They called Rahul".
    const match = /^\s*(?:their|my|your|the user's)\s+(?:full\s+)?name is\s+([A-Z][\p{L}'-]+)/iu.exec(m.content);
    if (match) return match[1].charAt(0).toUpperCase() + match[1].slice(1);
  }
  return null;
}

export function greeting(name: string | null, now = new Date()): string {
  const hour = now.getHours();
  if (hour < 5) return name ? `Up late, ${name}?` : "Up late?";
  const part = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  return name ? `${part}, ${name}` : part;
}

const useStyles = themed((t) => ({
  wrap: { flex: 1, alignItems: "center", justifyContent: "center", paddingVertical: space.xl, gap: space.lg, width: "100%" },
  hello: { alignItems: "center", gap: space.xs, marginTop: space.md },
  title: { color: t.ink, fontSize: 30, lineHeight: 38, fontWeight: "800", textAlign: "center" },
  sub: { color: t.inkSoft, fontSize: 16, lineHeight: 23, textAlign: "center", maxWidth: 420 },
  card: { width: "100%", maxWidth: 460, backgroundColor: t.surface, borderRadius: radius.lg, padding: space.lg, gap: space.md,
    borderWidth: 1, borderColor: t.hairline, ...softShadow(t) },
  cardHeader: { flexDirection: "row", alignItems: "center", gap: space.sm },
  cardTitle: { color: t.ink, fontSize: 16, fontWeight: "800", flex: 1 },
  memory: { flexDirection: "row", gap: space.md, alignItems: "flex-start" },
  memoryIcon: { width: 30, height: 30, borderRadius: 15, backgroundColor: t.accentSoft, alignItems: "center", justifyContent: "center" },
  memoryText: { flex: 1, color: t.ink, fontSize: 15, lineHeight: 21, paddingTop: 4 },
  seeAll: { color: t.accentText, fontSize: 14, fontWeight: "700" },
  emptyMemory: { color: t.inkSoft, fontSize: 14, lineHeight: 20 },
  chips: { flexDirection: "row", flexWrap: "wrap", justifyContent: "center", gap: space.sm, maxWidth: 560 },
  chip: { flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: t.surface, borderRadius: radius.pill,
    borderWidth: 1, borderColor: t.hairline, paddingHorizontal: 14, paddingVertical: 9, ...softShadow(t) },
  chipText: { color: t.ink, fontSize: 14, fontWeight: "600" },
}));

export function Welcome({
  memories,
  offTheRecord,
  thinking,
  onSuggestion,
  onSeeMemories,
}: {
  memories: Memory[];
  offTheRecord: boolean;
  thinking?: boolean;
  onSuggestion: (text: string) => void;
  onSeeMemories: () => void;
}) {
  const s = useStyles();
  const t = useTokens();
  const name = firstName(memories);
  const recent = [...memories].sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1)).slice(0, 3);

  return (
    <View style={s.wrap}>
      <Orb size={92} mood={thinking ? "thinking" : "idle"} />
      <View style={s.hello}>
        <Text accessibilityRole="header" style={s.title}>
          {greeting(name)}
        </Text>
        <Text style={s.sub}>
          {offTheRecord
            ? "This one's just between us - I won't remember any of it."
            : "I'm here. What's on your mind?"}
        </Text>
      </View>

      {!offTheRecord ? (
        <View style={s.card}>
          <View style={s.cardHeader}>
            <Ionicons name="heart" size={16} color={t.accent} />
            <Text style={s.cardTitle}>Things I remember</Text>
            {memories.length ? (
              <Pressable accessibilityRole="link" onPress={onSeeMemories} hitSlop={8}>
                <Text style={s.seeAll}>See all {memories.length}</Text>
              </Pressable>
            ) : null}
          </View>
          {recent.length ? (
            recent.map((m) => (
              <View key={m.id} style={s.memory}>
                <View style={s.memoryIcon}>
                  <Ionicons name={MEMORY_ICON[m.type]} size={15} color={t.accentText} />
                </View>
                <Text style={s.memoryText} numberOfLines={2}>
                  {toYou(m.content)}
                </Text>
              </View>
            ))
          ) : (
            <Text style={s.emptyMemory}>
              Nothing yet. Tell me about yourself - your name, what you like, what's coming up - and I'll keep it in mind,
              in every chat.
            </Text>
          )}
        </View>
      ) : null}

      <View style={s.chips}>
        {SUGGESTIONS.map((item) => (
          <Pressable key={item.label} accessibilityRole="button" style={s.chip} onPress={() => onSuggestion(item.text)}>
            <Ionicons name={item.icon} size={16} color={t.accentText} />
            <Text style={s.chipText}>{item.label}</Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}
