// One switch: a tool scope (e.g. Calendar · read) and whether Custodian may use it.

import { Text, View } from "react-native";

import { space, useTokens } from "../lib/tokens";
import type { Permission } from "../lib/types";
import { ToggleSwitch } from "./ui";

export type { Permission };

const SCOPE_TEXT: Record<string, string> = {
  read: "Read",
  write: "Add & change",
};

export function PermissionRow({
  permission,
  busy,
  onToggle,
}: {
  permission: Permission;
  busy?: boolean;
  onToggle: (granted: boolean) => void;
}) {
  const t = useTokens();
  const label = `${SCOPE_TEXT[permission.scope] ?? permission.scope}`;
  return (
    <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: space.md, paddingVertical: space.xs }}>
      <View style={{ flex: 1 }}>
        <Text style={{ color: t.ink, fontSize: 15, fontWeight: "600" }}>{label}</Text>
        <Text style={{ color: t.inkSoft, fontSize: 13 }}>
          {permission.scope}
          {permission.description ? ` · ${permission.description}` : ""}
          {!permission.enabled ? " · switched off on the server" : ""}
        </Text>
      </View>
      <ToggleSwitch
        accessibilityLabel={`${permission.tool_name} ${permission.scope}`}
        value={permission.granted}
        disabled={busy || !permission.enabled}
        onValueChange={onToggle}
      />
    </View>
  );
}
