// Built in Step 3 — see docs/PLAN.md.

import { View, Text, StyleSheet } from "react-native";
import { tokens } from "../lib/tokens";

export function Banner() {
  return (
    <View style={styles.banner}>
      <Text style={styles.text}>Nothing is shared until you switch it on. Revoke anytime.</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: { backgroundColor: tokens.accent2, opacity: 0.12, padding: 10, borderRadius: 8, marginBottom: 10 },
  text: { color: tokens.ink, fontSize: 12 },
});
