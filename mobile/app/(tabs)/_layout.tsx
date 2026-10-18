// The signed-in app: Chat, Scheduled, Tools & Data and Account, joined by a bottom tab bar.
// Anyone not signed in is sent to /sign-in.

import Ionicons from "@expo/vector-icons/Ionicons";
import { Redirect } from "expo-router";
import { Tabs } from "expo-router/js-tabs";
import type { ComponentProps } from "react";
import { ActivityIndicator, Platform, View, type ColorValue } from "react-native";

import { useAuth } from "../../lib/auth";
import { InboxProvider } from "../../lib/inbox";
import { fonts, useTokens } from "../../lib/tokens";

type IconName = ComponentProps<typeof Ionicons>["name"];

/** A tab's icon: filled, on a soft accent pill, when it's the tab you're on. */
function TabIcon({ name, focused, color }: { name: string; focused: boolean; color: ColorValue }) {
  const t = useTokens();
  return (
    <View style={{ width: 58, height: 30, borderRadius: 15, alignItems: "center", justifyContent: "center",
      backgroundColor: focused ? t.accentSoft : "transparent" }}>
      <Ionicons name={(focused ? name : `${name}-outline`) as IconName} size={21} color={color} />
    </View>
  );
}

export default function TabsLayout() {
  const t = useTokens();
  const { status } = useAuth();

  if (status === "loading") {
    return (
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: t.bg }}>
        <ActivityIndicator color={t.accent2} />
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
          tabBarLabelPosition: "below-icon", // also on wide screens, so the active pill sits over its label
          tabBarActiveTintColor: t.accentText,
          tabBarInactiveTintColor: t.inkSoft,
          tabBarLabelStyle: { fontFamily: fonts.bodyBold, fontSize: 12 },
          tabBarStyle: [
            { backgroundColor: t.surface, borderTopColor: t.hairline, borderTopWidth: 1 },
            // On the web there's no safe-area inset, and the default height clips stacked labels.
            Platform.OS === "web" ? { height: 66, paddingTop: 6, paddingBottom: 8 } : null,
          ],
          sceneStyle: { backgroundColor: t.bg },
        }}
      >
        <Tabs.Screen name="index" options={{ title: "Chat", tabBarIcon: icon("chatbubble-ellipses") }} />
        <Tabs.Screen name="scheduled" options={{ title: "Scheduled", tabBarIcon: icon("alarm") }} />
        <Tabs.Screen name="tools-data" options={{ title: "Tools & Data", tabBarIcon: icon("shield-checkmark") }} />
        <Tabs.Screen name="account" options={{ title: "Account", tabBarIcon: icon("person-circle") }} />
      </Tabs>
    </InboxProvider>
  );
}
