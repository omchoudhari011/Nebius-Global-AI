// Tools & Data: your permission switches, the memories Custodian keeps, and the audit log of
// every tool call (including blocked ones). Privacy controls and your plan are in Account.

import { useFocusEffect } from "expo-router";
import { useCallback, useMemo, useState } from "react";
import { ActivityIndicator, Linking, Platform, RefreshControl, ScrollView, Text, View } from "react-native";

import { Banner } from "../../components/Banner";
import { PermissionRow } from "../../components/PermissionRow";
import { Button, Card, Chip, Field, IconButton, Notice, ScreenHeader, SectionTitle, Segmented, statusTone } from "../../components/ui";
import * as api from "../../lib/api";
import { confirmAction, formatWhen, toolLabel } from "../../lib/format";
import { space, themed, useTokens } from "../../lib/tokens";
import { pickFile } from "../../lib/media";
import type { DocumentFile, Integration, Memory, MemoryType, Permission, PermissionEvent, Reminder, ToolCallLog } from "../../lib/types";

const MEMORY_TYPES: { value: MemoryType; label: string }[] = [
  { value: "semantic", label: "Fact" },
  { value: "preference", label: "Preference" },
  { value: "episodic", label: "Event" },
];
const TYPE_LABEL: Record<MemoryType, string> = { semantic: "fact", preference: "preference", episodic: "event" };

const useStyles = themed((t) => ({
  screen: { flex: 1, backgroundColor: t.bg },
  content: { padding: space.lg, paddingTop: 0, gap: space.lg, width: "100%", maxWidth: 760, alignSelf: "center" },
  toolName: { fontSize: 17, fontWeight: "700", color: t.ink },
  divider: { height: 1, backgroundColor: t.border },
  rowBetween: { flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between", gap: space.md },
  body: { color: t.ink, fontSize: 15, lineHeight: 21 },
  meta: { color: t.inkSoft, fontSize: 13 },
  itemText: { color: t.ink, fontSize: 15, fontWeight: "600" },
  fileRow: { flexDirection: "row", alignItems: "center", gap: space.md, paddingVertical: 4 },
  chips: { flexDirection: "row", gap: 6, flexWrap: "wrap", alignItems: "center" },
  empty: { color: t.inkSoft, fontSize: 14 },
}));

export default function ToolsDataScreen() {
  const s = useStyles();
  const t = useTokens();

  const [permissions, setPermissions] = useState<Permission[]>([]);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [activity, setActivity] = useState<ToolCallLog[]>([]);
  const [events, setEvents] = useState<PermissionEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busySwitch, setBusySwitch] = useState<string | null>(null);
  const [newMemory, setNewMemory] = useState("");
  const [newType, setNewType] = useState<MemoryType>("semantic");
  const [adding, setAdding] = useState(false);
  const [google, setGoogle] = useState<Integration | null>(null);
  const [files, setFiles] = useState<DocumentFile[]>([]);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [pc, setPc] = useState<{ connected: boolean; device: string | null } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, m, a, e, g, d, r, desk] = await Promise.all([
        api.getPermissions(),
        api.getMemories(),
        api.getToolCalls(25),
        api.getPermissionEvents(10),
        api.getIntegrations(),
        api.getDocuments(),
        api.getReminders(),
        api.getDesktopStatus().catch(() => null),
      ]);
      setPc(desk);
      setGoogle(g.find((i) => i.provider === "google") ?? null);
      setFiles(d);
      setReminders(r);
      setPermissions(p);
      setMemories(m);
      setActivity(a);
      setEvents(e);
      setError(null);
    } catch (err) {
      setError(api.errorMessage(err));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      void load();
    }, [load]),
  );

  async function connectGoogle() {
    setBusy("google");
    try {
      const { url } = await api.startGoogle();
      if (Platform.OS === "web" && typeof window !== "undefined") window.location.assign(url);
      else await Linking.openURL(url);
    } catch (err) {
      setError(api.errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function disconnectGoogle() {
    if (!(await confirmAction("Disconnect Google?", "Custodian forgets its access to your Google account and asks Google to revoke it.", "Disconnect"))) return;
    setBusy("google");
    try {
      await api.disconnectGoogle();
      await load();
    } catch (err) {
      setError(api.errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function addFile() {
    setBusy("upload");
    try {
      const picked = await pickFile();
      if (picked) {
        const doc = await api.uploadDocument(picked.name, picked.mimeType, picked.base64);
        setInfo(doc.warning ? `Saved “${doc.name}”. ${doc.warning}` : `Saved “${doc.name}” (${doc.characters.toLocaleString()} characters of text).`);
        await load();
      }
    } catch (err) {
      setError(api.errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function removeFile(file: DocumentFile) {
    if (!(await confirmAction(`Delete “${file.name}”?`, "Custodian won't be able to read it any more."))) return;
    try {
      await api.deleteDocument(file.id);
      setFiles((prev) => prev.filter((f) => f.id !== file.id));
    } catch (err) {
      setError(api.errorMessage(err));
    }
  }

  async function cancelReminder(reminder: Reminder) {
    if (!(await confirmAction("Cancel this reminder?", reminder.text, "Cancel reminder"))) return;
    try {
      await api.cancelReminder(reminder.id);
      setReminders((prev) => prev.filter((r) => r.id !== reminder.id));
    } catch (err) {
      setError(api.errorMessage(err));
    }
  }

  const byTool = useMemo(() => {
    const groups = new Map<string, Permission[]>();
    for (const p of permissions) groups.set(p.tool_name, [...(groups.get(p.tool_name) ?? []), p]);
    return [...groups.entries()];
  }, [permissions]);

  async function toggle(permission: Permission, granted: boolean) {
    const key = `${permission.tool_id}:${permission.scope}`;
    const same = (p: Permission) => p.tool_id === permission.tool_id && p.scope === permission.scope;
    setBusySwitch(key);
    setPermissions((prev) => prev.map((p) => (same(p) ? { ...p, granted } : p)));
    try {
      const saved = await api.setPermission(permission.tool_id, permission.scope, granted);
      setPermissions((prev) => prev.map((p) => (same(p) ? saved : p)));
      setEvents(await api.getPermissionEvents(10));
      setError(null);
    } catch (err) {
      setPermissions((prev) => prev.map((p) => (same(p) ? { ...p, granted: !granted } : p)));
      setError(api.errorMessage(err));
    } finally {
      setBusySwitch(null);
    }
  }

  async function addMemory() {
    const text = newMemory.trim();
    if (!text || adding) return;
    setAdding(true);
    try {
      const created = await api.addMemory(text, newType);
      setMemories((prev) => [created, ...prev.filter((m) => m.id !== created.id)]);
      setNewMemory("");
      setError(null);
    } catch (err) {
      setError(api.errorMessage(err));
    } finally {
      setAdding(false);
    }
  }

  async function removeMemory(memory: Memory) {
    if (!(await confirmAction("Forget this memory?", memory.content, "Forget"))) return;
    try {
      await api.deleteMemory(memory.id);
      setMemories((prev) => prev.filter((m) => m.id !== memory.id));
    } catch (err) {
      setError(api.errorMessage(err));
    }
  }

  return (
    <View style={s.screen}>
      <ScreenHeader title="Tools & Data" subtitle="What Custodian may use, and what it knows" />
      <ScrollView
        contentContainerStyle={s.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }} tintColor={t.accent2} />}
        keyboardShouldPersistTaps="handled"
      >
        <Banner />
        {error ? <Notice message={error} actionLabel="Retry" onAction={load} /> : null}
        {loading ? <ActivityIndicator color={t.accent2} /> : null}

        {info ? <Notice tone="success" message={info} actionLabel="Dismiss" onAction={() => setInfo(null)} /> : null}

        <SectionTitle title="Google account" hint="Google Calendar, Gmail (read only) and Google Drive (read only). Each has its own switch below, all off until you turn them on." />
        <Card>
          {google?.connected ? (
            <>
              <Text style={s.toolName}>Connected{google.account ? ` as ${google.account}` : ""}</Text>
              <Text style={s.meta}>Custodian keeps the access encrypted on your server; tools get only a short-lived pass to Google's servers.</Text>
              <Button compact variant="danger" icon="unlink-outline" label="Disconnect Google" loading={busy === "google"} onPress={() => void disconnectGoogle()} />
            </>
          ) : google?.configured ? (
            <>
              <Text style={s.meta}>Sign in with Google to let Custodian see your calendar, email and Drive files when you allow it.</Text>
              <Button compact icon="logo-google" label="Connect Google" loading={busy === "google"} onPress={() => void connectGoogle()} />
            </>
          ) : (
            <Text style={s.meta}>Not set up on this server: it needs a Google OAuth client (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET; see the README).</Text>
          )}
        </Card>

        <SectionTitle title="Your PC" hint="The Custodian desktop app lets Custodian open apps, control media and volume, and work with files on your Windows PC - only with PC · read and PC · write on, and anything risky asks you on the PC first." />
        <Card>
          {pc?.connected ? (
            <Text style={s.toolName}>Connected: {pc.device ?? "your PC"}</Text>
          ) : (
            <Text style={s.meta}>Not connected. On your PC, run desktop\run-desktop.ps1 and sign in; say “Hey Jarvis” or press Ctrl+Alt+Space to talk.</Text>
          )}
        </Card>

        <SectionTitle title={`Files (${files.length})`} hint="PDFs, text files and photos you gave Custodian. It reads them only when Files · read is on." />
        <Card>
          {files.map((file) => (
            <View key={file.id} style={s.fileRow}>
              <View style={{ flex: 1, gap: 2 }}>
                <Text style={s.itemText}>{file.name}</Text>
                <Text style={s.meta}>{file.kind.toUpperCase()} · {Math.max(1, Math.round(file.bytes / 1024))} KB · {formatWhen(file.created_at)}</Text>
              </View>
              <IconButton icon="trash-outline" label={`Delete ${file.name}`} onPress={() => void removeFile(file)} />
            </View>
          ))}
          {!files.length ? <Text style={s.meta}>No files yet. Attach one in the chat or here.</Text> : null}
          <Button compact variant="secondary" icon="cloud-upload-outline" label="Add a file" loading={busy === "upload"} onPress={() => void addFile()} />
        </Card>

        <SectionTitle title={`Upcoming reminders (${reminders.length})`} hint="Set them in the chat (“remind me to … at …”). They show in the inbox, and on this phone, when due." />
        {reminders.length ? (
          <Card>
            {reminders.map((r) => (
              <View key={r.id} style={s.fileRow}>
                <View style={{ flex: 1, gap: 2 }}>
                  <Text style={s.itemText}>{r.text}</Text>
                  <Text style={s.meta}>{formatWhen(r.due)}{r.repeat ? ` · repeats ${r.repeat}` : ""}</Text>
                </View>
                <IconButton icon="close-circle-outline" label={`Cancel reminder: ${r.text}`} onPress={() => void cancelReminder(r)} />
              </View>
            ))}
          </Card>
        ) : null}

        {byTool.length ? <SectionTitle title="Tool permissions" hint="Each switch covers one kind of access. Everything starts off." /> : null}
        {byTool.map(([tool, rows]) => (
          <Card key={tool}>
            <Text style={s.toolName}>{toolLabel(tool)}</Text>
            {rows.map((p, i) => (
              <View key={p.scope} style={{ gap: space.md }}>
                {i > 0 ? <View style={s.divider} /> : null}
                <PermissionRow
                  permission={p}
                  busy={busySwitch === `${p.tool_id}:${p.scope}`}
                  onToggle={(granted) => void toggle(p, granted)}
                />
              </View>
            ))}
          </Card>
        ))}

        <SectionTitle
          title={`Memories (${memories.length})`}
          hint="Stored encrypted. Every chat gets all of them, the ones that match first. Passwords, PINs, card and bank account numbers and ID numbers (Aadhaar, PAN, passport) are never saved."
        />
        <Card>
          <Field
            label="Add a memory"
            value={newMemory}
            onChangeText={setNewMemory}
            placeholder="e.g. Prefers meetings before noon"
            maxLength={2000}
            onSubmitEditing={addMemory}
            returnKeyType="done"
          />
          <Segmented options={MEMORY_TYPES} value={newType} onChange={setNewType} />
          <Button label="Save memory" icon="add" onPress={addMemory} loading={adding} disabled={!newMemory.trim()} />
        </Card>
        {memories.length ? (
          <Card>
            {memories.map((m, i) => (
              <View key={m.id} style={{ gap: space.md }}>
                {i > 0 ? <View style={s.divider} /> : null}
                <View style={s.rowBetween}>
                  <View style={{ flex: 1, gap: 6 }}>
                    <View style={s.chips}>
                      <Chip label={TYPE_LABEL[m.type]} tone={m.type === "preference" ? "accent" : "neutral"} />
                      <Text style={s.meta}>
                        #{m.id} · {formatWhen(m.created_at)}
                        {m.expires_at ? ` · forgets ${formatWhen(m.expires_at)}` : ""}
                      </Text>
                    </View>
                    <Text style={s.body}>{m.content}</Text>
                  </View>
                  <IconButton icon="trash-outline" label={`Forget memory ${m.id}`} onPress={() => void removeMemory(m)} color={t.danger} />
                </View>
              </View>
            ))}
          </Card>
        ) : !loading ? (
          <Text style={s.empty}>No memories yet. Tell Custodian “Remember that …” or add one above.</Text>
        ) : null}

        <SectionTitle title="Recent activity" hint="Every tool call Custodian proposed, including the ones that were blocked." />
        <Card>
          {activity.length ? (
            activity.map((call, i) => (
              <View key={call.id} style={{ gap: space.md }}>
                {i > 0 ? <View style={s.divider} /> : null}
                <View style={{ gap: 4 }}>
                  <View style={s.chips}>
                    <Chip label={call.status.replace("_", " ")} tone={statusTone(call.status)} />
                    <Text style={s.body}>
                      {call.tool}.{call.function}
                    </Text>
                  </View>
                  <Text style={s.meta}>
                    {formatWhen(call.created_at)} · {call.channel === "task" ? "scheduled task" : "chat"}
                    {call.latency_ms != null ? ` · ${call.latency_ms} ms` : ""}
                  </Text>
                  {call.error ? <Text style={s.meta}>{call.error}</Text> : null}
                </View>
              </View>
            ))
          ) : (
            <Text style={s.empty}>No tool calls yet.</Text>
          )}
        </Card>

        {events.length ? (
          <>
            <SectionTitle title="Switch history" />
            <Card>
              {events.map((e) => (
                <Text key={e.id} style={s.meta}>
                  {formatWhen(e.changed_at)} · {toolLabel(e.tool_name, e.scope)} switched {e.granted ? "on" : "off"}
                </Text>
              ))}
            </Card>
          </>
        ) : null}
      </ScrollView>
    </View>
  );
}
