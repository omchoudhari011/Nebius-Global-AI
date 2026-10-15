// Root layout: safe areas, light/dark navigation theme, and the session provider.
// app/(tabs)/_layout.tsx sends signed-out users to app/sign-in.tsx.

import { DarkTheme, DefaultTheme, Stack, ThemeProvider } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useMemo } from "react";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { AuthProvider } from "../lib/auth";
import { useTokens } from "../lib/tokens";

export default function RootLayout() {
  const t = useTokens();
  const navTheme = useMemo(() => {
    const base = t.scheme === "dark" ? DarkTheme : DefaultTheme;
    return {
      ...base,
      colors: { ...base.colors, primary: t.accentText, background: t.bg, card: t.surface, text: t.ink, border: t.border },
    };
  }, [t]);

  return (
    <SafeAreaProvider>
      <ThemeProvider value={navTheme}>
        <AuthProvider>
          <StatusBar style={t.scheme === "dark" ? "light" : "dark"} />
          <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: t.bg } }} />
        </AuthProvider>
      </ThemeProvider>
    </SafeAreaProvider>
  );
}
