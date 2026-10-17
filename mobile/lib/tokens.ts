// Colour tokens for light and dark mode, plus a hook that picks the right set and a helper
// for theme-aware StyleSheets. Text colours meet WCAG AA (4.5:1) on the surfaces they're
// used on; `onAccent` is the text colour for accent-filled buttons and bubbles.
//
// The look: a private steward's study. Dark is midnight navy with antique gold; light is
// ivory parchment with royal blue ink. Headings are set in Cormorant Garamond, text in Inter.

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
  accent2: string;
  accentSoft: string; // a quiet accent wash behind chips and selections
  gold: string; // ornament: crests, hairlines, the active tab
  hairline: string; // a fine gold-tinted border for cards
  bgTop: string; // the background's gradient, top to bottom
  bgBottom: string;
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
  bg: "#f5efe2",
  surface: "#fffcf5",
  surface2: "#f3ebda",
  ink: "#1a1733",
  inkSoft: "#5a536b",
  accent: "#1f2a63",
  onAccent: "#fbf6ea",
  accentText: "#1f2a63",
  accent2: "#7d5a14",
  accentSoft: "#e6e3f1",
  gold: "#8c6716",
  hairline: "#e2d3ae",
  bgTop: "#f8f3e8",
  bgBottom: "#efe5d0",
  border: "#dccdab",
  danger: "#a8331d",
  dangerBg: "#f8e4dd",
  success: "#0b6e54",
  successBg: "#e0f1e9",
  warning: "#7f5300",
  warningBg: "#f7ecd2",
  neutralBg: "#efe7d6",
};

export const dark: Tokens = {
  scheme: "dark",
  bg: "#0a0f1f",
  surface: "#111a30",
  surface2: "#17223d",
  ink: "#f4ecd8",
  inkSoft: "#b9b09c",
  accent: "#c9a24c",
  onAccent: "#16110a",
  accentText: "#e3c47b",
  accent2: "#9db4ff",
  accentSoft: "#2a2a35",
  gold: "#d4af5a",
  hairline: "#3a3a4a",
  bgTop: "#0d1430",
  bgBottom: "#070b17",
  border: "#2b3658",
  danger: "#ff917c",
  dangerBg: "#3a201c",
  success: "#6fd6ad",
  successBg: "#13302a",
  warning: "#f0c766",
  warningBg: "#352b12",
  neutralBg: "#1d2742",
};

// Kept for code that only needs the light palette (and backwards compatibility).
export const tokens = light;

/** Font families, loaded in app/_layout.tsx. Body text picks its weight in components/Typography.tsx. */
export const fonts = {
  display: "CormorantGaramond_600SemiBold",
  displayBold: "CormorantGaramond_700Bold",
  displayItalic: "CormorantGaramond_500Medium_Italic",
  body: "Inter_400Regular",
  bodyMedium: "Inter_500Medium",
  bodySemibold: "Inter_600SemiBold",
  bodyBold: "Inter_700Bold",
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

export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 } as const;
export const radius = { sm: 8, md: 12, lg: 18, pill: 999 } as const;
