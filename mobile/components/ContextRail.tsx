// What shaped my reply (Active Context): what the last reply used - the skill version, the memories that matched
// (every other saved memory was in its context too), what you said in earlier chats, each
// tool call and its gate status, and anything saved to memory. On wide screens it's a
// panel beside the chat; on narrow ones a collapsible strip above the message box.

import Ionicons from "@expo/vector-icons/Ionicons";
import { LinearGradient } from "expo-linear-gradient";
import { useState, type ReactNode } from "react";
import { Pressable, ScrollView, View } from "react-native";
import { Text } from "./Typography";

import { formatWhen, toolLabel } from "../lib/format";
import { space, themed, useTokens } from "../lib/tokens";
import type { Used } from "../lib/types";
import { Chip, statusTone } from "./ui";

const TYPE_LABEL = { semantic: "fact", preference: "preference", episodic: "event" } as const;

const useStyles = themed((t) => ({
  panel: { width: 290, borderLeftWidth: 1, borderLeftColor: t.hairline },
  panelInner: { padding: space.lg, gap: space.lg },
  strip: { borderTopWidth: 1, borderTopColor: t.hairline },
  stripHeader: { flexDirection: "row", alignItems: "center", gap: space.sm, paddingHorizontal: space.lg, minHeight: 40 },
  stripBody: { maxHeight: 240 },
  stripInner: { paddingHorizontal: space.lg, paddingBottom: space.md, gap: space.md },
  heading: { fontSize: 16, fontWeight: "800", color: t.ink },
  summary: { flex: 1, color: t.inkSoft, fontSize: 13 },
  group: { gap: 6 },
  groupTitle: { fontSize: 14, fontWeight: "800", color: t.ink },
  item: { gap: 4, paddingVertical: 4 },
  itemText: { color: t.ink, fontSize: 14, lineHeight: 19 },
  muted: { color: t.inkSoft, fontSize: 13, lineHeight: 18 },
  row: { flexDirection: "row", alignItems: "center", gap: 6, flexWrap: "wrap" },
}));

function Group({ title, children }: { title: string; children: ReactNode }) {
  const s = useStyles();
  return (
    <View style={s.group}>
      <Text style={s.groupTitle}>{title}</Text>
      {children}
    </View>
  );
}

function Details({ used }: { used: Used | null }) {
  const s = useStyles();
  if (!used) {
    return <Text style={s.muted}>Send me a message and I'll show you here what I remembered, what I did and how I checked it.</Text>;
  }
  const matched = used.memories.filter((m) => m.matched !== false);
  const others = used.memories.length - matched.length;
  return (
    <>
      <Group title="Skill">
        <Text style={s.itemText}>{used.skill ? `${used.skill.name} · v${used.skill.version}` : "None"}</Text>
      </Group>
      <Group title={`What I remembered (${matched.length})`}>
        {matched.length ? (
          matched.map((m) => (
            <View key={m.id} style={s.item}>
              <View style={s.row}>
                <Chip label={`#${m.id}`} />
                <Chip label={TYPE_LABEL[m.type]} tone={m.type === "preference" ? "accent" : "neutral"} />
              </View>
              <Text style={s.itemText}>{m.content}</Text>
            </View>
          ))
        ) : (
          <Text style={s.muted}>Nothing I remember matched this message.</Text>
        )}
        {others ? (
          <Text style={s.muted}>
            I also had {others} other {others === 1 ? "memory" : "memories"} in mind.
          </Text>
        ) : null}
      </Group>
      <Group title={`From our earlier chats (${used.earlier.length})`}>
        {used.earlier.length ? (
          used.earlier.map((e) => (
            <View key={e.id} style={s.item}>
              <View style={s.row}>
                <Chip label={formatWhen(e.created_at)} />
              </View>
              <Text style={s.itemText} numberOfLines={4}>
                “{e.content}”
              </Text>
            </View>
          ))
        ) : (
          <Text style={s.muted}>Nothing you told me before matched.</Text>
        )}
      </Group>
      <Group title={`What I did (${used.tools.length})`}>
        {used.tools.length ? (
          used.tools.map((tool) => (
            <View key={tool.id} style={s.item}>
              <View style={s.row}>
                <Text style={s.itemText}>{toolLabel(tool.tool, tool.scope)}</Text>
                <Chip label={tool.status.replace("_", " ")} tone={statusTone(tool.status)} />
              </View>
              <Text style={s.muted}>
                {tool.function}
                {tool.latency_ms != null ? ` · ${tool.latency_ms} ms` : ""}
              </Text>
              {tool.error ? <Text style={s.muted}>{tool.error}</Text> : null}
            </View>
          ))
        ) : (
          <Text style={s.muted}>I didn't need any tools for this.</Text>
        )}
      </Group>
      {used.remembered.length ? (
        <Group title="I'll remember">
          {used.remembered.map((m) => (
            <Text key={m.id} style={s.itemText}>
              #{m.id} · {m.content}
            </Text>
          ))}
        </Group>
      ) : null}
      {used.checks ? <Checks checks={used.checks} /> : null}
    </>
  );
}

const CHECK_SUMMARY = {
  passed: "Everything I said matched what actually happened.",
  corrected: "My first draft claimed something that didn't happen, so I corrected it before sending.",
  flagged: "Part of my reply couldn't be backed up; the note at its end says what.",
} as const;

function Checks({ checks }: { checks: NonNullable<Used["checks"]> }) {
  const s = useStyles();
  return (
    <Group title="How I checked it">
      <View style={s.row}>
        <Chip
          label={checks.status === "passed" ? "passed" : checks.status === "corrected" ? "corrected" : "flagged"}
          tone={checks.status === "passed" ? "success" : checks.status === "corrected" ? "accent" : "warning"}
        />
      </View>
      <Text style={s.muted}>{CHECK_SUMMARY[checks.status]}</Text>
      {checks.items.map((item, i) => (
        <Text key={`${item.kind}-${i}`} style={s.itemText}>
          {item.fixed ? "Fixed: " : ""}
          {item.note}
        </Text>
      ))}
      {checks.memories_not_saved ? (
        <Text style={s.muted}>
          I didn't save {checks.memories_not_saved} {checks.memories_not_saved === 1 ? "memory" : "memories"}: not in your own words, a
          secret, or over a limit.
        </Text>
      ) : null}
      {checks.secrets_withheld ? (
        <Text style={s.muted}>
          I kept {checks.secrets_withheld} {checks.secrets_withheld === 1 ? "secret" : "secrets"} private: replaced with a placeholder before
          the message went to the model.
        </Text>
      ) : null}
    </Group>
  );
}

export function ContextRail({ used, wide }: { used: Used | null; wide: boolean }) {
  const s = useStyles();
  const t = useTokens();
  const [open, setOpen] = useState(false);

  if (wide) {
    return (
      <LinearGradient
        colors={t.gradients.card}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={s.panel}
        accessibilityLabel="Active context"
      >
        <ScrollView contentContainerStyle={s.panelInner}>
          <Text style={s.heading}>What shaped my reply</Text>
          <Details used={used} />
        </ScrollView>
      </LinearGradient>
    );
  }

  const matchedCount = used ? used.memories.filter((m) => m.matched !== false).length : 0;
  const summary = used
    ? `${matchedCount} ${matchedCount === 1 ? "memory" : "memories"} · ${used.earlier.length} earlier · ${used.tools.length} ${used.tools.length === 1 ? "tool" : "tools"}${used.skill ? ` · ${used.skill.name}` : ""}`
    : "nothing yet";
  return (
    <LinearGradient
      colors={t.gradients.card}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={s.strip}
    >
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        accessibilityLabel={`What shaped my reply: ${summary}`}
        onPress={() => setOpen((v) => !v)}
        style={s.stripHeader}
      >
        <Text style={s.heading}>What shaped my reply</Text>
        <Text style={s.summary} numberOfLines={1}>
          {summary}
        </Text>
        <Ionicons name={open ? "chevron-down" : "chevron-up"} size={16} color={t.inkSoft} />
      </Pressable>
      {open ? (
        <ScrollView style={s.stripBody} contentContainerStyle={s.stripInner}>
          <Details used={used} />
        </ScrollView>
      ) : null}
    </LinearGradient>
  );
}
