// Built in Step 3 — see docs/PLAN.md.
// Switch's onValueChange calls setPermission(toolId, granted) from lib/api.ts,
// then re-fetches getPermissions() to reconcile UI state with the server.

import { View, Text, Switch, StyleSheet } from "react-native";
import { tokens } from "../lib/tokens";

export type Permission = { tool_id: string; tool_name: string; scope: string; granted: boolean };

export function PermissionRow({ permission }: { permission: Permission }) {
  return (
    <View style={styles.row}>
      <Text style={styles.name}>{permission.tool_name}</Text>
      <Switch value={permission.granted} onValueChange={() => {
        throw new Error("Step 3: wire this to setPermission() in lib/api.ts");
      }} />
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", padding: 10, backgroundColor: tokens.surface, borderRadius: 8, marginVertical: 4 },
  name: { color: tokens.ink },
});
