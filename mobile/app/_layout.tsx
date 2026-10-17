// Root layout: fonts, safe areas, light/dark navigation theme, and the session provider.
// app/(tabs)/_layout.tsx sends signed-out users to app/sign-in.tsx.

import {
  CormorantGaramond_500Medium_Italic,
  CormorantGaramond_600SemiBold,
  CormorantGaramond_700Bold,
} from "@expo-google-fonts/cormorant-garamond";
import { Inter_400Regular, Inter_500Medium, Inter_600SemiBold, Inter_700Bold } from "@expo-google-fonts/inter";
import { useFonts } from "expo-font";
import { LinearGradient } from "expo-linear-gradient";
import { DarkTheme, DefaultTheme, Stack, ThemeProvider } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useMemo } from "react";
import { StyleSheet, View } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { AuthProvider } from "../lib/auth";
import { useTokens } from "../lib/tokens";

export default function RootLayout() {
  const t = useTokens();
  const [fontsReady, fontError] = useFonts({
    CormorantGaramond_500Medium_Italic, CormorantGaramond_600SemiBold, CormorantGaramond_700Bold,
    Inter_400Regular, Inter_500Medium, Inter_600SemiBold, Inter_700Bold,
  });
  const navTheme = useMemo(() => {
    const base = t.scheme === "dark" ? DarkTheme : DefaultTheme;
    return {
      ...base,
      colors: { ...base.colors, primary: t.accentText, background: "transparent", card: t.surface, text: t.ink, border: t.border },
    };
  }, [t]);

  // A moment of plain background while the fonts load (they're bundled; this is brief). If they
  // can't load, the app carries on in the system font.
  if (!fontsReady && !fontError) return <View style={{ flex: 1, backgroundColor: t.bg }} />;

  return (
    <SafeAreaProvider>
      <LinearGradient colors={[t.bgTop, t.bgBottom]} style={StyleSheet.absoluteFill} />
      <ThemeProvider value={navTheme}>
        <AuthProvider>
          <StatusBar style={t.scheme === "dark" ? "light" : "dark"} />
          <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: "transparent" } }} />
        </AuthProvider>
      </ThemeProvider>
    </SafeAreaProvider>
  );
}
