import { FlatList, Text, View, StyleSheet } from "react-native";
import { tokens } from "../lib/tokens";

export type Message = { role: "user" | "assistant"; content: string; used?: Record<string, unknown> };

export function ChatThread({ messages }: { messages: Message[] }) {
  return (
    <FlatList
      data={messages}
      keyExtractor={(_, i) => String(i)}
      renderItem={({ item }) => (
        <View style={[styles.bubble, item.role === "user" && styles.user]}>
          <Text style={styles.text}>{item.content}</Text>
          {/* Provenance tag line ("used: Memory #12") added in Step 2 — see docs/PLAN.md */}
        </View>
      )}
    />
  );
}

const styles = StyleSheet.create({
  bubble: { padding: 10, borderRadius: 8, backgroundColor: tokens.surface, marginVertical: 4, maxWidth: "82%" },
  user: { alignSelf: "flex-end", backgroundColor: tokens.accent },
  text: { color: tokens.ink },
});
