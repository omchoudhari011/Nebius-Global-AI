// The start of a new chat: greeting by name, memory cards, and suggestion chips.
// Enhanced with rich cybernetic gradients, radiant AI core halo, and glowing memory icons.

import Ionicons from "@expo/vector-icons/Ionicons";
import type { ComponentProps } from "react";
import { LinearGradient } from "expo-linear-gradient";
import { Pressable, StyleSheet, View } from "react-native";

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
  orbDish: {
    width: 136,
    height: 136,
    borderRadius: 68,
    alignItems: "center",
    justifyContent: "center",
    overflow: "hidden",
    ...softShadow(t, 2),
  },
  hello: { alignItems: "center", gap: space.xs, marginTop: space.sm },
  title: { color: t.ink, fontSize: 32, lineHeight: 40, fontWeight: "800", textAlign: "center", letterSpacing: 0.5 },
  sub: { color: t.inkSoft, fontSize: 16, lineHeight: 23, textAlign: "center", maxWidth: 440 },
  card: {
    width: "100%",
    maxWidth: 460,
    borderRadius: radius.xl,
    padding: space.xl,
    gap: space.md,
    overflow: "hidden",
    ...softShadow(t, 2),
  },
  cardHeader: { flexDirection: "row", alignItems: "center", gap: space.sm },
  cardTitle: { color: t.ink, fontSize: 16, fontWeight: "800", flex: 1, letterSpacing: 0.3 },
  memory: { flexDirection: "row", gap: space.md, alignItems: "flex-start" },
  memoryIcon: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: "center",
    justifyContent: "center",
    overflow: "hidden",
    ...softShadow(t, 1),
  },
  memoryText: { flex: 1, color: t.ink, fontSize: 15, lineHeight: 21, paddingTop: 6 },
  seeAll: { color: t.accentText, fontSize: 14, fontWeight: "700" },
  emptyMemory: { color: t.inkSoft, fontSize: 14, lineHeight: 20 },
  chips: { flexDirection: "row", flexWrap: "wrap", justifyContent: "center", gap: space.sm, maxWidth: 580 },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    borderRadius: radius.pill,
    paddingHorizontal: 16,
    paddingVertical: 10,
    overflow: "hidden",
    ...softShadow(t, 1),
  },
  chipText: { color: t.ink, fontSize: 14, fontWeight: "700" },
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
      <LinearGradient
        colors={t.gradients.hudBezel}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={s.orbDish}
      >
        <Orb size={100} mood={thinking ? "thinking" : "idle"} />
      </LinearGradient>
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
        <LinearGradient
          colors={t.gradients.card}
          start={{ x: 0, y: 0 }}
          end={{ x: 0.9, y: 1 }}
          style={s.card}
        >
          <View style={s.cardHeader}>
            <Ionicons name="sparkles" size={18} color={t.accent} />
            <Text style={s.cardTitle}>Things I keep in mind</Text>
            {memories.length > 3 ? (
              <Pressable accessibilityRole="button" onPress={onSeeMemories} hitSlop={8}>
                <Text style={s.seeAll}>See all ({memories.length})</Text>
              </Pressable>
            ) : null}
          </View>
          {recent.length ? (
            recent.map((m) => (
              <View key={m.id} style={s.memory}>
                <View style={s.memoryIcon}>
                  <LinearGradient
                    colors={t.gradients.primary}
                    start={{ x: 0, y: 0 }}
                    end={{ x: 1, y: 1 }}
                    style={StyleSheet.absoluteFill}
                  />
                  <Ionicons name={MEMORY_ICON[m.type]} size={16} color={t.onAccent} />
                </View>
                <Text style={s.memoryText}>{toYou(m.content)}</Text>
              </View>
            ))
          ) : (
            <Text style={s.emptyMemory}>
              As we talk, I'll pick up your preferences, projects and people you mention so you don't have to repeat
              yourself. You can see and erase them in Tools & Data anytime.
            </Text>
          )}
        </LinearGradient>
      ) : null}

      <View style={s.chips}>
        {SUGGESTIONS.map((item) => (
          <Pressable
            key={item.label}
            accessibilityRole="button"
            onPress={() => onSuggestion(item.text)}
            style={({ pressed }) => [s.chip, pressed && { transform: [{ scale: 0.96 }] }]}
          >
            <LinearGradient
              colors={["rgba(18, 44, 38, 0.90)", "rgba(10, 26, 23, 0.96)"]}
              start={{ x: 0, y: 0 }}
              end={{ x: 1, y: 1 }}
              style={StyleSheet.absoluteFill}
            />
            <Ionicons name={item.icon} size={16} color={t.accent} />
            <Text style={s.chipText}>{item.label}</Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}
