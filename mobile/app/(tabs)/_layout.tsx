// The signed-in app: Chat, Jarvis, Scheduled, Tools & Data and Account, joined by a bottom tab bar.
// Features illuminated gradient indicator pills and ambient glass styling.

import Ionicons from "@expo/vector-icons/Ionicons";
import { Redirect } from "expo-router";
import { Tabs } from "expo-router/js-tabs";
import type { ComponentProps } from "react";
import { LinearGradient } from "expo-linear-gradient";
import { ActivityIndicator, Platform, StyleSheet, View, type ColorValue } from "react-native";

import { useAuth } from "../../lib/auth";
import { InboxProvider } from "../../lib/inbox";
import { fonts, softShadow, useTokens } from "../../lib/tokens";

type IconName = ComponentProps<typeof Ionicons>["name"];

/** A tab's icon: when focused, it sits illuminated in a glowing cybernetic gradient pill. */
function TabIcon({ name, focused, color }: { name: string; focused: boolean; color: ColorValue }) {
  const t = useTokens();
  return (
    <View
      style={{
        width: 58,
        height: 32,
        borderRadius: 16,
        alignItems: "center",
        justifyContent: "center",
        overflow: "hidden",
      }}
    >
      {focused ? (
        <LinearGradient
          colors={t.gradients.tabActive}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 0 }}
          style={[
            StyleSheet.absoluteFill,
            {
              borderRadius: 16,
            },
          ]}
        />
      ) : null}
      <Ionicons
        name={(focused ? name : `${name}-outline`) as IconName}
        size={21}
        color={focused ? t.accentText : color}
      />
    </View>
  );
}

export default function TabsLayout() {
  const t = useTokens();
  const { status } = useAuth();

  if (status === "loading") {
    return (
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: t.bg }}>
        <ActivityIndicator color={t.accent} />
      </View>
    );
  }
  if (status === "signedOut") return <Redirect href="/sign-in" />;

  const icon = (name: string) => ({ focused, color }: { focused: boolean; color: ColorValue }) => (
    <TabIcon name={name} focused={focused} color={color} />
  );

  return (
    <InboxProvider>
      <Tabs
        screenOptions={{
          headerShown: false,
          tabBarHideOnKeyboard: true,
          tabBarLabelPosition: "below-icon",
          tabBarActiveTintColor: t.accentText,
          tabBarInactiveTintColor: t.inkSoft,
          tabBarLabelStyle: { fontFamily: fonts.bodyBold, fontSize: 11, letterSpacing: 0.4 },
          tabBarStyle: [
            {
              backgroundColor: t.scheme === "dark" ? "rgba(9, 22, 20, 0.95)" : "rgba(255, 255, 255, 0.95)",
              borderTopColor: t.hairline,
              borderTopWidth: 1,
              ...softShadow(t, 2),
            },
            Platform.OS === "web" ? { height: 68, paddingTop: 6, paddingBottom: 8 } : null,
          ],
          sceneStyle: { backgroundColor: t.bg },
        }}
      >
        <Tabs.Screen name="index" options={{ title: "Chat", tabBarIcon: icon("chatbubble-ellipses") }} />
        <Tabs.Screen name="jarvis" options={{ title: "Jarvis", tabBarIcon: icon("desktop") }} />
        <Tabs.Screen name="scheduled" options={{ title: "Scheduled", tabBarIcon: icon("alarm") }} />
        <Tabs.Screen name="tools-data" options={{ title: "Tools & Data", tabBarIcon: icon("shield-checkmark") }} />
        <Tabs.Screen name="account" options={{ title: "Account", tabBarIcon: icon("person-circle") }} />
      </Tabs>
    </InboxProvider>
  );
}
