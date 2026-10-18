// How Custodian is doing, in plain words: here for you (and always on, when the scheduler
// runs), practising offline (the stub model), or still connecting. The model's name is in
// the accessibility label and in Account.

import { View } from "react-native";

import { useAuth } from "../lib/auth";
import { useTokens } from "../lib/tokens";
import { Text } from "./Typography";

export function StatusPill() {
  const t = useTokens();
  const { server } = useAuth();
  const text = !server
    ? "Waking up…"
    : server.model_backend === "stub"
      ? "Practice mode - the offline stub model"
      : `Here for you${server.scheduler ? " · always on" : ""}`;
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 1 }}>
      <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: server ? t.success : t.inkSoft }} />
      <Text style={{ color: t.inkSoft, fontSize: 13, fontWeight: "600" }} accessibilityLabel={`${text}. Model: ${server?.model ?? "unknown"}`}>
        {text}
      </Text>
    </View>
  );
}
