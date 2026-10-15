// Colour tokens for light and dark mode, plus a hook that picks the right set and a helper
// for theme-aware StyleSheets. Text colours meet WCAG AA (4.5:1) on the surfaces they're
// used on; `onAccent` is the text colour for accent-filled buttons and bubbles.

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
  bg: "#eef0f3",
  surface: "#ffffff",
  surface2: "#f6f7fa",
  ink: "#171a24",
  inkSoft: "#565c6e",
  accent: "#5b4fc4",
  onAccent: "#ffffff",
  accentText: "#5b4fc4",
  accent2: "#1f7a72",
  border: "#ccd1db",
  danger: "#b03d26",
  dangerBg: "#fbe9e5",
  success: "#0b775b",
  successBg: "#e2f4ee",
  warning: "#8a5a00",
  warningBg: "#fbf0d9",
  neutralBg: "#eceef3",
};

export const dark: Tokens = {
  scheme: "dark",
  bg: "#121419",
  surface: "#1b1e27",
  surface2: "#232734",
  ink: "#e6e8ef",
  inkSoft: "#a0a7ba",
  accent: "#6a5fd6",
  onAccent: "#ffffff",
  accentText: "#a59cf5",
  accent2: "#4fc1b5",
  border: "#353b4a",
  danger: "#ff8b70",
  dangerBg: "#3a211c",
  success: "#5cd1a6",
  successBg: "#15302a",
  warning: "#efc25e",
  warningBg: "#352b14",
  neutralBg: "#2a2f3b",
};

// Kept for code that only needs the light palette (and backwards compatibility).
export const tokens = light;

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
export const radius = { sm: 6, md: 10, lg: 14, pill: 999 } as const;
