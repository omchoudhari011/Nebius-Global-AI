// Small shared building blocks: buttons, chips, cards, headers and notices.

import Ionicons from "@expo/vector-icons/Ionicons";
import type { ComponentProps, ReactNode } from "react";
import { LinearGradient } from "expo-linear-gradient";
import { ActivityIndicator, Platform, Pressable, StyleSheet, Switch, View, type TextInputProps } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { fonts, radius, softShadow, space, themed, useTokens, type Tokens } from "../lib/tokens";
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
    minHeight: 46,
    paddingHorizontal: space.xl,
    borderRadius: radius.pill,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: space.sm,
  },
  compact: { minHeight: 36, paddingHorizontal: space.lg },
  primary: { backgroundColor: t.accent, ...softShadow(t) },
  secondary: { backgroundColor: t.surface, borderWidth: 1, borderColor: t.border },
  danger: { backgroundColor: t.surface, borderWidth: 1, borderColor: t.danger },
  ghost: { backgroundColor: "transparent" },
  label: { fontSize: 15, fontWeight: "700" },
  labelCompact: { fontSize: 14 },
  pressed: { opacity: 0.8, transform: [{ scale: 0.98 }] },
  disabled: { opacity: 0.5 },
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
  const color = variant === "primary" ? t.onAccent : variant === "danger" ? t.danger : variant === "ghost" ? t.accent2 : t.ink;
  const inactive = disabled || loading;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? label}
      accessibilityState={{ disabled: !!inactive, busy: !!loading }}
      disabled={inactive}
      onPress={onPress}
      style={({ pressed }) => [s.base, compact && s.compact, s[variant], pressed && s.pressed, inactive && s.disabled]}
    >
      {loading ? (
        <ActivityIndicator size="small" color={color} />
      ) : icon ? (
        <Ionicons name={icon} size={compact ? 15 : 18} color={color} />
      ) : null}
      <Text style={[s.label, compact && s.labelCompact, { color }]}>{label}</Text>
    </Pressable>
  );
}

/** A Switch in the app's colours on every platform (react-native-web otherwise draws the "on" thumb teal). */
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
      trackColor={{ false: t.scheme === "dark" ? "#5A4D44" : t.border, true: t.accent }}
      thumbColor="#ffffff"
      ios_backgroundColor={t.scheme === "dark" ? "#5A4D44" : t.border}
      {...webOnly}
    />
  );
}

export function IconButton({ icon, onPress, label, color }: { icon: IconName; onPress: () => void; label: string; color?: string }) {
  const t = useTokens();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      hitSlop={8}
      onPress={onPress}
      style={({ pressed }) => ({ padding: 6, borderRadius: radius.sm, opacity: pressed ? 0.6 : 1 })}
    >
      <Ionicons name={icon} size={20} color={color ?? t.inkSoft} />
    </Pressable>
  );
}

export function Chip({ label, tone = "neutral", icon }: { label: string; tone?: Tone; icon?: IconName }) {
  const t = useTokens();
  const { fg, bg } = toneColors(t, tone);
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: bg, borderRadius: radius.pill, paddingHorizontal: 10, paddingVertical: 4, alignSelf: "flex-start" }}>
      {icon ? <Ionicons name={icon} size={12} color={fg} /> : null}
      <Text style={{ color: fg, fontSize: 12, fontWeight: "700" }}>{label}</Text>
    </View>
  );
}

const useCardStyles = themed((t) => ({
  card: {
    backgroundColor: t.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: t.hairline,
    padding: space.lg,
    gap: space.md,
    ...softShadow(t),
  },
  title: { fontSize: 18, fontWeight: "800", color: t.ink },
  sectionHint: { fontSize: 14, lineHeight: 20, color: t.inkSoft, marginTop: -space.xs },
}));

export function Card({ children }: { children: ReactNode }) {
  const s = useCardStyles();
  return <View style={s.card}>{children}</View>;
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
  title: { fontFamily: fonts.display, fontSize: 26, lineHeight: 32, color: t.ink },
  subtitle: { fontSize: 14, color: t.inkSoft, marginTop: 1 },
}));

/** The theme's gradient, filling the screen it's the first child of. The screen itself keeps an
 * opaque background (t.bg): the tab bar keeps other tabs mounted underneath, and a
 * transparent screen would show them through it. */
export function ScreenGradient() {
  const t = useTokens();
  return <LinearGradient colors={[t.bgTop, t.bgBottom]} style={StyleSheet.absoluteFill} />;
}

/** A screen's title, with Custodian's orb beside it when `orb` is set (its mood: breathing
 * slowly, or quickly while a reply is on its way). */
export function ScreenHeader({ title, subtitle, right, orb }: { title: string; subtitle?: ReactNode; right?: ReactNode; orb?: OrbMood }) {
  const s = useHeaderStyles();
  const insets = useSafeAreaInsets();
  return (
    <View style={[s.wrap, { paddingTop: insets.top + space.md }]}>
      <View style={s.row}>
        <View style={{ flexShrink: 1, flexDirection: "row", alignItems: "center", gap: space.md }}>
          {orb ? <Orb size={38} mood={orb} /> : null}
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
  const { fg, bg } = toneColors(t, tone);
  return (
    <View
      accessibilityRole="alert"
      style={{ backgroundColor: bg, borderRadius: radius.md, paddingVertical: space.md, paddingHorizontal: space.lg, flexDirection: "row", alignItems: "center", gap: space.md }}
    >
      <Ionicons name={tone === "danger" ? "alert-circle-outline" : "information-circle-outline"} size={18} color={fg} />
      <Text style={{ color: fg, flex: 1, fontSize: 14, lineHeight: 20 }}>{message}</Text>
      {actionLabel && onAction ? (
        <Pressable accessibilityRole="button" onPress={onAction} hitSlop={8}>
          <Text style={{ color: fg, fontWeight: "700" }}>{actionLabel}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const useFieldStyles = themed((t) => ({
  label: { fontSize: 14, fontWeight: "700", color: t.ink, marginBottom: 6 },
  input: {
    minHeight: 48,
    borderWidth: 1,
    borderColor: t.border,
    borderRadius: radius.md,
    paddingHorizontal: space.lg,
    paddingVertical: 12,
    backgroundColor: t.surface,
    color: t.ink,
    fontSize: 16,
    outlineColor: t.accent,
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
    <View accessibilityRole="radiogroup" style={{ flexDirection: "row", flexWrap: "wrap", gap: space.sm }}>
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <Pressable
            key={option.value}
            accessibilityRole="radio"
            accessibilityState={{ selected }}
            onPress={() => onChange(option.value)}
            style={{
              paddingHorizontal: space.lg,
              minHeight: 38,
              justifyContent: "center",
              borderRadius: radius.pill,
              borderWidth: 1,
              borderColor: selected ? t.accent : t.border,
              backgroundColor: selected ? t.accent : t.surface,
            }}
          >
            <Text style={{ color: selected ? t.onAccent : t.ink, fontSize: 14, fontWeight: "700" }}>{option.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}
