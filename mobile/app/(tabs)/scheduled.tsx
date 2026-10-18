// Scheduled tasks: jobs Custodian runs on its own (like a 9:00 daily summary), with their
// schedule, pinned skill version, last run and output. Run one now, pause it, or add one.

import { useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { ActivityIndicator, RefreshControl, ScrollView, View } from "react-native";
import { Text } from "../../components/Typography";

import { Button, Card, Chip, Field, Notice, ScreenGradient, ScreenHeader, SectionTitle, Segmented, ToggleSwitch, statusTone } from "../../components/ui";
import * as api from "../../lib/api";
import { confirmAction, describeCron, deviceTimeZone, formatWhen } from "../../lib/format";
import { space, themed, useTokens } from "../../lib/tokens";
import type { Conversation, Skill, Task } from "../../lib/types";

const PRESETS = [
  { value: "0 9 * * *", label: "Daily 09:00" },
  { value: "30 8 * * 1-5", label: "Weekdays 08:30" },
  { value: "0 18 * * 0", label: "Sundays 18:00" },
  { value: "0 * * * *", label: "Hourly" },
];

const useStyles = themed((t) => ({
  screen: { flex: 1, backgroundColor: t.bg },
  content: { padding: space.lg, paddingTop: 0, gap: space.lg, width: "100%", maxWidth: 760, alignSelf: "center" },
  name: { fontSize: 17, fontWeight: "700", color: t.ink },
  body: { color: t.ink, fontSize: 15, lineHeight: 21 },
  meta: { color: t.inkSoft, fontSize: 13, lineHeight: 18 },
  row: { flexDirection: "row", alignItems: "center", gap: space.sm, flexWrap: "wrap" },
  between: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: space.md },
  output: { backgroundColor: t.surface2, borderRadius: 10, padding: space.md, gap: space.sm },
  empty: { color: t.inkSoft, fontSize: 14 },
  label: { fontSize: 13, fontWeight: "600", color: t.inkSoft },
}));

function TaskCard({
  task,
  running,
  output,
  onRun,
  onToggle,
  onShowOutput,
  onUpdateSkill,
  onDelete,
}: {
  task: Task;
  running: boolean;
  output: Conversation | "loading" | undefined;
  onRun: () => void;
  onToggle: (active: boolean) => void;
  onShowOutput: () => void;
  onUpdateSkill: () => void;
  onDelete: () => void;
}) {
  const s = useStyles();
  const t = useTokens();
  const newer = task.skill.latest_version != null && task.skill.latest_version > task.skill.version;
  const reply = output && output !== "loading" ? [...output.messages].reverse().find((m) => m.role === "assistant") : undefined;
  return (
    <Card>
      <View style={s.between}>
        <View style={{ flex: 1, gap: 4 }}>
          <Text style={s.name}>{task.name}</Text>
          <Text style={s.meta}>
            {describeCron(task.cron_schedule)} · {task.timezone}
          </Text>
        </View>
        <ToggleSwitch accessibilityLabel={`${task.name} active`} value={task.status === "active"} onValueChange={onToggle} />
      </View>

      <View style={s.row}>
        <Chip label={`${task.skill.name} v${task.skill.version}`} tone={task.skill.active ? "neutral" : "danger"} />
        {!task.skill.active ? <Text style={s.meta}>This version was rolled back.</Text> : null}
        {task.status === "active" ? (
          <Text style={s.meta}>Next run {formatWhen(task.next_run_at)}</Text>
        ) : (
          <Chip label="paused" tone="warning" />
        )}
      </View>
      {newer ? (
        <View style={s.row}>
          <Text style={s.meta}>Skill v{task.skill.latest_version} is available.</Text>
          <Button compact variant="ghost" label="Use it" onPress={onUpdateSkill} />
        </View>
      ) : null}

      {task.last_run ? (
        <View style={s.row}>
          <Text style={s.meta}>Last run {formatWhen(task.last_run.started_at)}</Text>
          <Chip label={task.last_run.status} tone={statusTone(task.last_run.status)} />
          {task.last_run.error ? <Text style={s.meta}>{task.last_run.error}</Text> : null}
        </View>
      ) : (
        <Text style={s.meta}>Hasn't run yet.</Text>
      )}

      {output === "loading" ? <ActivityIndicator color={t.accent2} /> : null}
      {reply ? (
        <View style={s.output}>
          <Text style={s.label}>OUTPUT</Text>
          <Text selectable style={s.body}>
            {reply.content}
          </Text>
        </View>
      ) : null}

      <View style={s.row}>
        <Button compact label="Run now" icon="play" onPress={onRun} loading={running} />
        {task.last_run?.conversation_id ? (
          <Button compact variant="secondary" label={output ? "Hide output" : "Show output"} onPress={onShowOutput} />
        ) : null}
        <Button compact variant="ghost" label="Delete" onPress={onDelete} />
      </View>
    </Card>
  );
}

export default function ScheduledScreen() {
  const s = useStyles();
  const t = useTokens();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runningId, setRunningId] = useState<number | null>(null);
  const [outputs, setOutputs] = useState<Record<number, Conversation | "loading">>({});

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [skill, setSkill] = useState("daily-summary");
  const [cron, setCron] = useState(PRESETS[0].value);
  const [timezone, setTimezone] = useState(deviceTimeZone());
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    try {
      const [taskList, skillList] = await Promise.all([api.getTasks(), api.getSkills()]);
      setTasks(taskList);
      setSkills(skillList);
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

  const replace = (task: Task) => setTasks((prev) => prev.map((x) => (x.id === task.id ? task : x)));

  async function showOutput(task: Task, force = false) {
    const conversationId = task.last_run?.conversation_id;
    if (!conversationId) return;
    if (outputs[task.id] && !force) {
      setOutputs(({ [task.id]: _, ...rest }) => rest);
      return;
    }
    setOutputs((prev) => ({ ...prev, [task.id]: "loading" }));
    try {
      const conversation = await api.getConversation(conversationId);
      setOutputs((prev) => ({ ...prev, [task.id]: conversation }));
    } catch (err) {
      setOutputs(({ [task.id]: _, ...rest }) => rest);
      setError(api.errorMessage(err));
    }
  }

  async function runNow(task: Task) {
    setRunningId(task.id);
    try {
      await api.runTask(task.id);
      const fresh = await api.getTasks();
      setTasks(fresh);
      const updated = fresh.find((x) => x.id === task.id);
      if (updated) await showOutput(updated, true);
      setError(null);
    } catch (err) {
      setError(api.errorMessage(err));
    } finally {
      setRunningId(null);
    }
  }

  async function toggle(task: Task, active: boolean) {
    replace({ ...task, status: active ? "active" : "paused" });
    try {
      replace(await api.updateTask(task.id, { status: active ? "active" : "paused" }));
    } catch (err) {
      replace(task);
      setError(api.errorMessage(err));
    }
  }

  async function updateSkill(task: Task) {
    try {
      replace(await api.updateTask(task.id, { skill: task.skill.name }));
    } catch (err) {
      setError(api.errorMessage(err));
    }
  }

  async function remove(task: Task) {
    if (!(await confirmAction(`Delete “${task.name}”?`, "Its run history goes too. Past output stays in your conversations."))) return;
    try {
      await api.deleteTask(task.id);
      setTasks((prev) => prev.filter((x) => x.id !== task.id));
    } catch (err) {
      setError(api.errorMessage(err));
    }
  }

  async function create() {
    if (creating) return;
    setCreating(true);
    try {
      const task = await api.createTask({ name: name.trim().toLowerCase(), skill, cron_schedule: cron.trim(), timezone: timezone.trim() });
      setTasks((prev) => [...prev, task].sort((a, b) => a.name.localeCompare(b.name)));
      setName("");
      setShowForm(false);
      setError(null);
    } catch (err) {
      setError(api.errorMessage(err));
    } finally {
      setCreating(false);
    }
  }

  return (
    <View style={s.screen}>
      <ScreenGradient />
      <ScreenHeader
        title="Scheduled"
        subtitle="Jobs Custodian runs on its own"
        right={<Button compact variant="secondary" icon={showForm ? "close" : "add"} label={showForm ? "Cancel" : "New task"} onPress={() => setShowForm((v) => !v)} />}
      />
      <ScrollView
        contentContainerStyle={s.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }} tintColor={t.accent2} />}
        keyboardShouldPersistTaps="handled"
      >
        {error ? <Notice message={error} actionLabel="Dismiss" onAction={() => setError(null)} /> : null}

        {showForm ? (
          <Card>
            <SectionTitle title="New task" hint="A task runs one skill on a schedule, with only the tools that skill allows." />
            <Field label="Name" value={name} onChangeText={setName} placeholder="morning-brief" autoCapitalize="none" autoCorrect={false} />
            <Text style={s.label}>Skill</Text>
            <Segmented options={skills.map((x) => ({ value: x.name, label: `${x.name} v${x.version}` }))} value={skill} onChange={setSkill} />
            {skills.find((x) => x.name === skill)?.description ? (
              <Text style={s.meta}>{skills.find((x) => x.name === skill)?.description}</Text>
            ) : null}
            <Text style={s.label}>Schedule</Text>
            <Segmented options={PRESETS} value={PRESETS.some((p) => p.value === cron) ? cron : ""} onChange={setCron} />
            <Field label="Cron expression" value={cron} onChangeText={setCron} autoCapitalize="none" autoCorrect={false} />
            <Text style={s.meta}>
              {describeCron(cron)} — minute, hour, day of month, month, day of week. Runs must be at least 15 minutes
              apart, since each one uses the model.
            </Text>
            <Field label="Time zone" value={timezone} onChangeText={setTimezone} autoCapitalize="none" autoCorrect={false} />
            <Button label="Create task" onPress={create} loading={creating} disabled={!name.trim() || !cron.trim()} />
          </Card>
        ) : null}

        {loading ? <ActivityIndicator color={t.accent2} /> : null}
        {!loading && !tasks.length && !showForm ? (
          <Text style={s.empty}>No scheduled tasks yet. Add one, such as a daily summary at 09:00.</Text>
        ) : null}
        {tasks.map((task) => (
          <TaskCard
            key={task.id}
            task={task}
            running={runningId === task.id}
            output={outputs[task.id]}
            onRun={() => void runNow(task)}
            onToggle={(active) => void toggle(task, active)}
            onShowOutput={() => void showOutput(task)}
            onUpdateSkill={() => void updateSkill(task)}
            onDelete={() => void remove(task)}
          />
        ))}
      </ScrollView>
    </View>
  );
}
