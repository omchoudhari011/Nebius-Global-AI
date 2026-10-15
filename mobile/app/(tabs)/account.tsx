// Account: your plan and what you've used, where your data goes and how long it's kept,
// downloading or erasing it, and signing out.

import { useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { ActivityIndicator, Linking, Platform, RefreshControl, ScrollView, Share, Text, View } from "react-native";

import { Button, Card, Chip, Field, Notice, ScreenHeader, SectionTitle, Segmented } from "../../components/ui";
import * as api from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { confirmAction, formatDateTime, formatWhen } from "../../lib/format";
import { radius, space, themed, useTokens } from "../../lib/tokens";
import type { Billing, DataService, Limit, Plan, PrivacySummary, RedactMode, ServiceLocation, Session } from "../../lib/types";

const RETENTION = [
  { value: "keep", label: "Until I delete them" },
  { value: "365", label: "1 year" },
  { value: "90", label: "90 days" },
  { value: "30", label: "30 days" },
  { value: "7", label: "7 days" },
];
const REDACT: { value: RedactMode; label: string }[] = [
  { value: "auto", label: "When the model is online" },
  { value: "on", label: "Always" },
  { value: "off", label: "Never" },
];
const WHERE: Record<ServiceLocation, string> = {
  offline: "nothing leaves this server",
  "this device": "runs on this machine",
  "local network": "on your local network",
  internet: "sent over the internet",
};

const useStyles = themed((t) => ({
  screen: { flex: 1, backgroundColor: t.bg },
  content: { padding: space.lg, paddingTop: 0, gap: space.lg, width: "100%", maxWidth: 760, alignSelf: "center" },
  rowBetween: { flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between", gap: space.md },
  planName: { fontSize: 22, fontWeight: "700", color: t.ink },
  body: { color: t.ink, fontSize: 15, lineHeight: 21 },
  label: { color: t.ink, fontSize: 14, fontWeight: "600" },
  meta: { color: t.inkSoft, fontSize: 13, lineHeight: 18 },
  divider: { height: 1, backgroundColor: t.border },
  stats: { flexDirection: "row", flexWrap: "wrap", gap: space.lg },
  stat: { minWidth: 96, gap: 2 },
  statValue: { color: t.ink, fontSize: 20, fontWeight: "700" },
  plans: { flexDirection: "row", flexWrap: "wrap", gap: space.md },
  planCard: { flexGrow: 1, flexBasis: 200, borderWidth: 1, borderColor: t.border, borderRadius: radius.md, padding: space.md, gap: 6 },
  planCardCurrent: { borderColor: t.accent, borderWidth: 2 },
  planCardName: { color: t.ink, fontSize: 16, fontWeight: "700" },
  feature: { color: t.ink, fontSize: 13, lineHeight: 18 },
}));

/** 1234 -> "1,234"; 1_500_000 -> "1.5M" (Hermes' Intl has no compact notation everywhere). */
function amount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 10_000) return `${(n / 1000).toFixed(0)}k`;
  return n.toLocaleString();
}

function Meter({ label, limit }: { label: string; limit: Limit }) {
  const t = useTokens();
  const s = useStyles();
  const ratio = limit.limit ? Math.min(1, limit.used / limit.limit) : 0;
  const color = ratio >= 1 ? t.danger : ratio >= 0.8 ? t.warning : t.accent2;
  return (
    <View style={{ gap: 6 }}>
      <View style={s.rowBetween}>
        <Text style={s.label}>{label}</Text>
        <Text style={s.meta}>{limit.limit == null ? `${amount(limit.used)} · no limit` : `${amount(limit.used)} of ${amount(limit.limit)}`}</Text>
      </View>
      {limit.limit != null ? (
        <View
          accessibilityRole="progressbar"
          accessibilityLabel={label}
          accessibilityValue={{ min: 0, max: limit.limit, now: Math.min(limit.used, limit.limit) }}
          style={{ height: 6, borderRadius: radius.pill, backgroundColor: t.neutralBg, overflow: "hidden" }}
        >
          <View style={{ width: `${Math.round(ratio * 100)}%`, height: "100%", backgroundColor: color }} />
        </View>
      ) : null}
    </View>
  );
}

/** "chat turn" or "chat turns" for a count. */
function plural(n: number, one: string, many = `${one}s`): string {
  return n === 1 ? one : many;
}

function Stat({ label, value }: { label: string; value: string }) {
  const s = useStyles();
  return (
    <View style={s.stat}>
      <Text style={s.statValue}>{value}</Text>
      <Text style={s.meta}>{label}</Text>
    </View>
  );
}

function planSource(billing: Billing): string {
  if (billing.source === "preview") return "Development preview: every feature of this plan works without a license.";
  if (billing.source === "free" || !billing.license) return "No license on this server.";
  const { licensee, expires, status } = billing.license;
  if (!expires) return `Licensed to ${licensee}, with no expiry.`;
  const when = new Date(`${expires}T00:00:00`).toLocaleDateString();
  return status === "grace"
    ? `Licensed to ${licensee}. Expired on ${when}; renew within a week to keep this plan.`
    : `Licensed to ${licensee}, valid until ${when}.`;
}

function service(item: DataService): string {
  return `${item.name}${item.host ? ` at ${item.host}` : ""} · ${WHERE[item.location]}`;
}

async function saveExport(data: Record<string, unknown>): Promise<void> {
  const text = JSON.stringify(data, null, 2);
  const filename = `custodian-export-${new Date().toISOString().slice(0, 10)}.json`;
  if (Platform.OS === "web") {
    const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return;
  }
  await Share.share({ title: filename, message: text });
}

function PlanOption({ plan, current }: { plan: Plan; current: boolean }) {
  const s = useStyles();
  return (
    <View style={[s.planCard, current && s.planCardCurrent]}>
      <View style={s.rowBetween}>
        <Text style={s.planCardName}>{plan.name}</Text>
        {current ? <Chip label="Your plan" tone="accent" /> : null}
      </View>
      <Text style={s.label}>{plan.price}</Text>
      <Text style={s.meta}>{plan.summary}</Text>
      {plan.features.map((feature) => (
        <Text key={feature} style={s.feature}>
          ✓ {feature}
        </Text>
      ))}
    </View>
  );
}

export default function AccountScreen() {
  const s = useStyles();
  const t = useTokens();
  const { user, server, signOut } = useAuth();

  const [billing, setBilling] = useState<Billing | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [privacy, setPrivacy] = useState<PrivacySummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [licenseKey, setLicenseKey] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [b, p, d] = await Promise.all([api.getBilling(), api.getPrivacy(), api.getSessions()]);
      setBilling(b);
      setPrivacy(p);
      setSessions(d);
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

  async function run(key: string, action: () => Promise<void>) {
    setBusy(key);
    setNotice(null);
    try {
      await action();
      setError(null);
    } catch (err) {
      setError(api.errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  const activate = () =>
    run("license", async () => {
      setBilling(await api.activateLicense(licenseKey.trim()));
      setLicenseKey("");
      setNotice("License activated.");
    });

  const removeLicense = async () => {
    if (!(await confirmAction("Remove the license?", "This server goes back to the Free plan. Nothing you've stored is deleted.", "Remove"))) return;
    await run("license", async () => setBilling(await api.removeLicense()));
  };

  const setRetention = (value: string) =>
    run("retention", async () => {
      setPrivacy(await api.updatePrivacy({ retention_days: value === "keep" ? null : Number(value) }));
    });

  const setRedaction = (value: RedactMode) =>
    run("redact", async () => {
      setPrivacy(await api.updatePrivacy({ redact_secrets: value }));
    });

  const download = () =>
    run("export", async () => {
      await saveExport(await api.exportData());
      setNotice(Platform.OS === "web" ? "Your data was downloaded. It's in plain text, so keep the file private." : "Your data is ready to save or share. It's in plain text, so keep it private.");
    });

  const erase = async () => {
    const ok = await confirmAction(
      "Erase all your data?",
      "Every chat, memory, note, calendar event and scheduled task on this server is deleted for good. Download your data first if you want a copy.",
      "Erase everything",
    );
    if (!ok) return;
    await run("erase", async () => {
      const { erased } = await api.eraseData();
      setNotice(`Erased ${erased.conversations ?? 0} chats, ${erased.memories ?? 0} memories, ${erased.notes ?? 0} notes and ${erased.calendar_events ?? 0} calendar events.`);
      await load();
    });
  };

  const signOutDevice = async (session: Session) => {
    const ok = await confirmAction("Sign out this device?", `${session.device ?? "That device"} will need the passcode to sign in again.`, "Sign out");
    if (!ok) return;
    await run(`session-${session.id}`, async () => {
      await api.revokeSession(session.id);
      setSessions(await api.getSessions());
    });
  };

  const signOutOthers = async () => {
    const ok = await confirmAction("Sign out every other device?", "Only this device stays signed in. Use this if a phone or computer was lost.", "Sign out others");
    if (!ok) return;
    await run("sessions", async () => {
      const { signed_out } = await api.revokeOtherSessions();
      setSessions(await api.getSessions());
      setNotice(`Signed out ${signed_out} other ${plural(signed_out, "device")}.`);
    });
  };

  async function confirmSignOut() {
    if (await confirmAction("Sign out?", "You'll need the passcode to sign in again.", "Sign out")) await signOut();
  }

  const retentionValue = privacy?.retention_days == null ? "keep" : String(privacy.retention_days);
  const retentionOptions = RETENTION.some((o) => o.value === retentionValue)
    ? RETENTION
    : [...RETENTION, { value: retentionValue, label: `${retentionValue} days` }];
  const usage = billing?.usage;

  return (
    <View style={s.screen}>
      <ScreenHeader title="Account" subtitle="Your plan, your privacy, your data" />
      <ScrollView
        contentContainerStyle={s.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }} tintColor={t.accent2} />}
        keyboardShouldPersistTaps="handled"
      >
        {error ? <Notice message={error} actionLabel="Retry" onAction={load} /> : null}
        {notice ? <Notice message={notice} tone="success" /> : null}
        {loading ? <ActivityIndicator color={t.accent2} /> : null}

        {billing && usage ? (
          <>
            <SectionTitle title="Plan & usage" />
            <Card>
              <View style={s.rowBetween}>
                <View style={{ flex: 1, gap: 2 }}>
                  <Text style={s.planName}>{billing.plan.name}</Text>
                  <Text style={s.meta}>{planSource(billing)}</Text>
                </View>
                <Chip label={billing.plan.price} tone="accent" />
              </View>
              {billing.problem ? <Notice tone="warning" message={billing.problem} /> : null}
              <Meter label="Memories" limit={billing.limits.memories} />
              <Meter label="Active scheduled tasks" limit={billing.limits.active_tasks} />
              {billing.limits.monthly_tokens.limit != null ? (
                <Meter label={`Model tokens this month (renews ${new Date(`${usage.renews}T00:00:00`).toLocaleDateString()})`} limit={billing.limits.monthly_tokens} />
              ) : null}
              <Text style={s.meta}>
                Search by meaning: {billing.limits.semantic_search ? "included" : "not in this plan (keyword search still finds everything you've said)"}
              </Text>
            </Card>

            <Card>
              <Text style={s.label}>This month</Text>
              <View style={s.stats}>
                <Stat label={plural(usage.month.chat_turns, "chat turn")} value={amount(usage.month.chat_turns)} />
                <Stat label={plural(usage.month.task_runs, "task run")} value={amount(usage.month.task_runs)} />
                <Stat label={plural(usage.month.model_calls, "model call")} value={amount(usage.month.model_calls)} />
                <Stat label={usage.estimated ? "tokens (estimated)" : "tokens"} value={`${usage.estimated ? "≈" : ""}${amount(usage.month.tokens)}`} />
                {usage.month.cost_usd != null ? <Stat label="model cost (estimate)" value={`$${usage.month.cost_usd.toFixed(2)}`} /> : null}
              </View>
              <Text style={s.meta}>
                Today: {usage.today.chat_turns} {plural(usage.today.chat_turns, "chat turn")}, {usage.today.model_calls}{" "}
                {plural(usage.today.model_calls, "model call")}.
                {usage.prices_set ? "" : " Set MODEL_PRICE_INPUT_PER_M and MODEL_PRICE_OUTPUT_PER_M on the server to see costs."}
              </Text>
            </Card>

            <Card>
              <Text style={s.label}>Plans</Text>
              <View style={s.plans}>
                {billing.plans.map((plan) => (
                  <PlanOption key={plan.id} plan={plan} current={plan.id === billing.plan.id} />
                ))}
              </View>
              {billing.source === "license" ? (
                <Button label="Remove license" variant="secondary" icon="close-circle-outline" onPress={() => void removeLicense()} loading={busy === "license"} />
              ) : (
                <>
                  <Field
                    label="License key"
                    value={licenseKey}
                    onChangeText={setLicenseKey}
                    placeholder="CL1.…"
                    autoCapitalize="none"
                    autoCorrect={false}
                    onSubmitEditing={activate}
                  />
                  <Button label="Activate" icon="key-outline" onPress={activate} loading={busy === "license"} disabled={!licenseKey.trim()} />
                  {billing.purchase_url ? (
                    <Button label="Get a license" variant="ghost" icon="open-outline" onPress={() => void Linking.openURL(billing.purchase_url as string)} />
                  ) : null}
                  <Text style={s.meta}>Keys are checked on this server; nothing is sent anywhere to verify them.</Text>
                </>
              )}
            </Card>
          </>
        ) : null}

        {privacy ? (
          <>
            <SectionTitle title="Privacy" hint="Every privacy control is included in every plan." />
            <Card>
              <Text style={s.label}>Where your data goes</Text>
              <Text style={s.body}>Chat model: {service(privacy.model)}</Text>
              <Text style={s.body}>Search by meaning: {privacy.embeddings ? service(privacy.embeddings) : "off"}</Text>
              <View style={s.divider} />
              <Text style={s.label}>Withhold secrets from the model</Text>
              <Text style={s.meta}>
                Passwords, PINs, card, account and ID numbers are replaced with placeholders before a message leaves this server.
                Right now: {privacy.redaction.active ? "on" : "off"}.
              </Text>
              <Segmented options={REDACT} value={privacy.redaction.mode} onChange={(v) => void setRedaction(v)} />
            </Card>

            <Card>
              <Text style={s.label}>Keep chats</Text>
              <Segmented options={retentionOptions} value={retentionValue} onChange={(v) => void setRetention(v)} />
              <Text style={s.meta}>
                Older chats are deleted every hour. Memories stay until you delete them. Off-the-record chats are deleted{" "}
                {privacy.private_chat_hours} hours after their last message.
              </Text>
            </Card>

            <Card>
              <Text style={s.label}>Stored on this server</Text>
              <View style={s.stats}>
                <Stat label={plural(privacy.stored.conversations, "chat")} value={amount(privacy.stored.conversations)} />
                <Stat label={plural(privacy.stored.messages, "message")} value={amount(privacy.stored.messages)} />
                <Stat label={plural(privacy.stored.memories, "memory", "memories")} value={amount(privacy.stored.memories)} />
                <Stat label={plural(privacy.stored.notes, "note")} value={amount(privacy.stored.notes)} />
                <Stat label={plural(privacy.stored.calendar_events, "calendar event")} value={amount(privacy.stored.calendar_events)} />
              </View>
              <Text style={s.meta}>
                Encrypted: {privacy.encrypted.join(", ")}. Not encrypted: {privacy.plaintext.join("; ")}.
                {privacy.oldest_message_at ? ` Oldest message: ${formatDateTime(privacy.oldest_message_at)}.` : ""}
              </Text>
              <Button label="Download my data" variant="secondary" icon="download-outline" onPress={download} loading={busy === "export"} />
              <Button label="Erase all my data" variant="danger" icon="trash-outline" onPress={() => void erase()} loading={busy === "erase"} />
            </Card>
          </>
        ) : null}

        <SectionTitle title="Account" />
        <Card>
          <Text style={s.body}>{user?.email}</Text>
          {server ? (
            <Text style={s.meta}>
              Custodian {server.version} · model {server.model} · {server.timezone} · scheduler {server.scheduler ? "on" : "off"}
              {api.getApiBase() ? ` · ${api.getApiBase()}` : ""}
            </Text>
          ) : null}
          <Button label="Sign out" variant="danger" icon="log-out-outline" onPress={() => void confirmSignOut()} />
        </Card>

        {sessions.length ? (
          <Card>
            <Text style={s.label}>Signed-in devices</Text>
            {sessions.map((session) => (
              <View key={session.id} style={s.rowBetween}>
                <View style={{ flex: 1, gap: 2 }}>
                  <Text style={s.body}>{session.device ?? (session.client === "native" ? "Custodian app" : "A browser")}</Text>
                  <Text style={s.meta}>
                    Signed in {formatDateTime(session.created_at)} · last used {formatWhen(session.last_seen_at)}
                  </Text>
                </View>
                {session.current ? (
                  <Chip label="This device" tone="accent" />
                ) : (
                  <Button
                    compact
                    variant="secondary"
                    label="Sign out"
                    onPress={() => void signOutDevice(session)}
                    loading={busy === `session-${session.id}`}
                  />
                )}
              </View>
            ))}
            {sessions.some((session) => !session.current) ? (
              <Button
                label="Sign out every other device"
                variant="secondary"
                icon="phone-portrait-outline"
                onPress={() => void signOutOthers()}
                loading={busy === "sessions"}
              />
            ) : null}
          </Card>
        ) : null}
      </ScrollView>
    </View>
  );
}
