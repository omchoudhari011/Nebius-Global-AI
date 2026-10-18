// Colour tokens for light and dark mode, plus a hook that picks the right set and a helper
// for theme-aware StyleSheets. Text colours meet WCAG AA (4.5:1) on the surfaces they're
// used on; `onAccent` is the text colour for accent-filled buttons.
//
// The look: a warm companion. Light is cream and white with coral and peach; dark is warm
// charcoal with amber. Everything is set in Nunito, a rounded, friendly typeface.

import { useMemo } from "react";
import { StyleSheet, useColorScheme } from "react-native";

export type Tokens = {
  scheme: "light" | "dark";
  bg: string;
  surface: string;
  surface2: string;
  ink: string;
  inkSoft: string;
  accent: string;
  onAccent: string;
  accentText: string; // accent-coloured text and active tab labels
  accent2: string; // a calm second colour: links, spinners, "online"
  accentSoft: string; // a quiet accent wash behind chips, selections and the active tab
  glow: string; // the orb's warm light (decoration, never text)
  hairline: string; // a soft border for cards and fields
  bgTop: string; // the screen gradient, top to bottom
  bgBottom: string;
  bubbleTop: string; // your messages: a soft peach (light) or amber (dark) gradient
  bubbleBottom: string;
  onBubble: string;
  shadow: string; // a warm shadow colour, so depth doesn't look grey
  border: string;
  danger: string;
  dangerBg: string;
  success: string;
  successBg: string;
  warning: string;
  warningBg: string;
  neutralBg: string;
};

export const light: Tokens = {
  scheme: "light",
  bg: "#FBF6EF",
  surface: "#FFFFFF",
  surface2: "#F6EEE5",
  ink: "#2B2320",
  inkSoft: "#6B5E55",
  accent: "#C4502F",
  onAccent: "#FFFFFF",
  accentText: "#B2462A",
  accent2: "#2F7666",
  accentSoft: "#FCE6DA",
  glow: "#F2A65A",
  hairline: "#F0E4D7",
  bgTop: "#FDF9F4",
  bgBottom: "#F8ECDF",
  bubbleTop: "#FFE3D2",
  bubbleBottom: "#FFD3BA",
  onBubble: "#2B2320",
  shadow: "#7A4A2A",
  border: "#E6D8C8",
  danger: "#AE3923",
  dangerBg: "#FBE4DD",
  success: "#2B7356",
  successBg: "#E2F1E9",
  warning: "#865700",
  warningBg: "#FAEFD7",
  neutralBg: "#F2E9DF",
};

export const dark: Tokens = {
  scheme: "dark",
  bg: "#1A1512",
  surface: "#26201C",
  surface2: "#302823",
  ink: "#F7EEE6",
  inkSoft: "#C3B5A9",
  accent: "#F2A65A",
  onAccent: "#2A1708",
  accentText: "#F6B97A",
  accent2: "#8FD1BE",
  accentSoft: "#3A2C22",
  glow: "#F2A65A",
  hairline: "#3A312B",
  bgTop: "#221B17",
  bgBottom: "#14100E",
  bubbleTop: "#F6B97A",
  bubbleBottom: "#EE9B52",
  onBubble: "#2A1708",
  shadow: "#000000",
  border: "#463B34",
  danger: "#FF917C",
  dangerBg: "#3D211C",
  success: "#7FD8B2",
  successBg: "#17312A",
  warning: "#F3C76A",
  warningBg: "#3A2E14",
  neutralBg: "#352C26",
};

// Kept for code that only needs the light palette (and backwards compatibility).
export const tokens = light;

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
  return useColorScheme() === "dark" ? dark : light;
}

/** themed((t) => ({ box: { backgroundColor: t.surface } })) returns a hook giving memoized styles. */
export function themed<T extends StyleSheet.NamedStyles<T>>(factory: (t: Tokens) => T): () => T {
  return function useThemedStyles(): T {
    const t = useTokens();
    return useMemo(() => StyleSheet.create(factory(t)), [t]);
  };
}

/** A soft, warm drop shadow (iOS/web; Android uses elevation). */
export function softShadow(t: Tokens, depth: 1 | 2 = 1) {
  return {
    shadowColor: t.shadow,
    shadowOpacity: t.scheme === "dark" ? 0.35 : depth === 1 ? 0.07 : 0.12,
    shadowRadius: depth === 1 ? 16 : 28,
    shadowOffset: { width: 0, height: depth === 1 ? 4 : 10 },
    elevation: depth === 1 ? 2 : 5,
  } as const;
}

export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 } as const;
export const radius = { sm: 10, md: 14, lg: 22, xl: 28, pill: 999 } as const;
