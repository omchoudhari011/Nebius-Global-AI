// The Jarvis HUD, in the app: the PC's vital signs, weather, and day schedule.
// Enhanced with futuristic gradient gauges, cybernetic display bezels, and arc reactor core.

import { useEffect, useRef, useState } from "react";
import { AccessibilityInfo, Animated, Easing, Platform, StyleSheet, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";

import type { HudSnapshot, PcVitals } from "../lib/types";
import { Text } from "./Typography";

const HUD = {
  bg: "#0B1816",
  panel: "rgba(18, 46, 40, 0.88)",
  line: "rgba(0, 229, 188, 0.25)",
  lineStrong: "rgba(0, 229, 188, 0.45)",
  lime: "#C9E750",
  turquoise: "#00E5BC",
  emerald: "#00E5BC",
  bioGreen: "#4CD964",
  ink: "#E8FAF4",
  soft: "#7FA89E",
  warn: "#E5B83B",
  bad: "#FF5E5E",
};

function levelColors(percent: number, warnAt = 80, badAt = 92): [string, string] {
  return percent >= badAt
    ? ["#FF5E5E", "#C02020"]
    : percent >= warnAt
      ? ["#E5B83B", "#FF8F3B"]
      : ["#00E5BC", "#4CD964"];
}

function Gauge({ label, percent, value, color }: { label: string; percent: number; value: string; color?: string }) {
  const shown = Math.max(0, Math.min(100, percent));
  const grad = levelColors(shown);
  return (
    <View style={s.gauge} accessible accessibilityLabel={`${label}: ${value}`}>
      <Text style={s.gaugeLabel}>{label}</Text>
      <View style={s.track}>
        <LinearGradient
          colors={color ? [color, color] : grad}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 0 }}
          style={[s.fill, { width: `${shown}%` }]}
        />
      </View>
      <Text style={s.gaugeValue}>{value}</Text>
    </View>
  );
}

function Reactor({ live }: { live: boolean }) {
  const pulse = useRef(new Animated.Value(0)).current;
  const rotate = useRef(new Animated.Value(0)).current;
  const [reduceMotion, setReduceMotion] = useState(false);
  useEffect(() => {
    AccessibilityInfo.isReduceMotionEnabled().then(setReduceMotion).catch(() => undefined);
    const sub = AccessibilityInfo.addEventListener("reduceMotionChanged", setReduceMotion);
    return () => sub.remove();
  }, []);
  useEffect(() => {
    if (!live || reduceMotion) {
      pulse.setValue(0);
      rotate.setValue(0);
      return;
    }
    const native = Platform.OS !== "web";
    const loop = Animated.loop(Animated.sequence([
      Animated.timing(pulse, { toValue: 1, duration: 1600, easing: Easing.inOut(Easing.sin), useNativeDriver: native }),
      Animated.timing(pulse, { toValue: 0, duration: 1600, easing: Easing.inOut(Easing.sin), useNativeDriver: native }),
    ]));
    const spinLoop = Animated.loop(
      Animated.timing(rotate, { toValue: 1, duration: 8000, easing: Easing.linear, useNativeDriver: native })
    );
    loop.start();
    spinLoop.start();
    return () => {
      loop.stop();
      spinLoop.stop();
    };
  }, [live, reduceMotion, pulse, rotate]);

  const glow = pulse.interpolate({ inputRange: [0, 1], outputRange: [0.5, 1] });
  const scale = pulse.interpolate({ inputRange: [0, 1], outputRange: [0.92, 1.08] });
  const spin = rotate.interpolate({ inputRange: [0, 1], outputRange: ["0deg", "360deg"] });

  return (
    <View style={s.reactor} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
      <View style={[s.ring, { width: 68, height: 68, opacity: live ? 0.35 : 0.15 }]} />
      <Animated.View style={[s.ring, { width: 52, height: 52, borderStyle: "dashed", borderColor: HUD.lime, opacity: live ? 0.75 : 0.25, transform: [{ rotate: spin }] }]} />
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
    rows.push(<Gauge key="bat" label="Battery" percent={b.percent} color={low ? HUD.bad : undefined}
      value={`${b.percent}%${b.charging ? " · charging" : b.minutes_left ? ` · ${b.minutes_left} min` : ""}`} />);
  }
  const disk = pc.disks[0];
  if (disk) {
    rows.push(<Gauge key="disk" label={`Drive ${disk.drive.replace(/[\\:]+$/, "")}`} percent={disk.used_percent}
      value={`${disk.free_gb} GB free`} />);
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
    <LinearGradient
      colors={["#081716", "#0E2925"]}
      start={{ x: 0, y: 0 }}
      end={{ x: 0.9, y: 1 }}
      style={s.screen}
    >
      <View style={s.head}>
        <Reactor live={live} />
        <View style={{ flex: 1, gap: 2 }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <Text style={s.title}>J.A.R.V.I.S.</Text>
            <View style={[s.liveDot, { backgroundColor: live ? HUD.bioGreen : HUD.soft }]} />
          </View>
          <Text style={s.soft}>
            {live && pc ? `${pc.device ?? "Your PC"} · online · updated ${ago(pc.age_seconds)}`
              : connected ? "Your PC is connected; its readings arrive within half a minute"
              : "Your PC is offline - start Jarvis with the Jarvis icon on its desktop"}
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
      {pc ? (
        <LinearGradient
          colors={["rgba(18, 46, 40, 0.92)", "rgba(10, 26, 23, 0.96)"]}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={s.panel}
        >
          {vitals(pc)}
        </LinearGradient>
      ) : null}
      {busiest && busiest.cpu_percent >= 10 ? (
        <Text style={s.soft}>Busiest: {busiest.app} · {Math.round(busiest.cpu_percent)}% CPU · {busiest.memory_mb} MB</Text>
      ) : null}
      <LinearGradient
        colors={["rgba(18, 46, 40, 0.92)", "rgba(10, 26, 23, 0.96)"]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={s.panel}
      >
        <Text style={s.section}>TODAY</Text>
        {upcoming.length ? upcoming.map((e, i) => (
          <Text key={`${e.time}-${i}`} style={s.ink}><Text style={s.time}>{e.time}</Text>  {e.title}</Text>
        )) : <Text style={s.soft}>Nothing more on the calendar today.</Text>}
        {(hud?.reminders ?? []).slice(0, 3).map((r, i) => (
          <Text key={`r-${i}`} style={s.ink}><Text style={s.time}>⏰</Text>  {r.text}</Text>
        ))}
        {hud?.activity?.length ? <Text style={s.soft}>Last: {hud.activity[0].label}</Text> : null}
      </LinearGradient>
    </LinearGradient>
  );
}

const s = StyleSheet.create({
  screen: {
    borderRadius: 24,
    padding: 18,
    gap: 14,
    shadowColor: "#000000",
    shadowOpacity: 0.6,
    shadowRadius: 20,
    shadowOffset: { width: 0, height: 8 },
    elevation: 8,
    overflow: "hidden",
  },
  head: { flexDirection: "row", alignItems: "center", gap: 14 },
  reactor: { width: 68, height: 68, alignItems: "center", justifyContent: "center" },
  ring: { position: "absolute", borderRadius: 999, borderWidth: 1.5, borderColor: HUD.turquoise },
  core: {
    width: 26,
    height: 26,
    borderRadius: 13,
    backgroundColor: HUD.turquoise,
    shadowColor: HUD.turquoise,
    shadowOpacity: 0.95,
    shadowRadius: 14,
    shadowOffset: { width: 0, height: 0 },
  },
  liveDot: { width: 6, height: 6, borderRadius: 3 },
  title: { color: HUD.lime, fontSize: 16, fontWeight: "800", letterSpacing: 4 },
  ink: { color: HUD.ink, fontSize: 14 },
  soft: { color: HUD.soft, fontSize: 12 },
  time: { color: HUD.turquoise, fontWeight: "700" },
  section: { color: HUD.lime, fontSize: 11, fontWeight: "800", letterSpacing: 2 },
  panel: {
    borderRadius: 16,
    padding: 14,
    gap: 10,
    overflow: "hidden",
  },
  gauge: { flexDirection: "row", alignItems: "center", gap: 10 },
  gaugeLabel: { color: HUD.soft, fontSize: 12, width: 66, fontWeight: "600" },
  track: { flex: 1, height: 8, borderRadius: 4, backgroundColor: "rgba(0, 0, 0, 0.55)", overflow: "hidden" },
  fill: { height: 8, borderRadius: 4 },
  gaugeValue: { color: HUD.ink, fontSize: 12, minWidth: 96, textAlign: "right", fontVariant: ["tabular-nums"] },
});
