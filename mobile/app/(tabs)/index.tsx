// Chat screen. Picks up the latest conversation, sends messages, shows what each reply
// used, and keeps the Active Context beside the chat on wide screens (700 px and up) or
// as a collapsible strip above the message box on phones. The eye button starts an
// off-the-record chat: never remembered or recalled, deleted a day after the last message.
// The composer can attach a file (PDF, text, photo), record a voice message, and - when the
// server has a larger model - ask it to think harder. The bell opens the inbox.

import Ionicons from "@expo/vector-icons/Ionicons";
import { useRouter } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  Text,
  TextInput,
  View,
  useWindowDimensions,
  type NativeSyntheticEvent,
  type TextInputKeyPressEventData,
} from "react-native";

import { ChatThread, type ChatItem } from "../../components/ChatThread";
import { ContextRail } from "../../components/ContextRail";
import { InboxButton } from "../../components/Inbox";
import { StatusPill } from "../../components/StatusPill";
import { Button, IconButton, Notice, ScreenHeader } from "../../components/ui";
import { VoiceButton } from "../../components/VoiceButton";
import * as api from "../../lib/api";
import { useInbox } from "../../lib/inbox";
import { pickFile } from "../../lib/media";
import { radius, space, themed, useTokens } from "../../lib/tokens";
import type { DocumentFile, Feedback, Used } from "../../lib/types";

const MAX_LENGTH = 8000;

const useStyles = themed((t) => ({
  screen: { flex: 1, backgroundColor: t.bg },
  body: { flex: 1 },
  composer: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: space.sm,
    paddingHorizontal: space.lg,
    paddingVertical: space.md,
    backgroundColor: t.bg,
  },
  input: {
    flex: 1,
    minHeight: 44,
    maxHeight: 140,
    borderWidth: 1,
    borderColor: t.border,
    borderRadius: radius.lg,
    paddingHorizontal: space.md,
    paddingTop: 11,
    paddingBottom: 11,
    backgroundColor: t.surface,
    color: t.ink,
    fontSize: 16,
    outlineColor: t.accent,
  },
  send: { width: 44, height: 44, borderRadius: 22, alignItems: "center", justifyContent: "center", backgroundColor: t.accent },
  sendDisabled: { opacity: 0.4 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  notice: { paddingHorizontal: space.lg, paddingTop: space.sm },
  tool: { width: 40, height: 44, alignItems: "center", justifyContent: "center" },
  deep: { flexDirection: "row", alignItems: "center", gap: 4, alignSelf: "flex-start", marginLeft: space.lg,
    paddingHorizontal: 10, paddingVertical: 4, borderRadius: radius.pill, borderWidth: 1, borderColor: t.border },
  deepOn: { borderColor: t.accent, backgroundColor: t.surface2 },
  deepText: { color: t.inkSoft, fontSize: 12, fontWeight: "600" },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: space.sm, paddingHorizontal: space.lg, paddingTop: space.sm },
  chip: { flexDirection: "row", alignItems: "center", gap: 6, maxWidth: 260, paddingHorizontal: 10, paddingVertical: 5,
    borderRadius: radius.pill, borderWidth: 1, borderColor: t.accent, backgroundColor: t.surface2 },
  chipText: { color: t.ink, fontSize: 13, flexShrink: 1 },
}));

export default function ChatScreen() {
  const s = useStyles();
  const t = useTokens();
  const { width } = useWindowDimensions();
  const wide = width >= 700;

  const [items, setItems] = useState<ChatItem[]>([]);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [offTheRecord, setOffTheRecord] = useState(false);
  const [lastUsed, setLastUsed] = useState<Used | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const sendingRef = useRef(false);
  const [deep, setDeep] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [attached, setAttached] = useState<DocumentFile[]>([]);
  const [composerNotice, setComposerNotice] = useState<{ tone: "danger" | "success" | "warning"; text: string } | null>(null);
  const { features, refresh: refreshInbox } = useInbox();
  const router = useRouter();

  const loadLatest = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [latest] = await api.listConversations("chat", 1);
      if (latest) {
        const conversation = await api.getConversation(latest.id);
        const mapped: ChatItem[] = conversation.messages
          .filter((m) => m.role !== "system")
          .map((m) => ({
            key: `m${m.id}`,
            role: m.role === "user" ? "user" : "assistant",
            content: m.content,
            used: m.used,
            messageId: m.role === "assistant" ? m.id : undefined,
            feedback: m.feedback ?? null,
          }));
        setItems(mapped);
        setConversationId(conversation.id);
        setOffTheRecord(conversation.private);
        setLastUsed([...mapped].reverse().find((m) => m.used)?.used ?? null);
      }
    } catch (err) {
      setLoadError(api.errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadLatest();
  }, [loadLatest]);

  const send = useCallback(
    async (raw: string, retryKey?: string, files: DocumentFile[] = []) => {
      const text = raw.trim() || (files.length ? "Please read the attached file and tell me what it's about." : "");
      if (!text || sendingRef.current) return;
      sendingRef.current = true;
      setSending(true);
      const key = retryKey ?? `local-${Date.now()}`;
      setItems((prev) =>
        retryKey
          ? prev.map((item) => (item.key === key ? { ...item, failed: false, error: undefined } : item))
          : [...prev, { key, role: "user", content: text + files.map((d) => `
📎 ${d.name}`).join("") }],
      );
      try {
        const res = await api.sendMessage(text, conversationId, offTheRecord, deep, files.map((d) => d.id));
        setConversationId(res.conversation_id);
        setOffTheRecord(res.private);
        setItems((prev) => [
          ...prev,
          { key: `m${res.message_id}`, role: "assistant", content: res.reply, used: res.used, messageId: res.message_id, feedback: null },
        ]);
        setLastUsed(res.used);
        setDeep(false);
        if (res.used.tools.some((tool) => tool.tool === "reminders")) void refreshInbox(); // schedule it on this phone now
      } catch (err) {
        let message = api.errorMessage(err);
        if (err instanceof api.ApiError && err.status === 404) {
          setConversationId(null); // the thread was deleted elsewhere; retry starts a new one
          message = "That conversation no longer exists. Retry to start a new one.";
        }
        setItems((prev) => prev.map((item) => (item.key === key ? { ...item, failed: true, error: message } : item)));
      } finally {
        sendingRef.current = false;
        setSending(false);
      }
    },
    [conversationId, offTheRecord, deep, refreshInbox],
  );

  const setFeedback = useCallback((messageId: number, feedback: Feedback | null) => {
    setItems((prev) => prev.map((item) => (item.messageId === messageId ? { ...item, feedback } : item)));
  }, []);

  async function attach() {
    setComposerNotice(null);
    let picked;
    try {
      picked = await pickFile();
    } catch (err) {
      setComposerNotice({ tone: "danger", text: api.errorMessage(err) });
      return;
    }
    if (!picked) return;
    setUploading(true);
    try {
      const doc = await api.uploadDocument(picked.name, picked.mimeType, picked.base64);
      setAttached((prev) => [...prev.filter((d) => d.id !== doc.id), doc].slice(-3));
      if (doc.warning) setComposerNotice({ tone: "warning", text: `“${doc.name}”: ${doc.warning}` });
    } catch (err) {
      setComposerNotice({ tone: "danger", text: api.errorMessage(err) });
    } finally {
      setUploading(false);
    }
  }

  function submit() {
    if (sending || (!input.trim() && !attached.length)) return;
    const text = input;
    const files = attached;
    setInput("");
    setAttached([]);
    void send(text, undefined, files);
  }

  function onKeyPress(e: NativeSyntheticEvent<TextInputKeyPressEventData>) {
    // Web: Enter sends, Shift+Enter adds a line. Phones use the send button.
    const event = e.nativeEvent as TextInputKeyPressEventData & { shiftKey?: boolean };
    if (Platform.OS === "web" && event.key === "Enter" && !event.shiftKey) {
      (e as unknown as { preventDefault?: () => void }).preventDefault?.();
      submit();
    }
  }

  function newChat(privately = false) {
    setItems([]);
    setConversationId(null);
    setOffTheRecord(privately);
    setLastUsed(null);
  }

  const canSend = (!!input.trim() || attached.length > 0) && !sending;

  return (
    <View style={s.screen}>
      <ScreenHeader
        title="Custodian"
        subtitle={<StatusPill />}
        right={
          <>
            <InboxButton onOpenConversation={() => router.navigate("/scheduled")} />
            <IconButton
              icon={offTheRecord ? "eye-off" : "eye-off-outline"}
              label="Start an off-the-record chat"
              color={offTheRecord ? t.accent2 : undefined}
              onPress={() => !sending && newChat(true)}
            />
            <Button compact variant="secondary" icon="create-outline" label="New chat" onPress={() => newChat(false)} disabled={sending} />
          </>
        }
      />
      <KeyboardAvoidingView style={s.body} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <View style={{ flex: 1, flexDirection: wide ? "row" : "column" }}>
          <View style={{ flex: 1 }}>
            {offTheRecord ? (
              <View style={s.notice}>
                <Notice
                  tone="accent"
                  message="Off the record: nothing here is saved to memory or recalled in other chats, and this chat is deleted 24 hours after your last message."
                />
              </View>
            ) : null}
            {loadError ? (
              <View style={s.notice}>
                <Notice message={loadError} actionLabel="Retry" onAction={loadLatest} />
              </View>
            ) : null}
            {loading ? (
              <View style={s.center}>
                <ActivityIndicator color={t.accent2} />
              </View>
            ) : (
              <ChatThread
                items={items}
                pending={sending}
                onRetry={(item) => void send(item.content, item.key)}
                onSuggestion={(text) => void send(text)}
                onFeedback={setFeedback}
              />
            )}
            {!wide ? <ContextRail used={lastUsed} wide={false} /> : null}
            {composerNotice ? (
              <View style={s.notice}>
                <Notice tone={composerNotice.tone} message={composerNotice.text} actionLabel="Dismiss" onAction={() => setComposerNotice(null)} />
              </View>
            ) : null}
            {features?.larger_model ? (
              <Pressable
                accessibilityRole="switch"
                accessibilityState={{ checked: deep }}
                accessibilityLabel="Think harder: the next message goes to the larger model"
                onPress={() => setDeep((v) => !v)}
                style={[s.deep, deep && s.deepOn]}
              >
                <Ionicons name={deep ? "bulb" : "bulb-outline"} size={14} color={deep ? t.accentText : t.inkSoft} />
                <Text style={[s.deepText, deep && { color: t.accentText }]}>Think harder</Text>
              </Pressable>
            ) : null}
            {attached.length ? (
              <View style={s.chips}>
                {attached.map((doc) => (
                  <View key={doc.id} style={s.chip}>
                    <Ionicons name="document-attach-outline" size={14} color={t.accentText} />
                    <Text style={s.chipText} numberOfLines={1}>{doc.name}</Text>
                    <Pressable accessibilityRole="button" accessibilityLabel={`Remove ${doc.name}`}
                      onPress={() => setAttached((prev) => prev.filter((d) => d.id !== doc.id))}>
                      <Ionicons name="close" size={14} color={t.inkSoft} />
                    </Pressable>
                  </View>
                ))}
              </View>
            ) : null}
            <View style={s.composer}>
              <Pressable accessibilityRole="button" accessibilityLabel="Attach a file" style={s.tool} disabled={uploading || sending} onPress={() => void attach()}>
                {uploading ? <ActivityIndicator size="small" color={t.accent2} /> : <Ionicons name="attach" size={24} color={t.inkSoft} />}
              </Pressable>
              <TextInput
                style={s.input}
                value={input}
                onChangeText={setInput}
                placeholder="Message Custodian…"
                placeholderTextColor={t.inkSoft}
                multiline
                maxLength={MAX_LENGTH}
                onKeyPress={onKeyPress}
                accessibilityLabel="Message"
              />
              {features?.voice ? (
                <VoiceButton
                  disabled={sending}
                  onText={(text) => setInput((prev) => (prev.trim() ? `${prev.trim()} ${text}` : text))}
                  onError={(message) => setComposerNotice({ tone: "danger", text: message })}
                />
              ) : null}
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Send"
                accessibilityState={{ disabled: !canSend }}
                disabled={!canSend}
                onPress={submit}
                style={[s.send, !canSend && s.sendDisabled]}
              >
                {sending ? <ActivityIndicator size="small" color={t.onAccent} /> : <Ionicons name="arrow-up" size={22} color={t.onAccent} />}
              </Pressable>
            </View>
          </View>
          {wide ? <ContextRail used={lastUsed} wide /> : null}
        </View>
      </KeyboardAvoidingView>
    </View>
  );
}
