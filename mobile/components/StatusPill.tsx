// Which model the server is running and whether its scheduler is on.

import { Text, View } from "react-native";

import { useAuth } from "../lib/auth";
import { useTokens } from "../lib/tokens";

export function StatusPill() {
  const t = useTokens();
  const { server } = useAuth();
  const model = !server ? "CONNECTING…" : server.model_backend === "stub" ? "OFFLINE STUB MODEL" : "NEMOTRON";
  const scheduler = server?.scheduler ? " · ALWAYS ON" : "";
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 2 }}>
      <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: server ? t.accent2 : t.inkSoft }} />
      <Text style={{ color: t.accent2, fontSize: 11, fontWeight: "700", letterSpacing: 0.5 }} accessibilityLabel={`Model: ${server?.model ?? "unknown"}`}>
        {model}
        {scheduler}
      </Text>
    </View>
  );
}
