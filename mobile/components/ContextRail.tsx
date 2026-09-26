// Built in Step 5 — see docs/PLAN.md.
// useWindowDimensions() repositions this: below the chat on a narrow screen,
// beside it on a wide one — one component, not two.

import { View, Text, StyleSheet, useWindowDimensions } from "react-native";
import { tokens } from "../lib/tokens";

export function ContextRail({ used }: { used?: Record<string, unknown> }) {
  const { width } = useWindowDimensions();
  const narrow = width < 700;

  return (
    <View style={[styles.rail, narrow ? styles.below : styles.beside]}>
      <Text style={styles.label}>ACTIVE CONTEXT</Text>
      <Text style={styles.placeholder}>Wire this up to the last response's `used` field in Step 5.</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  rail: { padding: 10, backgroundColor: tokens.surface, borderRadius: 8 },
  below: { marginTop: 10 },
  beside: { marginLeft: 10, width: 180 },
  label: { fontSize: 10, fontWeight: "700", color: tokens.inkSoft, marginBottom: 6 },
  placeholder: { fontSize: 11, color: tokens.inkSoft },
});
