// Privacy notice at the top of Tools & Data, on the soft accent wash (full-contrast text).

import Ionicons from "@expo/vector-icons/Ionicons";
import { View } from "react-native";
import { Text } from "./Typography";

import { radius, space, useTokens } from "../lib/tokens";

export function Banner() {
  const t = useTokens();
  return (
    <View
      style={{
        backgroundColor: t.accentSoft,
        borderRadius: radius.md,
        paddingVertical: space.md,
        paddingHorizontal: space.lg,
        flexDirection: "row",
        gap: space.sm,
        alignItems: "center",
      }}
    >
      <Ionicons name="shield-checkmark" size={18} color={t.accentText} />
      <Text style={{ color: t.ink, fontSize: 14, lineHeight: 20, flex: 1 }}>
        I only use what you switch on here, and you can switch anything off again at any time - I'll stop straight away.
      </Text>
    </View>
  );
}
