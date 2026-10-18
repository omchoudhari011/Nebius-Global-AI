// Root layout: fonts, safe areas, light/dark navigation theme, and the session provider.
// app/(tabs)/_layout.tsx sends signed-out users to app/sign-in.tsx.

import {
  Nunito_400Regular,
  Nunito_500Medium,
  Nunito_600SemiBold,
  Nunito_700Bold,
  Nunito_800ExtraBold,
} from "@expo-google-fonts/nunito";
import { useFonts } from "expo-font";
import { DarkTheme, DefaultTheme, Stack, ThemeProvider } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useMemo } from "react";
import { View } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { AuthProvider } from "../lib/auth";
import { useTokens } from "../lib/tokens";

export default function RootLayout() {
  const t = useTokens();
  const [fontsReady, fontError] = useFonts({
    Nunito_400Regular, Nunito_500Medium, Nunito_600SemiBold, Nunito_700Bold, Nunito_800ExtraBold,
  });
  const navTheme = useMemo(() => {
    const base = t.scheme === "dark" ? DarkTheme : DefaultTheme;
    return {
      ...base,
      colors: { ...base.colors, primary: t.accentText, background: t.bg, card: t.surface, text: t.ink, border: t.border },
    };
  }, [t]);

  // A moment of plain background while the fonts load (they're bundled; this is brief). If they
  // can't load, the app carries on in the system font.
  if (!fontsReady && !fontError) return <View style={{ flex: 1, backgroundColor: t.bg }} />;

  return (
    <SafeAreaProvider>
      <ThemeProvider value={navTheme}>
        <AuthProvider>
          <StatusBar style={t.scheme === "dark" ? "light" : "dark"} />
          {/* Opaque: a stack keeps screens mounted under the one in front. Each screen paints the
              theme gradient itself (ScreenGradient in components/ui.tsx). */}
          <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: t.bg } }} />
        </AuthProvider>
      </ThemeProvider>
    </SafeAreaProvider>
  );
}
