// Tools & Data screen. Placeholder for Step 1; built for real in Step 3 — see docs/PLAN.md.

import { View, Text, SafeAreaView, StyleSheet } from "react-native";
import { tokens } from "../lib/tokens";

export default function ToolsDataScreen() {
  return (
    <SafeAreaView style={styles.container}>
      <Text style={styles.text}>Tools &amp; Data — implemented in Step 3, see docs/PLAN.md</Text>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 12, backgroundColor: tokens.bg },
  text: { color: tokens.inkSoft },
});
