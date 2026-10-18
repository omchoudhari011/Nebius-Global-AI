// A PC action you asked for from this app (or Telegram) that needs your OK: closing an app,
// deleting a file, sending a WhatsApp message. It waits on the server for up to two minutes;
// "Allow once" sends it to the PC without a second dialog there, "Deny" means it never reaches
// the PC. Polls quickly while a reply is on its way (`active`), slowly otherwise.

import { useEffect, useState } from "react";
import { View, type StyleProp, type ViewStyle } from "react-native";

import * as api from "../lib/api";
import { space, useTokens } from "../lib/tokens";
import type { Approval } from "../lib/types";
import { Text } from "./Typography";
import { Button, Card } from "./ui";

export function ApprovalPrompt({ active, style }: { active: boolean; style?: StyleProp<ViewStyle> }) {
  const t = useTokens();
  const [waiting, setWaiting] = useState<Approval[]>([]);
  const [busy, setBusy] = useState<number | null>(null);

  useEffect(() => {
    let stopped = false;
    const check = async () => {
      try {
        const found = await api.getApprovals();
        if (!stopped) setWaiting(found);
      } catch {
        // offline for a moment: the next check tries again
      }
    };
    void check();
    const timer = setInterval(() => void check(), active ? 1500 : 15_000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [active]);

  async function decide(approval: Approval, allow: boolean) {
    setBusy(approval.id);
    try {
      await api.decideApproval(approval.id, allow);
    } catch {
      // it timed out or was answered on Telegram: drop it either way
    } finally {
      setWaiting((prev) => prev.filter((a) => a.id !== approval.id));
      setBusy(null);
    }
  }

  if (!waiting.length) return null;
  return (
    <View style={[{ gap: space.sm }, style]}>
      {waiting.map((approval) => (
        <Card key={approval.id}>
          <Text style={{ color: t.ink, fontSize: 16, fontWeight: "700" }}>Allow this on your PC?</Text>
          <Text style={{ color: t.ink, fontSize: 15, fontWeight: "600" }}>{approval.title}</Text>
          <Text style={{ color: t.inkSoft, fontSize: 14, lineHeight: 20 }}>{approval.details}</Text>
          <View style={{ flexDirection: "row", gap: space.sm, flexWrap: "wrap" }}>
            <Button compact icon="checkmark-circle-outline" label="Allow once" loading={busy === approval.id}
              onPress={() => void decide(approval, true)} />
            <Button compact variant="danger" icon="close-circle-outline" label="Deny" disabled={busy === approval.id}
              onPress={() => void decide(approval, false)} />
          </View>
        </Card>
      ))}
    </View>
  );
}
