// Sign in with the server's passcode (CUSTODIAN_PASSCODE). The native app also asks which
// server to use, since a phone can't reach the laptop's "localhost".

import { Redirect, router } from "expo-router";
import { useState } from "react";
import { ActivityIndicator, KeyboardAvoidingView, Platform, ScrollView, View } from "react-native";
import { Text } from "../components/Typography";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Button, Crest, Field, GoldRule, Notice } from "../components/ui";
import { errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth";
import { fonts, radius, space, themed, useTokens } from "../lib/tokens";

const useStyles = themed((t) => ({
  page: { flexGrow: 1, justifyContent: "center", padding: space.xl, backgroundColor: "transparent" },
  card: {
    width: "100%",
    maxWidth: 420,
    alignSelf: "center",
    backgroundColor: t.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: t.hairline,
    padding: space.xl,
    paddingTop: 36,
    gap: space.lg,
    shadowColor: "#000",
    shadowOpacity: t.scheme === "dark" ? 0.45 : 0.1,
    shadowRadius: 30,
    shadowOffset: { width: 0, height: 12 },
    elevation: 4,
  },
  mark: { fontSize: 12, fontWeight: "700", letterSpacing: 4, color: t.scheme === "dark" ? t.gold : t.accent2, textAlign: "center" },
  title: { fontFamily: fonts.display, fontSize: 40, lineHeight: 46, color: t.ink, textAlign: "center" },
  text: { fontSize: 15, color: t.inkSoft, lineHeight: 22, textAlign: "center" },
  hint: { fontSize: 13, color: t.inkSoft },
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
        <ActivityIndicator color={t.accent2} />
      </View>
    );
  }

  async function submit() {
    if (!passcode || busy) return;
    setBusy(true);
    setError(null);
    try {
      await signIn(passcode, native ? server : undefined);
      setPasscode("");
      router.replace("/");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScrollView
        contentContainerStyle={[s.page, { paddingTop: insets.top + space.xl, paddingBottom: insets.bottom + space.xl }]}
        keyboardShouldPersistTaps="handled"
      >
        <View style={s.card}>
          <View style={{ gap: space.md, alignItems: "center" }}>
            <Crest size={84} />
            <Text style={s.mark}>CUSTODIAN</Text>
            <Text accessibilityRole="header" style={s.title}>
              Welcome back
            </Text>
            <GoldRule style={{ alignSelf: "stretch" }} />
            <Text style={s.text}>Your private steward. It remembers what you tell it, and uses your tools and your PC only with your permission.</Text>
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
          <Text style={s.hint}>The passcode is set on the server as CUSTODIAN_PASSCODE.</Text>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}
