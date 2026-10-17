// Small shared building blocks: buttons, chips, cards, headers and notices.

import Ionicons from "@expo/vector-icons/Ionicons";
import type { ComponentProps, ReactNode } from "react";
import { ActivityIndicator, Platform, Pressable, Switch, View, type TextInputProps } from "react-native";
import { Text, TextInput } from "./Typography";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { LinearGradient } from "expo-linear-gradient";

import { fonts, radius, space, themed, useTokens, type Tokens } from "../lib/tokens";
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
    minHeight: 44,
    paddingHorizontal: space.lg,
    borderRadius: radius.md,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: space.sm,
  },
  compact: { minHeight: 34, paddingHorizontal: space.md },
  primary: { backgroundColor: t.accent, borderWidth: 1, borderColor: t.scheme === "dark" ? "#e6c97f" : t.accent },
  secondary: { backgroundColor: t.surface, borderWidth: 1, borderColor: t.hairline },
  danger: { backgroundColor: t.surface, borderWidth: 1, borderColor: t.danger },
  ghost: { backgroundColor: "transparent" },
  label: { fontSize: 15, fontWeight: "600", letterSpacing: 0.3 },
  labelCompact: { fontSize: 13 },
  pressed: { opacity: 0.75 },
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
      trackColor={{ false: t.scheme === "dark" ? "#4a5163" : t.border, true: t.accent }}
      thumbColor="#ffffff"
      ios_backgroundColor={t.scheme === "dark" ? "#4a5163" : t.border}
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
    <View style={{ flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: bg, borderRadius: radius.pill, paddingHorizontal: 8, paddingVertical: 3, alignSelf: "flex-start" }}>
      {icon ? <Ionicons name={icon} size={12} color={fg} /> : null}
      <Text style={{ color: fg, fontSize: 12, fontWeight: "600" }}>{label}</Text>
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
    shadowColor: "#000",
    shadowOpacity: t.scheme === "dark" ? 0.35 : 0.08,
    shadowRadius: 18,
    shadowOffset: { width: 0, height: 6 },
    elevation: 2,
  },
  title: { fontSize: 12, fontWeight: "700", letterSpacing: 1.8, textTransform: "uppercase", color: t.scheme === "dark" ? t.gold : t.accent2 },
  sectionHint: { fontSize: 13, color: t.inkSoft, marginTop: -space.sm },
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
  wrap: { backgroundColor: "transparent", paddingHorizontal: space.lg, paddingBottom: space.md, gap: space.md },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: space.md, minHeight: 44 },
  title: { fontFamily: fonts.display, fontSize: 32, lineHeight: 36, color: t.ink, letterSpacing: 0.3 },
  subtitle: { fontSize: 13, color: t.inkSoft, marginTop: 2, letterSpacing: 0.2 },
}));

/** A fine gold rule that fades out at both ends. */
export function GoldRule({ style }: { style?: object }) {
  const t = useTokens();
  return (
    <LinearGradient
      colors={["transparent", t.gold, "transparent"]}
      start={{ x: 0, y: 0.5 }}
      end={{ x: 1, y: 0.5 }}
      style={[{ height: 1, opacity: t.scheme === "dark" ? 0.55 : 0.45 }, style]}
    />
  );
}

/** Custodian's monogram: a serif "C" in a gold ring. */
export function Crest({ size = 40 }: { size?: number }) {
  const t = useTokens();
  return (
    <LinearGradient
      colors={t.scheme === "dark" ? ["#f0d68f", "#b8892f"] : ["#c9a24c", "#8a6418"]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={{ width: size, height: size, borderRadius: size / 2, padding: Math.max(1.5, size / 22) }}
    >
      <View style={{ flex: 1, borderRadius: size / 2, backgroundColor: t.surface, alignItems: "center", justifyContent: "center" }}>
        <Text style={{ fontFamily: fonts.displayBold, fontSize: size * 0.56, lineHeight: size * 0.66, color: t.gold, marginTop: -size * 0.04 }}>C</Text>
      </View>
    </LinearGradient>
  );
}

export function ScreenHeader({ title, subtitle, right, crest }: { title: string; subtitle?: ReactNode; right?: ReactNode; crest?: boolean }) {
  const s = useHeaderStyles();
  const insets = useSafeAreaInsets();
  return (
    <View style={[s.wrap, { paddingTop: insets.top + space.md }]}>
      <View style={s.row}>
        <View style={{ flexShrink: 1, flexDirection: "row", alignItems: "center", gap: space.md }}>
          {crest ? <Crest size={42} /> : null}
          <View style={{ flexShrink: 1 }}>
            <Text accessibilityRole="header" style={s.title}>
              {title}
            </Text>
            {typeof subtitle === "string" ? <Text style={s.subtitle}>{subtitle}</Text> : subtitle}
          </View>
        </View>
        {right ? <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm }}>{right}</View> : null}
      </View>
      <GoldRule />
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
      style={{ backgroundColor: bg, borderRadius: radius.md, padding: space.md, flexDirection: "row", alignItems: "center", gap: space.md }}
    >
      <Ionicons name={tone === "danger" ? "alert-circle-outline" : "information-circle-outline"} size={18} color={fg} />
      <Text style={{ color: fg, flex: 1, fontSize: 14 }}>{message}</Text>
      {actionLabel && onAction ? (
        <Pressable accessibilityRole="button" onPress={onAction} hitSlop={8}>
          <Text style={{ color: fg, fontWeight: "700" }}>{actionLabel}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const useFieldStyles = themed((t) => ({
  label: { fontSize: 13, fontWeight: "600", color: t.inkSoft, marginBottom: 6 },
  input: {
    minHeight: 44,
    borderWidth: 1,
    borderColor: t.hairline,
    borderRadius: radius.md,
    paddingHorizontal: space.md,
    paddingVertical: 10,
    backgroundColor: t.surface,
    color: t.ink,
    fontSize: 16,
    outlineColor: t.gold,
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
              paddingHorizontal: space.md,
              minHeight: 34,
              justifyContent: "center",
              borderRadius: radius.pill,
              borderWidth: 1,
              borderColor: selected ? t.accent : t.border,
              backgroundColor: selected ? t.accent : t.surface,
            }}
          >
            <Text style={{ color: selected ? t.onAccent : t.ink, fontSize: 13, fontWeight: "600" }}>{option.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}
