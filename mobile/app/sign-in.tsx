// Sign in with the server's passcode (CUSTODIAN_PASSCODE).
// Futuristic experience with glowing cybernetic gradient card, radiant AI core halo, and tactile inputs.

import { Redirect, router } from "expo-router";
import { useState } from "react";
import { LinearGradient } from "expo-linear-gradient";
import { ActivityIndicator, KeyboardAvoidingView, Platform, ScrollView, StyleSheet, View } from "react-native";
import { Text } from "../components/Typography";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Orb } from "../components/Orb";
import { Button, Field, Notice, ScreenGradient } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth";
import { fonts, radius, softShadow, space, themed, useTokens } from "../lib/tokens";

const useStyles = themed((t) => ({
  page: { flexGrow: 1, justifyContent: "center", padding: space.xl, backgroundColor: "transparent" },
  card: {
    width: "100%",
    maxWidth: 420,
    alignSelf: "center",
    borderRadius: radius.xl,
    padding: space.xl,
    paddingTop: 40,
    gap: space.lg,
    overflow: "hidden",
    ...softShadow(t, 2),
  },
  orbWell: {
    width: 108,
    height: 108,
    borderRadius: 54,
    alignItems: "center",
    justifyContent: "center",
    overflow: "hidden",
    marginBottom: space.xs,
    ...softShadow(t, 1),
  },
  title: { fontFamily: fonts.display, fontSize: 32, lineHeight: 38, color: t.ink, textAlign: "center", marginTop: space.sm, letterSpacing: 0.5 },
  text: { fontSize: 16, color: t.inkSoft, lineHeight: 23, textAlign: "center" },
  hint: { fontSize: 13, lineHeight: 18, color: t.inkSoft },
}));

export default function SignIn() {
  const s = useStyles();
  const t = useTokens();
  const insets = useSafeAreaInsets();
  const { status, signIn, serverUrl, startupError } = useAuth();
  const native = Platform.OS !== "web";
  const [passcode, setPasscode] = useState("");
  const [server, setServer] = useState(serverUrl);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (status === "signedIn") return <Redirect href="/" />;
  if (status === "loading") {
    return (
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: t.bg }}>
        <ActivityIndicator color={t.accent} />
      </View>
    );
  }

  async function submit() {
    if (!passcode || busy) return;
    setBusy(true);
    setError(null);
    try {
      let targetServer = server?.trim();
      if (targetServer && /:8081\/?$/.test(targetServer)) {
        targetServer = targetServer.replace(/:8081\/?$/, ":8000");
        setServer(targetServer);
      }
      await signIn(passcode, native ? targetServer : undefined);
      setPasscode("");
      router.replace("/");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: t.bg }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScreenGradient />
      <ScrollView
        contentContainerStyle={[s.page, { paddingTop: insets.top + space.xl, paddingBottom: insets.bottom + space.xl }]}
        keyboardShouldPersistTaps="handled"
      >
        <LinearGradient
          colors={t.gradients.card}
          start={{ x: 0, y: 0 }}
          end={{ x: 0.9, y: 1 }}
          style={s.card}
        >
          <View style={{ gap: space.sm, alignItems: "center" }}>
            <View style={s.orbWell}>
              <LinearGradient
                colors={["rgba(6, 18, 16, 0.96)", "rgba(18, 44, 38, 0.85)"]}
                start={{ x: 0, y: 0 }}
                end={{ x: 1, y: 1 }}
                style={StyleSheet.absoluteFill}
              />
              <Orb size={82} mood={busy ? "thinking" : "idle"} />
            </View>
            <Text accessibilityRole="header" style={s.title}>
              Welcome back
            </Text>
            <Text style={s.text}>
              It's good to see you. Everything you tell me stays on your own server, and I only use your tools and your PC
              when you say so.
            </Text>
          </View>

          {error || startupError ? <Notice message={error ?? startupError ?? ""} /> : null}

          {native ? (
            <View style={{ gap: 6 }}>
              <Field
                label="Server"
                value={server}
                onChangeText={setServer}
                autoCapitalize="none"
                autoCorrect={false}
                keyboardType="url"
                placeholder="https://custodian.example.com"
                textContentType="URL"
              />
              <Text style={s.hint}>On your home network use your computer's address, e.g. http://192.168.1.20:8000.</Text>
            </View>
          ) : null}

          <Field
            label="Passcode"
            value={passcode}
            onChangeText={setPasscode}
            secureTextEntry
            autoCapitalize="none"
            autoCorrect={false}
            autoComplete="current-password"
            textContentType="password"
            returnKeyType="go"
            onSubmitEditing={submit}
            autoFocus={!native}
          />
          <Button label="Sign in" onPress={submit} loading={busy} disabled={!passcode} />
          <Text style={[s.hint, { textAlign: "center" }]}>Forgot it? It's CUSTODIAN_PASSCODE in the server's settings.</Text>
        </LinearGradient>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}
