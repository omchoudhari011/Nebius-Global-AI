// Futuristic Bioluminescent Design System tokens with rich multi-stop colour gradients.
// Features luminous cybernetic gradients, tactile extruded surfaces, and precision LED indicators.

import { useMemo } from "react";
import { Platform, StyleSheet, useColorScheme } from "react-native";

export type Gradients = {
  screen: [string, string, ...string[]];
  primary: [string, string, ...string[]];
  primaryGlow: [string, string];
  card: [string, string];
  cardElevated: [string, string];
  userBubble: [string, string];
  assistantBubble: [string, string];
  tabActive: [string, string];
  hudBezel: [string, string];
  gaugeCyan: [string, string];
  gaugeLime: [string, string];
  gaugeAmber: [string, string];
  gaugeRed: [string, string];
};

export type Tokens = {
  scheme: "light" | "dark";
  bg: string;
  surface: string;
  surface2: string;
  surfaceSunken: string; // Recessed/carved wells (inputs, slider grooves)
  surfacePressed: string; // Depressed button state
  ink: string;
  inkSoft: string;
  accent: string;
  onAccent: string;
  accentText: string;
  accent2: string;
  accentSoft: string;
  glow: string;
  hairline: string;
  bgTop: string;
  bgBottom: string;
  bubbleTop: string;
  bubbleBottom: string;
  onBubble: string;
  shadow: string;
  shadowDark: string;
  shadowLight: string;
  border: string;
  borderTop: string; // Top-left highlight border
  borderBottom: string; // Bottom-right shadow border
  danger: string;
  dangerBg: string;
  success: string;
  successBg: string;
  warning: string;
  warningBg: string;
  neutralBg: string;
  // Kinetic Theme Specials
  lime: string;
  obsidian: string;
  emerald: string;
  turquoise: string;
  bioGreen: string;
  glassSurface: string;
  glassBorder: string;
  // Rich Futuristic Gradients
  gradients: Gradients;
};

export const dark: Tokens = {
  scheme: "dark",
  bg: "#0B1816", // Deep obsidian-emerald canvas
  surface: "rgba(18, 44, 39, 0.88)", // Volumetric emerald-obsidian
  surface2: "#14332D", // Slightly elevated cyber plate
  surfaceSunken: "#071312", // Deep carved inset well
  surfacePressed: "#0F2823", // Pressed tactile state
  ink: "#E8FAF4", // Crystalline white text
  inkSoft: "#7FA89E", // Muted sea-glass teal text
  accent: "#00E5BC", // Electric Turquoise
  onAccent: "#05110E", // Crisp contrast on accent
  accentText: "#00E5BC",
  accent2: "#C9E750", // Primary Luminous Lime
  accentSoft: "rgba(0, 229, 188, 0.16)",
  glow: "#00E5BC",
  hairline: "transparent", // Pure neomorphism: zero wireframe borders
  bgTop: "#122C26",
  bgBottom: "#061110",
  bubbleTop: "#1A5245",
  bubbleBottom: "#0F362E",
  onBubble: "#E8FAF4",
  shadow: "#000000",
  shadowDark: "#000000",
  shadowLight: "rgba(22, 54, 46, 0.45)",
  border: "transparent",
  borderTop: "transparent",
  borderBottom: "transparent",
  danger: "#FF5E5E",
  dangerBg: "rgba(255, 94, 94, 0.16)",
  success: "#4CD964",
  successBg: "rgba(76, 217, 100, 0.16)",
  warning: "#E5B83B",
  warningBg: "rgba(229, 184, 59, 0.16)",
  neutralBg: "#122B26",
  lime: "#C9E750",
  obsidian: "#06110F",
  emerald: "#17844B",
  turquoise: "#00E5BC",
  bioGreen: "#4CD964",
  glassSurface: "rgba(18, 44, 39, 0.88)",
  glassBorder: "transparent",
  gradients: {
    screen: ["#071412", "#0B211C", "#040B0A"],
    primary: ["#00E5BC", "#C9E750"], // Electric Turquoise -> Luminous Lime
    primaryGlow: ["rgba(0, 229, 188, 0.55)", "rgba(201, 231, 80, 0.35)"],
    card: ["rgba(20, 48, 42, 0.95)", "rgba(10, 24, 21, 0.98)"],
    cardElevated: ["rgba(26, 62, 54, 0.95)", "rgba(14, 34, 30, 0.98)"],
    userBubble: ["#164439", "#0E2C25"],
    assistantBubble: ["#122923", "#091715"],
    tabActive: ["rgba(0, 229, 188, 0.32)", "rgba(201, 231, 80, 0.16)"],
    hudBezel: ["#0C2420", "#061311"],
    gaugeCyan: ["#00E5BC", "#4CD964"],
    gaugeLime: ["#C9E750", "#00E5BC"],
    gaugeAmber: ["#E5B83B", "#FF8F3B"],
    gaugeRed: ["#FF5E5E", "#C02020"],
  },
};

export const light: Tokens = {
  scheme: "light",
  bg: "#F2F9F6", // Crystalline mint canvas
  surface: "#FFFFFF",
  surface2: "#E3F4ED",
  surfaceSunken: "#D4ECE1",
  surfacePressed: "#CCE6DA",
  ink: "#0B1C18",
  inkSoft: "#4A6E65",
  accent: "#0D9475",
  onAccent: "#FFFFFF",
  accentText: "#0D9475",
  accent2: "#10B981",
  accentSoft: "rgba(13, 148, 117, 0.14)",
  glow: "#10B981",
  hairline: "rgba(13, 148, 117, 0.20)",
  bgTop: "#FAFEFC",
  bgBottom: "#E5F4ED",
  bubbleTop: "#D6F6E9",
  bubbleBottom: "#BEEEDB",
  onBubble: "#0B1C18",
  shadow: "#A4C6B9",
  shadowDark: "#A4C6B9",
  shadowLight: "#FFFFFF",
  border: "rgba(13, 148, 117, 0.25)",
  borderTop: "#FFFFFF",
  borderBottom: "rgba(13, 148, 117, 0.40)",
  danger: "#D63939",
  dangerBg: "#FDE8E8",
  success: "#0D9475",
  successBg: "#E3F7EE",
  warning: "#9E7406",
  warningBg: "#FEF5DC",
  neutralBg: "#DBEFE6",
  lime: "#709600",
  obsidian: "#F2F9F6",
  emerald: "#0D9475",
  turquoise: "#0D9475",
  bioGreen: "#10B981",
  glassSurface: "rgba(255, 255, 255, 0.92)",
  glassBorder: "rgba(13, 148, 117, 0.20)",
  gradients: {
    screen: ["#F9FEFB", "#E8F7F0", "#D8EFE4"],
    primary: ["#0D9475", "#10B981"],
    primaryGlow: ["rgba(13, 148, 117, 0.4)", "rgba(16, 185, 129, 0.25)"],
    card: ["#FFFFFF", "#EFF8F4"],
    cardElevated: ["#FFFFFF", "#E3F4ED"],
    userBubble: ["#D6F6E9", "#BEEEDB"],
    assistantBubble: ["#FFFFFF", "#F2FAF6"],
    tabActive: ["rgba(13, 148, 117, 0.22)", "rgba(16, 185, 129, 0.12)"],
    hudBezel: ["#E7F4EE", "#D6ECE2"],
    gaugeCyan: ["#0D9475", "#10B981"],
    gaugeLime: ["#709600", "#10B981"],
    gaugeAmber: ["#B8860B", "#D97706"],
    gaugeRed: ["#DC2626", "#991B1B"],
  },
};

// Default export uses dark as the primary signature theme
export const tokens = dark;

/** Font families, loaded in app/_layout.tsx. Text picks its weight in components/Typography.tsx. */
export const fonts = {
  display: "Nunito_800ExtraBold",
  body: "Nunito_400Regular",
  bodyMedium: "Nunito_500Medium",
  bodySemibold: "Nunito_600SemiBold",
  bodyBold: "Nunito_700Bold",
  bodyExtraBold: "Nunito_800ExtraBold",
} as const;

export function useTokens(): Tokens {
  return useColorScheme() === "light" ? light : dark;
}

/** themed((t) => ({ box: { backgroundColor: t.surface } })) returns a hook giving memoized styles. */
export function themed<T extends StyleSheet.NamedStyles<T>>(factory: (t: Tokens) => T): () => T {
  return function useThemedStyles(): T {
    const t = useTokens();
    return useMemo(() => StyleSheet.create(factory(t)), [t]);
  };
}

/** Pure Neomorphic dual shadow for physical tactile depth (zero borders). */
export function softShadow(t: Tokens, depth: 1 | 2 = 1) {
  const isDark = t.scheme === "dark";
  if (Platform.OS === "web") {
    const darkShadow = isDark ? "rgba(0, 0, 0, 0.75)" : "rgba(150, 180, 170, 0.60)";
    const lightShadow = isDark ? "rgba(22, 54, 46, 0.45)" : "rgba(255, 255, 255, 0.90)";
    const offset = depth === 1 ? 6 : 10;
    const blur = depth === 1 ? 16 : 26;
    return {
      boxShadow: `${offset}px ${offset}px ${blur}px ${darkShadow}, -${Math.round(offset * 0.6)}px -${Math.round(offset * 0.6)}px ${blur}px ${lightShadow}`,
    } as const;
  }
  return {
    shadowColor: "#000000",
    shadowOpacity: isDark ? (depth === 1 ? 0.65 : 0.85) : 0.40,
    shadowRadius: depth === 1 ? 12 : 20,
    shadowOffset: { width: 0, height: depth === 1 ? 6 : 10 },
    elevation: depth === 1 ? 5 : 10,
  } as const;
}

/** Pure Neomorphic Extruded Surface with tactile depth */
export function neuExtruded(t: Tokens, depth: 1 | 2 = 1) {
  return {
    backgroundColor: t.surface,
    ...softShadow(t, depth),
  } as const;
}

/** Pure Neomorphic Carved Sunken Well (zero borders) */
export function neuSunken(t: Tokens) {
  const isDark = t.scheme === "dark";
  if (Platform.OS === "web") {
    const darkShadow = isDark ? "rgba(0, 0, 0, 0.85)" : "rgba(130, 160, 150, 0.70)";
    const lightShadow = isDark ? "rgba(20, 50, 42, 0.35)" : "rgba(255, 255, 255, 0.85)";
    return {
      backgroundColor: t.surfaceSunken,
      boxShadow: `inset 3px 3px 8px ${darkShadow}, inset -2px -2px 6px ${lightShadow}`,
    } as const;
  }
  return {
    backgroundColor: t.surfaceSunken,
  } as const;
}

/** Pressed Button State (zero borders) */
export function neuPressed(t: Tokens) {
  const isDark = t.scheme === "dark";
  if (Platform.OS === "web") {
    const darkShadow = isDark ? "rgba(0, 0, 0, 0.90)" : "rgba(130, 160, 150, 0.70)";
    const lightShadow = isDark ? "rgba(20, 50, 42, 0.35)" : "rgba(255, 255, 255, 0.85)";
    return {
      backgroundColor: t.surfacePressed,
      boxShadow: `inset 2px 2px 6px ${darkShadow}, inset -2px -2px 6px ${lightShadow}`,
      transform: [{ scale: 0.98 }],
    } as const;
  }
  return {
    backgroundColor: t.surfacePressed,
    transform: [{ scale: 0.98 }],
  } as const;
}

export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 } as const;
export const radius = { sm: 12, md: 16, lg: 22, xl: 28, pill: 999 } as const;
