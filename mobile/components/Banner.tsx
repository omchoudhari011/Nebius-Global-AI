// Privacy notice at the top of Tools & Data. The tint is a translucent background colour,
// not opacity on the container, so the text keeps full contrast.

import Ionicons from "@expo/vector-icons/Ionicons";
import { View } from "react-native";
import { Text } from "./Typography";

import { radius, space, useTokens } from "../lib/tokens";

export function Banner() {
  const t = useTokens();
  return (
    <View
      style={{
        backgroundColor: t.scheme === "dark" ? "rgba(79, 193, 181, 0.14)" : "rgba(31, 122, 114, 0.10)",
        borderRadius: radius.md,
        padding: space.md,
        flexDirection: "row",
        gap: space.sm,
        alignItems: "center",
      }}
    >
      <Ionicons name="shield-checkmark-outline" size={18} color={t.accent2} />
      <Text style={{ color: t.ink, fontSize: 14, flex: 1 }}>
        Nothing is shared until you switch it on. Revoke anytime — the next tool call is blocked.
      </Text>
    </View>
  );
}
