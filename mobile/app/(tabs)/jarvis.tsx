// Jarvis: your phone as a remote for the PC, PC actions waiting for your OK, routines, what
// Custodian did (with undo), the proactive and learning settings, and the accounts it can use
// besides Google (Spotify, Microsoft 365, Home Assistant, Telegram).

import { useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { ActivityIndicator, Linking, Platform, RefreshControl, ScrollView, View } from "react-native";

import { ApprovalPrompt } from "../../components/ApprovalPrompt";
import { Text } from "../../components/Typography";
import { Button, Card, Chip, Field, IconButton, Notice, ScreenGradient, ScreenHeader, SectionTitle, Segmented, ToggleSwitch } from "../../components/ui";
import * as api from "../../lib/api";
import { confirmAction, formatWhen } from "../../lib/format";
import { space, themed, useTokens } from "../../lib/tokens";
import type { Activity, Integration, LearningStatus, Prefs, Routine, VoiceLanguage } from "../../lib/types";

type IconName = Parameters<typeof Button>[0]["icon"];

// Each is a plain command the server recognises exactly (app/fastpath.py): no model, ~0.2 s.
const REMOTE: { label: string; icon: IconName; command: string }[] = [
  { label: "Play / pause", icon: "play-outline", command: "pause" },
  { label: "Next", icon: "play-skip-forward-outline", command: "next track" },
  { label: "Previous", icon: "play-skip-back-outline", command: "previous track" },
  { label: "Volume up", icon: "volume-high-outline", command: "volume up" },
  { label: "Volume down", icon: "volume-low-outline", command: "volume down" },
  { label: "Mute", icon: "volume-mute-outline", command: "mute" },
  { label: "Dark mode", icon: "moon-outline", command: "dark mode" },
  { label: "Battery", icon: "battery-half-outline", command: "battery" },
  { label: "Lock PC", icon: "lock-closed-outline", command: "lock my pc" },
];

const SETTINGS: { key: keyof Prefs; label: string; hint: string }[] = [
  { key: "remote_approvals", label: "Allow PC actions from here", hint: "When you ask from this app or Telegram, risky PC actions ask you here instead of on the PC. Off: they always ask on the PC." },
  { key: "announce", label: "Speak reminders and alerts on the PC", hint: "Never in your quiet hours." },
  { key: "briefing", label: "Morning briefing", hint: "Your day, read out the first time you use the PC in the morning." },
  { key: "telegram_alerts", label: "Alerts on Telegram", hint: "Reminders, meetings and watcher alerts as Telegram messages, once Telegram is linked." },
  { key: "nightly_learning", label: "Learn from my corrections overnight", hint: "While the PC is idle at night, train on the replies you corrected (and chose to share), and switch only if the new model is at least as good." },
  { key: "pc_index", label: "Search inside my files", hint: "Index Documents, Downloads and Desktop so you can find files by what they say. Off deletes the index." },
];

const PERSONAS: { value: Prefs["persona"]; label: string }[] = [
  { value: "jarvis", label: "Jarvis" },
  { value: "plain", label: "Plain" },
];
const LANGUAGES: { value: VoiceLanguage; label: string }[] = [
  { value: "auto", label: "Any" },
  { value: "en", label: "English" },
  { value: "hi", label: "हिन्दी" },
  { value: "mr", label: "मराठी" },
];

const useStyles = themed((t) => ({
  screen: { flex: 1, backgroundColor: t.bg },
  content: { padding: space.lg, paddingTop: 0, gap: space.lg, width: "100%", maxWidth: 760, alignSelf: "center" },
  title: { fontSize: 17, fontWeight: "700", color: t.ink },
  body: { color: t.ink, fontSize: 15, lineHeight: 21 },
  meta: { color: t.inkSoft, fontSize: 13, lineHeight: 18 },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: space.sm },
  row: { flexDirection: "row", alignItems: "center", gap: space.md },
  divider: { height: 1, backgroundColor: t.border },
}));

export default function JarvisScreen() {
  const s = useStyles();
  const t = useTokens();
  const [pc, setPc] = useState<{ connected: boolean; device: string | null } | null>(null);
  const [prefs, setPrefsState] = useState<Prefs | null>(null);
  const [address, setAddress] = useState("sir");
  const [city, setCity] = useState("");
  const [routines, setRoutines] = useState<Routine[]>([]);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [accounts, setAccounts] = useState<Integration[]>([]);
  const [learning, setLearning] = useState<LearningStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [reply, setReply] = useState<string | null>(null);
  const [command, setCommand] = useState("");
  const [search, setSearch] = useState("");
  const [routineName, setRoutineName] = useState("");
  const [routineSteps, setRoutineSteps] = useState("");
  const [homeUrl, setHomeUrl] = useState("");
  const [homeToken, setHomeToken] = useState("");
  const [telegramCode, setTelegramCode] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [desk, p, r, a, i, l] = await Promise.all([
        api.getDesktopStatus().catch(() => null),
        api.getPrefs(),
        api.getRoutines(),
        api.getActivity(),
        api.getIntegrations(),
        api.getLearning().catch(() => null),
      ]);
      setPc(desk);
      setPrefsState(p);
      setAddress(p.address);
      setCity(p.home_city);
      setRoutines(r);
      setActivity(a);
      setAccounts(i.filter((x) => x.provider !== "google"));
      setLearning(l);
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

  async function run<T>(key: string, work: () => Promise<T>): Promise<T | undefined> {
    setBusy(key);
    try {
      const out = await work();
      setError(null);
      return out;
    } catch (err) {
      setError(api.errorMessage(err));
      return undefined;
    } finally {
      setBusy(null);
    }
  }

  async function send(text: string, key = "command") {
    const said = text.trim();
    if (!said) return;
    const out = await run(key, () => api.remote(said));
    if (out) {
      setReply(out.reply);
      setPc((prev) => ({ connected: out.desktop, device: prev?.device ?? null }));
      if (key === "command") setCommand("");
      void api.getActivity().then(setActivity).catch(() => undefined);
    }
  }

  async function toggle(key: keyof Prefs, value: boolean) {
    const out = await run(`pref:${key}`, () => api.setPrefs({ [key]: value }));
    if (out) setPrefsState(out);
  }

  async function savePrefs(key: string, changes: Partial<Prefs>) {
    const out = await run(`pref:${key}`, () => api.setPrefs(changes));
    if (out) {
      setPrefsState(out);
      setAddress(out.address);
      setCity(out.home_city);
    }
  }

  async function setLanguage(value: VoiceLanguage) {
    const out = await run("pref:voice_language", () => api.setPrefs({ voice_language: value }));
    if (out) setPrefsState(out);
  }

  async function addRoutine() {
    const steps = routineSteps.split("\n").map((x) => x.trim()).filter(Boolean);
    if (!routineName.trim() || !steps.length) return;
    const out = await run("routine:new", () => api.saveRoutine(routineName.trim(), steps));
    if (out) {
      setRoutineName("");
      setRoutineSteps("");
      setRoutines(await api.getRoutines());
    }
  }

  async function removeRoutine(routine: Routine) {
    if (!(await confirmAction(`Delete “${routine.name}”?`, routine.steps.join("\n"), "Delete"))) return;
    const deleted = await run(`routine:${routine.id}`, async () => {
      await api.deleteRoutine(routine.id);
      return true;
    });
    if (deleted) setRoutines((prev) => prev.filter((r) => r.id !== routine.id));
  }

  async function runRoutine(routine: Routine) {
    const out = await run(`routine:${routine.id}`, () => api.runRoutine(routine.id));
    if (out) setReply(out.reply);
  }

  async function undo(item: Activity) {
    const out = await run(`undo:${item.id}`, () => api.undoActivity(item.id));
    if (out) {
      setReply(`Done - I ${out.undone}.`);
      setActivity(await api.getActivity(search));
    }
  }

  async function findActivity() {
    const out = await run("activity", () => api.getActivity(search, 30));
    if (out) setActivity(out);
  }

  async function connect(provider: "spotify" | "microsoft") {
    const out = await run(`account:${provider}`, () => api.startAccount(provider));
    if (!out) return;
    if (Platform.OS === "web" && typeof window !== "undefined") window.location.assign(out.url);
    else await Linking.openURL(out.url);
  }

  async function disconnect(item: Integration) {
    if (item.provider === "google") return;
    if (!(await confirmAction(`Disconnect ${item.label ?? item.provider}?`, "Custodian forgets its access.", "Disconnect"))) return;
    const provider = item.provider;
    await run(`account:${provider}`, () => api.disconnectAccount(provider));
    await load();
  }

  async function connectHome() {
    const out = await run("account:homeassistant", () => api.connectHomeAssistant(homeUrl.trim(), homeToken.trim()));
    if (out) {
      setHomeToken("");
      await load();
    }
  }

  async function linkTelegram() {
    const out = await run("account:telegram", () => api.linkTelegram());
    if (out) setTelegramCode(out.how);
  }

  function accountCard(item: Integration) {
    const name = item.label ?? item.provider;
    return (
      <Card key={item.provider}>
        <View style={s.row}>
          <Text style={[s.title, { flex: 1 }]}>{name}</Text>
          {item.connected ? <Chip label="Connected" tone="success" /> : null}
        </View>
        {item.connected ? (
          <>
            {item.account ? <Text style={s.meta}>{item.account}</Text> : null}
            <Button compact variant="danger" icon="unlink-outline" label={`Disconnect ${name}`} loading={busy === `account:${item.provider}`}
              onPress={() => void disconnect(item)} />
          </>
        ) : item.provider === "homeassistant" ? (
          <>
            <Text style={s.meta}>Lights, AC, fans and plugs. Create a long-lived access token in your Home Assistant profile. Locks, alarms and doors are never controlled.</Text>
            <Field label="Address" value={homeUrl} onChangeText={setHomeUrl} placeholder="http://homeassistant.local:8123" autoCapitalize="none" autoCorrect={false} />
            <Field label="Access token" value={homeToken} onChangeText={setHomeToken} secureTextEntry autoCapitalize="none" autoCorrect={false} />
            <Button compact icon="home-outline" label="Connect Home Assistant" loading={busy === "account:homeassistant"}
              disabled={!homeUrl.trim() || homeToken.trim().length < 20} onPress={() => void connectHome()} />
          </>
        ) : item.provider === "telegram" ? (
          item.configured ? (
            <>
              <Text style={s.meta}>Chat with Custodian on Telegram, allow PC actions with a button there, and get alerts. Only your chat can use the bot.</Text>
              {telegramCode ? <Text style={s.body} selectable>{telegramCode}</Text> : null}
              <Button compact icon="paper-plane-outline" label={telegramCode ? "New code" : "Link Telegram"} loading={busy === "account:telegram"}
                onPress={() => void linkTelegram()} />
            </>
          ) : (
            <Text style={s.meta}>Not set up on this server: create a bot with @BotFather and set TELEGRAM_BOT_TOKEN (see the README).</Text>
          )
        ) : item.configured ? (
          <>
            <Text style={s.meta}>
              {item.provider === "spotify"
                ? "Play exact songs, albums and playlists on any device with Spotify open, instead of clicking the app."
                : "Your Outlook mail (read only) and calendar."}
            </Text>
            <Button compact icon="link-outline" label={`Connect ${name}`} loading={busy === `account:${item.provider}`}
              onPress={() => void connect(item.provider as "spotify" | "microsoft")} />
          </>
        ) : (
          <Text style={s.meta}>
            Not set up on this server: it needs an app registration ({item.provider === "spotify" ? "SPOTIFY" : "MICROSOFT"}_CLIENT_ID and _CLIENT_SECRET; see the README).
          </Text>
        )}
      </Card>
    );
  }

  return (
    <View style={s.screen}>
      <ScreenGradient />
      <ScreenHeader title="Jarvis" subtitle={pc?.connected ? `Connected to ${pc.device ?? "your PC"}` : "Your PC isn't connected"} />
      <ScrollView
        contentContainerStyle={s.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }} tintColor={t.accent2} />}
        keyboardShouldPersistTaps="handled"
      >
        {error ? <Notice message={error} actionLabel="Dismiss" onAction={() => setError(null)} /> : null}
        {loading ? <ActivityIndicator color={t.accent2} /> : null}
        <ApprovalPrompt active={busy !== null} />

        <SectionTitle title="Remote" hint="Control your PC from here. Anything risky asks you to allow it on this screen." />
        <Card>
          <View style={s.grid}>
            {REMOTE.map((b) => (
              <Button key={b.command} compact variant="secondary" icon={b.icon} label={b.label} loading={busy === `remote:${b.command}`}
                disabled={busy !== null && busy !== `remote:${b.command}`} onPress={() => void send(b.command, `remote:${b.command}`)} />
            ))}
          </View>
          <Field label="Tell your PC" value={command} onChangeText={setCommand} placeholder="play lofi on spotify, close chrome, turn off the bedroom lights"
            onSubmitEditing={() => void send(command)} returnKeyType="send" />
          <Button compact icon="send-outline" label="Send" loading={busy === "command"} disabled={!command.trim()} onPress={() => void send(command)} />
          {reply ? <Text style={s.body}>{reply}</Text> : null}
        </Card>

        <SectionTitle title={`Routines (${routines.length})`} hint="One phrase, many actions. Say the name (“work mode”) in any chat, or press Run." />
        <Card>
          {routines.map((r, i) => (
            <View key={r.id} style={{ gap: space.xs }}>
              {i > 0 ? <View style={s.divider} /> : null}
              <View style={s.row}>
                <View style={{ flex: 1, gap: 2 }}>
                  <Text style={s.title}>{r.name}</Text>
                  <Text style={s.meta}>{r.steps.join(" → ")}</Text>
                  {r.last_run_at ? <Text style={s.meta}>Last run {formatWhen(r.last_run_at)}</Text> : null}
                </View>
                <Button compact variant="secondary" icon="play-outline" label="Run" loading={busy === `routine:${r.id}`} onPress={() => void runRoutine(r)} />
                <IconButton icon="trash-outline" label={`Delete ${r.name}`} onPress={() => void removeRoutine(r)} />
              </View>
            </View>
          ))}
          {!routines.length ? <Text style={s.meta}>None yet. Try “Work mode”: open vs code, volume 30, do not disturb on.</Text> : null}
          <Field label="New routine" value={routineName} onChangeText={setRoutineName} placeholder="Good night" />
          <Field value={routineSteps} onChangeText={setRoutineSteps} multiline placeholder={"One step per line:\nclose chrome\nalarm at 07:00\nlock my pc"}
            style={{ minHeight: 96, textAlignVertical: "top" }} />
          <Button compact icon="add-circle-outline" label="Save routine" loading={busy === "routine:new"}
            disabled={!routineName.trim() || !routineSteps.trim()} onPress={() => void addRoutine()} />
        </Card>

        <SectionTitle title="What Custodian did" hint="Everything it did on your PC and with your tools. Undo puts things back where it can: files from the Recycle Bin, the volume, moves." />
        <Card>
          <View style={s.row}>
            <View style={{ flex: 1 }}>
              <Field value={search} onChangeText={setSearch} placeholder="What did I send Tanishka?" onSubmitEditing={() => void findActivity()} returnKeyType="search" />
            </View>
            <IconButton icon="search-outline" label="Search what Custodian did" onPress={() => void findActivity()} />
          </View>
          {activity.slice(0, 20).map((a) => (
            <View key={a.id} style={s.row}>
              <View style={{ flex: 1, gap: 2 }}>
                <Text style={s.body}>{a.tool} · {a.function.replace(/_/g, " ")}</Text>
                <Text style={s.meta}>{formatWhen(a.when)} · {a.undone_at ? "undone" : a.status}{a.error ? ` · ${a.error}` : ""}</Text>
              </View>
              {a.can_undo ? (
                <Button compact variant="secondary" icon="arrow-undo-outline" label="Undo" loading={busy === `undo:${a.id}`} onPress={() => void undo(a)} />
              ) : null}
            </View>
          ))}
          {!activity.length ? <Text style={s.meta}>Nothing yet.</Text> : null}
        </Card>

        <SectionTitle title="Settings" />
        {prefs ? (
          <Card>
            {SETTINGS.map((item, i) => (
              <View key={item.key} style={{ gap: space.xs }}>
                {i > 0 ? <View style={s.divider} /> : null}
                <View style={s.row}>
                  <View style={{ flex: 1, gap: 2 }}>
                    <Text style={s.body}>{item.label}</Text>
                    <Text style={s.meta}>{item.hint}</Text>
                  </View>
                  <ToggleSwitch accessibilityLabel={item.label} value={Boolean(prefs[item.key])} disabled={busy === `pref:${item.key}`}
                    onValueChange={(v) => void toggle(item.key, v)} />
                </View>
              </View>
            ))}
            <View style={s.divider} />
            <Text style={s.body}>The language you speak to the PC in</Text>
            <Segmented options={LANGUAGES} value={prefs.voice_language} onChange={(v) => void setLanguage(v)} />
            <View style={s.divider} />
            <Text style={s.body}>How the PC talks when you speak to it</Text>
            <Segmented options={PERSONAS} value={prefs.persona} onChange={(v) => void savePrefs("persona", { persona: v })} />
            <Text style={s.meta}>Jarvis is calm, brief and polite, and calls you what you choose below. Typed chats keep Custodian's own voice.</Text>
            <Field label="Jarvis calls you" value={address} onChangeText={setAddress} placeholder="sir" maxLength={40} autoCorrect={false} />
            <Field label="Your city, for the weather" value={city} onChangeText={setCity} maxLength={40}
              placeholder="Empty: the city your memories say you live in" />
            <Button compact icon="checkmark" label="Save" loading={busy === "pref:jarvis"}
              disabled={address.trim() === prefs.address && city.trim() === prefs.home_city}
              onPress={() => void savePrefs("jarvis", { address: address.trim() || "sir", home_city: city.trim() })} />
            {learning ? (
              <Text style={s.meta}>
                {learning.new_examples} correction{learning.new_examples === 1 ? "" : "s"} waiting to be learned.
                {learning.last_run ? ` Last night's run (${formatWhen(learning.last_run.at)}): ${learning.last_run.summary}` : ""}
              </Text>
            ) : null}
          </Card>
        ) : null}

        <SectionTitle title="Connected accounts" hint="Each one's tools have their own switches in Tools & Data, all off until you turn them on." />
        {accounts.map(accountCard)}
      </ScrollView>
    </View>
  );
}
