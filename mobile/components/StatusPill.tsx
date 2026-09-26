import { View, Text, StyleSheet } from "react-native";
import { tokens } from "../lib/tokens";

export function StatusPill() {
  return (
    <View style={styles.pill}>
      <Text style={styles.text}>ALWAYS ON — LOCAL</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  pill: { flexDirection: "row", alignItems: "center", paddingVertical: 6 },
  text: { color: tokens.accent2, fontSize: 11, fontWeight: "600" },
});
