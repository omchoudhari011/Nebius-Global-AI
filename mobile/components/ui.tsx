// Futuristic UI Components with vibrant colour gradients, luminous cybernetic plates,
// tactile extruded cards, and precision LED indicators.

import Ionicons from "@expo/vector-icons/Ionicons";
import type { ComponentProps, ReactNode } from "react";
import { LinearGradient } from "expo-linear-gradient";
import { ActivityIndicator, Platform, Pressable, StyleSheet, Switch, View, type TextInputProps } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { fonts, neuPressed, neuSunken, radius, softShadow, space, themed, useTokens, type Tokens } from "../lib/tokens";
import { Orb, type OrbMood } from "./Orb";
import { Text, TextInput } from "./Typography";
import type { TaskRunStatus, ToolStatus } from "../lib/types";

export type IconName = ComponentProps<typeof Ionicons>["name"];
export type Tone = "neutral" | "accent" | "success" | "danger" | "warning";

export function toneColors(t: Tokens, tone: Tone): { fg: string; bg: string } {
  switch (tone) {
    case "accent":
      return { fg: t.accentText, bg: t.accentSoft };
    case "success":
      return { fg: t.success, bg: t.successBg };
    case "danger":
      return { fg: t.danger, bg: t.dangerBg };
    case "warning":
      return { fg: t.warning, bg: t.warningBg };
    default:
      return { fg: t.inkSoft, bg: t.neutralBg };
  }
}

export function statusTone(status: ToolStatus | TaskRunStatus | string): Tone {
  if (status === "succeeded") return "success";
  if (status === "denied" || status === "rejected") return "warning";
  if (status === "running") return "accent";
  if (status === "failed" || status === "timed_out") return "danger";
  return "neutral";
}

const useButtonStyles = themed((t) => ({
  base: {
    minHeight: 48,
    paddingHorizontal: space.xl,
    borderRadius: radius.pill,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: space.sm,
    overflow: "hidden",
  },
  compact: { minHeight: 38, paddingHorizontal: space.lg },
  primary: {
    ...softShadow(t, 2),
  },
  secondary: {
    ...softShadow(t, 1),
  },
  danger: {
    ...softShadow(t, 1),
  },
  ghost: {
    backgroundColor: "transparent",
  },
  label: { fontSize: 15, fontWeight: "800", letterSpacing: 0.4 },
  labelCompact: { fontSize: 13 },
  disabled: { opacity: 0.45 },
}));

type ButtonProps = {
  label: string;
  onPress: () => void;
  variant?: "primary" | "secondary" | "danger" | "ghost";
  icon?: IconName;
  loading?: boolean;
  disabled?: boolean;
  compact?: boolean;
  accessibilityLabel?: string;
};

export function Button({ label, onPress, variant = "primary", icon, loading, disabled, compact, accessibilityLabel }: ButtonProps) {
  const s = useButtonStyles();
  const t = useTokens();
  const isPrimary = variant === "primary";
  const isSecondary = variant === "secondary";
  const isDanger = variant === "danger";
  const color = isPrimary ? t.onAccent : isDanger ? t.danger : variant === "ghost" ? t.accentText : t.ink;
  const inactive = disabled || loading;

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? label}
      accessibilityState={{ disabled: !!inactive, busy: !!loading }}
      disabled={inactive}
      onPress={onPress}
      style={({ pressed }) => [
        s.base,
        compact && s.compact,
        isPrimary ? s.primary : isSecondary ? s.secondary : isDanger ? s.danger : s.ghost,
        pressed && !inactive && (variant === "ghost" ? { opacity: 0.7 } : { transform: [{ scale: 0.97 }] }),
        inactive && s.disabled,
      ]}
    >
      {isPrimary ? (
        <LinearGradient
          colors={t.gradients.primary}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 0 }}
          style={StyleSheet.absoluteFill}
        />
      ) : isSecondary ? (
        <LinearGradient
          colors={t.gradients.cardElevated}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={StyleSheet.absoluteFill}
        />
      ) : isDanger ? (
        <LinearGradient
          colors={["rgba(255, 94, 94, 0.24)", "rgba(180, 30, 30, 0.16)"]}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={StyleSheet.absoluteFill}
        />
      ) : null}
      {loading ? (
        <ActivityIndicator size="small" color={color} />
      ) : icon ? (
        <Ionicons name={icon} size={compact ? 15 : 18} color={color} />
      ) : null}
      <Text style={[s.label, compact && s.labelCompact, { color }]}>{label}</Text>
    </Pressable>
  );
}

/** A Switch with a recessed track and extruded tactile thumb. */
export function ToggleSwitch({
  value,
  onValueChange,
  disabled,
  accessibilityLabel,
}: {
  value: boolean;
  onValueChange: (value: boolean) => void;
  disabled?: boolean;
  accessibilityLabel: string;
}) {
  const t = useTokens();
  const webOnly = Platform.OS === "web" ? ({ activeThumbColor: "#ffffff", activeTrackColor: t.accent } as object) : {};
  return (
    <Switch
      accessibilityLabel={accessibilityLabel}
      value={value}
      disabled={disabled}
      onValueChange={onValueChange}
      trackColor={{ false: t.surfaceSunken, true: t.accent }}
      thumbColor={value ? (t.scheme === "dark" ? "#05110E" : "#ffffff") : t.inkSoft}
      ios_backgroundColor={t.surfaceSunken}
      {...webOnly}
    />
  );
}

/** Tactile Icon Button with cybernetic gradient background. */
export function IconButton({ icon, onPress, label, color }: { icon: IconName; onPress: () => void; label: string; color?: string }) {
  const t = useTokens();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      hitSlop={8}
      onPress={onPress}
      style={({ pressed }) => [
        {
          width: 42,
          height: 42,
          borderRadius: radius.pill,
          alignItems: "center",
          justifyContent: "center",
          overflow: "hidden",
          ...softShadow(t, 1),
        },
        pressed && neuPressed(t),
      ]}
    >
      <LinearGradient
        colors={t.gradients.cardElevated}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={StyleSheet.absoluteFill}
      />
      <Ionicons name={icon} size={20} color={color ?? t.inkSoft} />
    </Pressable>
  );
}

export function Chip({ label, tone = "neutral", icon }: { label: string; tone?: Tone; icon?: IconName }) {
  const t = useTokens();
  const { fg } = toneColors(t, tone);
  return (
    <LinearGradient
      colors={["rgba(20, 52, 45, 0.95)", "rgba(10, 26, 23, 0.98)"]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={{
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        borderRadius: radius.pill,
        paddingHorizontal: 13,
        paddingVertical: 7,
        alignSelf: "flex-start",
        overflow: "hidden",
        ...softShadow(t, 1),
      }}
    >
      <View
        style={{
          width: 6,
          height: 6,
          borderRadius: 3,
          backgroundColor: fg,
        }}
      />
      {icon ? <Ionicons name={icon} size={12} color={fg} /> : null}
      <Text style={{ color: fg, fontSize: 12, fontWeight: "700", letterSpacing: 0.2 }}>{label}</Text>
    </LinearGradient>
  );
}

const useCardStyles = themed((t) => ({
  card: {
    borderRadius: radius.xl,
    padding: space.xl,
    gap: space.md,
    overflow: "hidden",
    ...softShadow(t, 2),
  },
  title: { fontSize: 18, fontWeight: "800", color: t.ink, letterSpacing: 0.2 },
  sectionHint: { fontSize: 14, lineHeight: 20, color: t.inkSoft, marginTop: -space.xs },
}));

export function Card({ children }: { children: ReactNode }) {
  const s = useCardStyles();
  const t = useTokens();
  return (
    <LinearGradient
      colors={t.gradients.card}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={s.card}
    >
      {children}
    </LinearGradient>
  );
}

export function SectionTitle({ title, hint, right }: { title: string; hint?: string; right?: ReactNode }) {
  const s = useCardStyles();
  return (
    <View style={{ gap: space.sm }}>
      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
        <Text accessibilityRole="header" style={s.title}>
          {title}
        </Text>
        {right}
      </View>
      {hint ? <Text style={s.sectionHint}>{hint}</Text> : null}
    </View>
  );
}

const useHeaderStyles = themed((t) => ({
  wrap: { backgroundColor: "transparent", paddingHorizontal: space.lg, paddingBottom: space.md },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: space.md, minHeight: 48 },
  title: { fontFamily: fonts.display, fontSize: 26, lineHeight: 32, color: t.ink, letterSpacing: 0.6 },
  subtitle: { fontSize: 14, color: t.inkSoft, marginTop: 2 },
}));

/** Atmospheric Multi-Stop Background Screen Gradient (Luminous Top Aurora Flare from Reference Image 2) */
export function ScreenGradient() {
  const t = useTokens();
  const isDark = t.scheme === "dark";

  if (!isDark) {
    return (
      <LinearGradient
        colors={t.gradients.screen}
        start={{ x: 0, y: 0 }}
        end={{ x: 0.8, y: 1 }}
        style={StyleSheet.absoluteFill}
      />
    );
  }

  return (
    <View style={[StyleSheet.absoluteFill, { backgroundColor: "#06110F", overflow: "hidden" }]} pointerEvents="none">
      {/* Deep Obsidian-Emerald Base Canvas */}
      <LinearGradient
        colors={["#081714", "#06110F", "#030A09"]}
        start={{ x: 0, y: 0 }}
        end={{ x: 0, y: 1 }}
        style={StyleSheet.absoluteFill}
      />

      {/* Luminous Top Atmospheric Aurora Flare (Replicating Reference Image 2) */}
      <LinearGradient
        colors={[
          "rgba(0, 229, 188, 0.48)", // Electric Turquoise Core
          "rgba(201, 231, 80, 0.38)", // Luminous Lime Radiance
          "rgba(76, 217, 100, 0.18)", // Vitality Green mid-falloff
          "rgba(23, 132, 75, 0.07)",  // Deep Emerald mist
          "rgba(6, 17, 15, 0)",       // Dissolves into deep obsidian
        ]}
        locations={[0, 0.18, 0.38, 0.62, 1]}
        start={{ x: 0.5, y: 0 }}
        end={{ x: 0.5, y: 1 }}
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: "56%",
        }}
      />

      {/* Kinetic Asymmetric Light Beams (Turquoise sweep on left, Lime bloom on right) */}
      <LinearGradient
        colors={["rgba(0, 229, 188, 0.32)", "rgba(23, 132, 75, 0.06)", "transparent"]}
        start={{ x: 0.2, y: 0 }}
        end={{ x: 0.0, y: 0.7 }}
        style={{
          position: "absolute",
          top: -30,
          left: "-15%",
          width: "75%",
          height: "48%",
          borderRadius: 999,
          transform: [{ rotate: "-15deg" }],
        }}
      />
      <LinearGradient
        colors={["rgba(201, 231, 80, 0.28)", "rgba(76, 217, 100, 0.06)", "transparent"]}
        start={{ x: 0.8, y: 0 }}
        end={{ x: 1.0, y: 0.7 }}
        style={{
          position: "absolute",
          top: -30,
          right: "-15%",
          width: "75%",
          height: "48%",
          borderRadius: 999,
          transform: [{ rotate: "15deg" }],
        }}
      />

      {/* Radiant Radial Light Burst at Top Center */}
      <View
        style={{
          position: "absolute",
          top: -100,
          left: "15%",
          right: "15%",
          height: 240,
          borderRadius: 999,
          backgroundColor: "rgba(0, 229, 188, 0.30)",
          ...(Platform.OS === "web" ? ({ filter: "blur(75px)" } as object) : { opacity: 0.45 }),
        }}
      />
    </View>
  );
}

/** A screen's title, with Custodian's orb beside it when `orb` is set. */
export function ScreenHeader({ title, subtitle, right, orb }: { title: string; subtitle?: ReactNode; right?: ReactNode; orb?: OrbMood }) {
  const s = useHeaderStyles();
  const insets = useSafeAreaInsets();
  return (
    <View style={[s.wrap, { paddingTop: insets.top + space.md }]}>
      <View style={s.row}>
        <View style={{ flexShrink: 1, flexDirection: "row", alignItems: "center", gap: space.md }}>
          {orb ? <Orb size={40} mood={orb} /> : null}
          <View style={{ flexShrink: 1 }}>
            <Text accessibilityRole="header" style={s.title} numberOfLines={1}>
              {title}
            </Text>
            {typeof subtitle === "string" ? <Text style={s.subtitle}>{subtitle}</Text> : subtitle}
          </View>
        </View>
        {right ? <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm }}>{right}</View> : null}
      </View>
    </View>
  );
}

export function Notice({
  message,
  tone = "danger",
  actionLabel,
  onAction,
}: {
  message: string;
  tone?: Tone;
  actionLabel?: string;
  onAction?: () => void;
}) {
  const t = useTokens();
  const { fg } = toneColors(t, tone);
  const colors: [string, string] = tone === "danger"
    ? ["rgba(255, 94, 94, 0.24)", "rgba(140, 20, 20, 0.12)"]
    : ["rgba(0, 229, 188, 0.22)", "rgba(16, 140, 110, 0.10)"];
  return (
    <LinearGradient
      colors={colors}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 0 }}
      accessibilityRole="alert"
      style={{
        borderRadius: radius.lg,
        paddingVertical: space.md,
        paddingHorizontal: space.lg,
        flexDirection: "row",
        alignItems: "center",
        gap: space.md,
        overflow: "hidden",
        ...softShadow(t, 1),
      }}
    >
      <Ionicons name={tone === "danger" ? "alert-circle-outline" : "information-circle-outline"} size={18} color={fg} />
      <Text style={{ color: fg, flex: 1, fontSize: 14, lineHeight: 20 }}>{message}</Text>
      {actionLabel && onAction ? (
        <Pressable accessibilityRole="button" onPress={onAction} hitSlop={8}>
          <Text style={{ color: fg, fontWeight: "700" }}>{actionLabel}</Text>
        </Pressable>
      ) : null}
    </LinearGradient>
  );
}

const useFieldStyles = themed((t) => ({
  label: { fontSize: 13, fontWeight: "700", color: t.ink, marginBottom: 8, letterSpacing: 0.3 },
  input: {
    minHeight: 50,
    borderRadius: radius.md,
    paddingHorizontal: space.lg,
    paddingVertical: 12,
    color: t.ink,
    fontSize: 16,
    outlineWidth: 0,
    ...neuSunken(t),
  },
}));

export function Field({ label, ...props }: TextInputProps & { label?: string }) {
  const s = useFieldStyles();
  const t = useTokens();
  return (
    <View>
      {label ? <Text style={s.label}>{label}</Text> : null}
      <TextInput placeholderTextColor={t.inkSoft} accessibilityLabel={label} {...props} style={[s.input, props.style]} />
    </View>
  );
}

/** Segmented Control with glowing gradient active selection pill. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
}) {
  const t = useTokens();
  return (
    <View
      accessibilityRole="radiogroup"
      style={[
        {
          flexDirection: "row",
          flexWrap: "wrap",
          gap: 6,
          padding: 6,
          borderRadius: radius.pill,
          alignSelf: "flex-start",
        },
        neuSunken(t),
      ]}
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <Pressable
            key={option.value}
            accessibilityRole="radio"
            accessibilityState={{ selected }}
            onPress={() => onChange(option.value)}
            style={({ pressed }) => [
              {
                paddingHorizontal: space.lg,
                minHeight: 38,
                justifyContent: "center",
                borderRadius: radius.pill,
                overflow: "hidden",
              },
              pressed && !selected && { opacity: 0.8 },
            ]}
          >
            {selected ? (
              <LinearGradient
                colors={t.gradients.primary}
                start={{ x: 0, y: 0 }}
                end={{ x: 1, y: 0 }}
                style={StyleSheet.absoluteFill}
              />
            ) : null}
            <Text
              style={{
                color: selected ? t.onAccent : t.inkSoft,
                fontSize: 14,
                fontWeight: selected ? "800" : "600",
                letterSpacing: 0.3,
              }}
            >
              {option.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}
