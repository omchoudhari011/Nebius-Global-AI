// Chat screen. Step 1 — see docs/PLAN.md.

import { useState } from "react";
import { View, TextInput, Button, StyleSheet, SafeAreaView } from "react-native";
import { ChatThread, Message } from "../components/ChatThread";
import { StatusPill } from "../components/StatusPill";
import { sendMessage } from "../lib/api";
import { tokens } from "../lib/tokens";

export default function ChatScreen() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");

  async function onSend() {
    if (!input.trim()) return;
    const text = input;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }]);
    const res = await sendMessage(text);
    setMessages((m) => [...m, { role: "assistant", content: res.reply, used: res.used }]);
  }

  return (
    <SafeAreaView style={styles.container}>
      <StatusPill />
      <ChatThread messages={messages} />
      <View style={styles.inputRow}>
        <TextInput
          style={styles.input}
          value={input}
          onChangeText={setInput}
          placeholder="Message Custodian…"
          onSubmitEditing={onSend}
        />
        <Button title="Send" onPress={onSend} />
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 12, backgroundColor: tokens.bg },
  inputRow: { flexDirection: "row", gap: 8, alignItems: "center" },
  input: { flex: 1, borderWidth: 1, borderColor: tokens.border, borderRadius: 8, padding: 8, backgroundColor: tokens.surface },
});
