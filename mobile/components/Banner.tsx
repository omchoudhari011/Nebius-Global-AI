// Privacy notice at the top of Tools & Data, on the soft accent wash (full-contrast text).

import Ionicons from "@expo/vector-icons/Ionicons";
import { LinearGradient } from "expo-linear-gradient";
import { Text } from "./Typography";

import { radius, softShadow, space, useTokens } from "../lib/tokens";

export function Banner() {
  const t = useTokens();
  return (
    <LinearGradient
      colors={["rgba(0, 229, 188, 0.16)", "rgba(201, 231, 80, 0.08)"]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 0 }}
      style={{
        borderRadius: radius.md,
        paddingVertical: space.md,
        paddingHorizontal: space.lg,
        flexDirection: "row",
        gap: space.sm,
        alignItems: "center",
        overflow: "hidden",
        ...softShadow(t, 1),
      }}
    >
      <Ionicons name="shield-checkmark" size={18} color={t.accentText} />
      <Text style={{ color: t.ink, fontSize: 14, lineHeight: 20, flex: 1 }}>
        I only use what you switch on here, and you can switch anything off again at any time - I'll stop straight away.
      </Text>
    </LinearGradient>
  );
}

