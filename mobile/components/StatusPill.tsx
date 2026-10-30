// How Custodian is doing, in plain words: here for you (and always on, when the scheduler
// runs), practising offline (the stub model), or still connecting. The model's name is in
// the accessibility label and in Account.

import { LinearGradient } from "expo-linear-gradient";
import { View } from "react-native";

import { useAuth } from "../lib/auth";
import { radius, softShadow, useTokens } from "../lib/tokens";
import { Text } from "./Typography";

export function StatusPill() {
  const t = useTokens();
  const { server } = useAuth();
  const text = !server
    ? "Waking up…"
    : server.model_backend === "stub"
      ? "Practice mode - offline stub"
      : `Online${server.scheduler ? " · always on" : ""}`;
  return (
    <LinearGradient
      colors={["rgba(0, 229, 188, 0.12)", "rgba(23, 132, 75, 0.16)"]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 0 }}
      style={{
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        paddingHorizontal: 8,
        paddingVertical: 3,
        borderRadius: radius.pill,
        overflow: "hidden",
        marginTop: 2,
        alignSelf: "flex-start",
        ...softShadow(t, 1),
      }}
    >
      <View
        style={{
          width: 7,
          height: 7,
          borderRadius: 3.5,
          backgroundColor: server ? t.turquoise : t.inkSoft,
          shadowColor: server ? t.turquoise : "transparent",
          shadowOpacity: 0.8,
          shadowRadius: 4,
          elevation: 2,
        }}
      />
      <Text
        style={{ color: t.inkSoft, fontSize: 12, fontWeight: "700", letterSpacing: 0.2 }}
        accessibilityLabel={`${text}. Model: ${server?.model ?? "unknown"}`}
      >
        {text}
      </Text>
    </LinearGradient>
  );
}

