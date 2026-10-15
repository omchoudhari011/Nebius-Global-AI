// Active Context: what the last reply used - the skill version, the memories that matched
// (every other saved memory was in its context too), what you said in earlier chats, each
// tool call and its gate status, and anything saved to memory. On wide screens it's a
// panel beside the chat; on narrow ones a collapsible strip above the message box.

import Ionicons from "@expo/vector-icons/Ionicons";
import { useState, type ReactNode } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";

import { formatWhen, toolLabel } from "../lib/format";
import { space, themed, useTokens } from "../lib/tokens";
import type { Used } from "../lib/types";
import { Chip, statusTone } from "./ui";

const TYPE_LABEL = { semantic: "fact", preference: "preference", episodic: "event" } as const;

const useStyles = themed((t) => ({
  panel: { width: 280, backgroundColor: t.surface, borderLeftWidth: 1, borderLeftColor: t.border },
  panelInner: { padding: space.lg, gap: space.lg },
  strip: { backgroundColor: t.surface, borderTopWidth: 1, borderTopColor: t.border },
  stripHeader: { flexDirection: "row", alignItems: "center", gap: space.sm, paddingHorizontal: space.lg, minHeight: 40 },
  stripBody: { maxHeight: 240 },
  stripInner: { paddingHorizontal: space.lg, paddingBottom: space.md, gap: space.md },
  heading: { fontSize: 11, fontWeight: "800", letterSpacing: 0.8, color: t.inkSoft },
  summary: { flex: 1, color: t.inkSoft, fontSize: 13 },
  group: { gap: 6 },
  groupTitle: { fontSize: 12, fontWeight: "700", color: t.ink },
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
    return <Text style={s.muted}>Send a message to see which memories, tools and skill shaped the reply.</Text>;
  }
  const matched = used.memories.filter((m) => m.matched !== false);
  const others = used.memories.length - matched.length;
  return (
    <>
      <Group title="Skill">
        <Text style={s.itemText}>{used.skill ? `${used.skill.name} · v${used.skill.version}` : "None"}</Text>
      </Group>
      <Group title={`Memories matched (${matched.length})`}>
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
          <Text style={s.muted}>No memory matched this message.</Text>
        )}
        {others ? (
          <Text style={s.muted}>
            {others} other saved {others === 1 ? "memory was" : "memories were"} in context too.
          </Text>
        ) : null}
      </Group>
      <Group title={`From earlier chats (${used.earlier.length})`}>
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
          <Text style={s.muted}>Nothing you said before matched.</Text>
        )}
      </Group>
      <Group title={`Tool calls (${used.tools.length})`}>
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
          <Text style={s.muted}>No tools used.</Text>
        )}
      </Group>
      {used.remembered.length ? (
        <Group title="Saved to memory">
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
  passed: "Every claim in this reply matched what actually happened.",
  corrected: "The first draft claimed something that didn't happen. You're seeing the corrected reply.",
  flagged: "This reply says something that didn't happen; the note at its end explains what.",
} as const;

function Checks({ checks }: { checks: NonNullable<Used["checks"]> }) {
  const s = useStyles();
  return (
    <Group title="Accuracy check">
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
          {checks.memories_not_saved} {checks.memories_not_saved === 1 ? "memory" : "memories"} the model proposed weren't saved: not in your own
          words, a secret, or over a limit.
        </Text>
      ) : null}
      {checks.secrets_withheld ? (
        <Text style={s.muted}>
          {checks.secrets_withheld} {checks.secrets_withheld === 1 ? "secret was" : "secrets were"} replaced with a placeholder before the
          message went to the model.
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
      <View style={s.panel} accessibilityLabel="Active context">
        <ScrollView contentContainerStyle={s.panelInner}>
          <Text style={s.heading}>ACTIVE CONTEXT</Text>
          <Details used={used} />
        </ScrollView>
      </View>
    );
  }

  const matchedCount = used ? used.memories.filter((m) => m.matched !== false).length : 0;
  const summary = used
    ? `${matchedCount} ${matchedCount === 1 ? "memory" : "memories"} · ${used.earlier.length} earlier · ${used.tools.length} ${used.tools.length === 1 ? "tool" : "tools"}${used.skill ? ` · ${used.skill.name}` : ""}`
    : "nothing yet";
  return (
    <View style={s.strip}>
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        accessibilityLabel={`Active context: ${summary}`}
        onPress={() => setOpen((v) => !v)}
        style={s.stripHeader}
      >
        <Text style={s.heading}>ACTIVE CONTEXT</Text>
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
    </View>
  );
}
