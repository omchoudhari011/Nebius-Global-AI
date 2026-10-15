// Rating a reply. A thumbs down asks what it should have said; either way you choose whether
// this reply may be used to improve the model. Sharing stays on your server: it only lets the
// fine-tuning scripts there learn from it (backend/finetune/from_feedback.py).

import { useEffect, useState } from "react";
import { Modal, Pressable, Text, View } from "react-native";

import * as api from "../lib/api";
import { radius, space, themed } from "../lib/tokens";
import type { Feedback } from "../lib/types";
import { Button, Field, Notice, ToggleSwitch } from "./ui";

const useStyles = themed((t) => ({
  backdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.45)", justifyContent: "center", padding: space.lg },
  sheet: { backgroundColor: t.surface, borderRadius: radius.lg, padding: space.lg, gap: space.md, width: "100%", maxWidth: 520, alignSelf: "center" },
  title: { color: t.ink, fontSize: 18, fontWeight: "700" },
  text: { color: t.inkSoft, fontSize: 14, lineHeight: 20 },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: space.md },
  label: { flex: 1, color: t.ink, fontSize: 15 },
  buttons: { flexDirection: "row", justifyContent: "flex-end", gap: space.sm },
}));

export function FeedbackSheet({
  messageId,
  rating,
  current,
  onClose,
  onSaved,
}: {
  messageId: number | null;
  rating: 1 | -1;
  current: Feedback | null | undefined;
  onClose: () => void;
  onSaved: (feedback: Feedback | null) => void;
}) {
  const s = useStyles();
  const [correction, setCorrection] = useState("");
  const [share, setShare] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setCorrection(current?.rating === -1 ? current.correction ?? "" : "");
    setShare(current?.share ?? false);
    setError(null);
  }, [messageId, current]);

  async function save() {
    if (messageId == null) return;
    setBusy(true);
    try {
      onSaved(await api.rateMessage(messageId, rating, rating === -1 ? correction.trim() || null : null, share));
      onClose();
    } catch (err) {
      setError(api.errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (messageId == null) return;
    setBusy(true);
    try {
      await api.clearRating(messageId);
      onSaved(null);
      onClose();
    } catch (err) {
      setError(api.errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal visible={messageId != null} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={s.backdrop} onPress={onClose} accessibilityLabel="Close">
        <Pressable style={s.sheet} onPress={() => undefined}>
          <Text style={s.title}>{rating === 1 ? "Good reply" : "What went wrong?"}</Text>
          {rating === -1 ? (
            <Field
              label="What should it have said? (optional)"
              value={correction}
              onChangeText={setCorrection}
              multiline
              maxLength={4000}
              placeholder="The right answer, in your words"
            />
          ) : null}
          <View style={s.row}>
            <Text style={s.label}>Use this to improve the model</Text>
            <ToggleSwitch accessibilityLabel="Use this to improve the model" value={share} onValueChange={setShare} />
          </View>
          <Text style={s.text}>
            {share
              ? rating === 1
                ? "This chat turn becomes an example of a good answer when you next fine-tune the model on this server."
                : correction.trim()
                  ? "Your correction becomes the right answer for this turn when you next fine-tune the model on this server."
                  : "Write what it should have said, so it can be learned from."
              : "Nothing is used for training unless you turn this on. Either way it never leaves your server."}
          </Text>
          {error ? <Notice message={error} /> : null}
          <View style={s.buttons}>
            {current ? <Button compact variant="ghost" label="Remove rating" onPress={() => void remove()} disabled={busy} /> : null}
            <Button compact variant="secondary" label="Cancel" onPress={onClose} disabled={busy} />
            <Button compact label="Save" onPress={() => void save()} loading={busy} />
          </View>
        </Pressable>
      </Pressable>
    </Modal>
  );
}
