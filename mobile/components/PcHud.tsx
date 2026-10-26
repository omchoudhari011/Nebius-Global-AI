// The Jarvis HUD, in the app: the PC's vital signs (sent by the desktop app every 30 s), the
// weather where you are, and your day - a small dark "screen" in the HUD's own cyan, like the
// one on the PC. Its arc reactor breathes while the PC reports in, and stays still when the
// system asks for reduced motion.

import { useEffect, useRef, useState } from "react";
import { AccessibilityInfo, Animated, Easing, Platform, StyleSheet, View } from "react-native";

import type { HudSnapshot, PcVitals } from "../lib/types";
import { Text } from "./Typography";

const HUD = { bg: "#04101A", panel: "#071A28", line: "#12405A", cyan: "#5FE3FF", ink: "#D7F6FF", soft: "#8FB8C8", warn: "#FFB547", bad: "#FF6B6B" };

function level(percent: number, warnAt = 80, badAt = 92): string {
  return percent >= badAt ? HUD.bad : percent >= warnAt ? HUD.warn : HUD.cyan;
}

function Gauge({ label, percent, value, color }: { label: string; percent: number; value: string; color?: string }) {
  const shown = Math.max(0, Math.min(100, percent));
  return (
    <View style={s.gauge} accessible accessibilityLabel={`${label}: ${value}`}>
      <Text style={s.gaugeLabel}>{label}</Text>
      <View style={s.track}>
        <View style={[s.fill, { width: `${shown}%`, backgroundColor: color ?? level(shown) }]} />
      </View>
      <Text style={s.gaugeValue}>{value}</Text>
    </View>
  );
}

function Reactor({ live }: { live: boolean }) {
  const pulse = useRef(new Animated.Value(0)).current;
  const [reduceMotion, setReduceMotion] = useState(false);
  useEffect(() => {
    AccessibilityInfo.isReduceMotionEnabled().then(setReduceMotion).catch(() => undefined);
    const sub = AccessibilityInfo.addEventListener("reduceMotionChanged", setReduceMotion);
    return () => sub.remove();
  }, []);
  useEffect(() => {
    if (!live || reduceMotion) {
      pulse.setValue(0);
      return;
    }
    const native = Platform.OS !== "web";
    const loop = Animated.loop(Animated.sequence([
      Animated.timing(pulse, { toValue: 1, duration: 1800, easing: Easing.inOut(Easing.sin), useNativeDriver: native }),
      Animated.timing(pulse, { toValue: 0, duration: 1800, easing: Easing.inOut(Easing.sin), useNativeDriver: native }),
    ]));
    loop.start();
    return () => loop.stop();
  }, [live, reduceMotion, pulse]);
  const glow = pulse.interpolate({ inputRange: [0, 1], outputRange: [0.55, 1] });
  const scale = pulse.interpolate({ inputRange: [0, 1], outputRange: [0.94, 1.04] });
  return (
    <View style={s.reactor} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
      <View style={[s.ring, { width: 64, height: 64, opacity: live ? 0.5 : 0.2 }]} />
      <View style={[s.ring, { width: 46, height: 46, borderStyle: "dashed", opacity: live ? 0.8 : 0.3 }]} />
      <Animated.View style={[s.core, { opacity: live ? glow : 0.25, transform: [{ scale }] }]} />
    </View>
  );
}

function ago(seconds: number): string {
  return seconds < 5 ? "just now" : seconds < 60 ? `${seconds} s ago` : `${Math.round(seconds / 60)} min ago`;
}

function vitals(pc: PcVitals) {
  const rows = [
    <Gauge key="cpu" label="CPU" percent={pc.cpu_percent} value={`${Math.round(pc.cpu_percent)}%`} />,
    <Gauge key="mem" label="Memory" percent={pc.memory.used_percent}
      value={`${Math.round(pc.memory.used_percent)}% · ${pc.memory.used_gb}/${pc.memory.total_gb} GB`} />,
  ];
  if (pc.gpu) {
    rows.push(<Gauge key="gpu" label="Graphics" percent={pc.gpu.percent}
      value={`${Math.round(pc.gpu.percent)}% · ${Math.round(pc.gpu.temperature_c)}°C`} />);
  }
  if (pc.battery) {
    const b = pc.battery;
    const low = !b.charging && b.percent <= 20;
    rows.push(<Gauge key="bat" label="Battery" percent={b.percent} color={low ? HUD.bad : HUD.cyan}
      value={`${b.percent}%${b.charging ? " · charging" : b.minutes_left ? ` · ${b.minutes_left} min` : ""}`} />);
  }
  const disk = pc.disks[0];
  if (disk) {
    rows.push(<Gauge key="disk" label={`Drive ${disk.drive.replace(/[\\:]+$/, "")}`} percent={disk.used_percent}
      value={`${disk.free_gb} GB free`} color={level(disk.used_percent, 85, 95)} />);
  }
  return rows;
}

export function PcHud({ hud, connected }: { hud: HudSnapshot | null; connected: boolean }) {
  const pc = hud?.pc ?? null;
  const live = Boolean(pc && connected);
  const weather = pc?.weather;
  const upcoming = (hud?.events ?? []).filter((e) => !e.past).slice(0, 3);
  const busiest = pc?.top_apps?.[0];
  return (
    <View style={s.screen}>
      <View style={s.head}>
        <Reactor live={live} />
        <View style={{ flex: 1, gap: 2 }}>
          <Text style={s.title}>CUSTODIAN</Text>
          <Text style={s.soft}>
            {live && pc ? `${pc.device ?? "Your PC"} · online · updated ${ago(pc.age_seconds)}`
              : connected ? "Your PC is connected; its readings arrive within half a minute"
              : "Your PC is offline - start Custodian with the Custodian icon on its desktop"}
          </Text>
          {weather ? (
            <Text style={s.ink}>
              {weather.city} · {weather.temperature != null ? `${Math.round(weather.temperature)}°` : "-"} · {weather.condition}
              {weather.high != null && weather.low != null ? ` · ${Math.round(weather.high)}°/${Math.round(weather.low)}°` : ""}
              {weather.rain_chance ? ` · rain ${weather.rain_chance}%` : ""}
            </Text>
          ) : null}
        </View>
      </View>
      {pc ? <View style={s.panel}>{vitals(pc)}</View> : null}
      {busiest && busiest.cpu_percent >= 10 ? (
        <Text style={s.soft}>Busiest: {busiest.app} · {Math.round(busiest.cpu_percent)}% CPU · {busiest.memory_mb} MB</Text>
      ) : null}
      <View style={s.panel}>
        <Text style={s.section}>TODAY</Text>
        {upcoming.length ? upcoming.map((e, i) => (
          <Text key={`${e.time}-${i}`} style={s.ink}><Text style={s.time}>{e.time}</Text>  {e.title}</Text>
        )) : <Text style={s.soft}>Nothing more on the calendar today.</Text>}
        {(hud?.reminders ?? []).slice(0, 3).map((r, i) => (
          <Text key={`r-${i}`} style={s.ink}><Text style={s.time}>⏰</Text>  {r.text}</Text>
        ))}
        {hud?.activity?.length ? <Text style={s.soft}>Last: {hud.activity[0].label}</Text> : null}
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  screen: { backgroundColor: HUD.bg, borderRadius: 18, padding: 16, gap: 12, borderWidth: 1, borderColor: HUD.line },
  head: { flexDirection: "row", alignItems: "center", gap: 14 },
  reactor: { width: 68, height: 68, alignItems: "center", justifyContent: "center" },
  ring: { position: "absolute", borderRadius: 999, borderWidth: 2, borderColor: HUD.cyan },
  core: { width: 26, height: 26, borderRadius: 13, backgroundColor: HUD.cyan, shadowColor: HUD.cyan, shadowOpacity: 0.9,
          shadowRadius: 12, shadowOffset: { width: 0, height: 0 } },
  title: { color: HUD.cyan, fontSize: 16, fontWeight: "700", letterSpacing: 3 },
  ink: { color: HUD.ink, fontSize: 14 },
  soft: { color: HUD.soft, fontSize: 12 },
  time: { color: HUD.cyan, fontWeight: "700" },
  section: { color: HUD.cyan, fontSize: 11, fontWeight: "700", letterSpacing: 2 },
  panel: { backgroundColor: HUD.panel, borderRadius: 12, padding: 12, gap: 8 },
  gauge: { flexDirection: "row", alignItems: "center", gap: 10 },
  gaugeLabel: { color: HUD.soft, fontSize: 12, width: 66 },
  track: { flex: 1, height: 6, borderRadius: 3, backgroundColor: HUD.line, overflow: "hidden" },
  fill: { height: 6, borderRadius: 3 },
  gaugeValue: { color: HUD.ink, fontSize: 12, minWidth: 96, textAlign: "right" },
});
