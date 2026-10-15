// The signed-in app: Chat, Scheduled, Tools & Data and Account, joined by a bottom tab bar.
// Anyone not signed in is sent to /sign-in.

import Ionicons from "@expo/vector-icons/Ionicons";
import { Redirect } from "expo-router";
import { Tabs } from "expo-router/js-tabs";
import { ActivityIndicator, Platform, View } from "react-native";

import { useAuth } from "../../lib/auth";
import { InboxProvider } from "../../lib/inbox";
import { useTokens } from "../../lib/tokens";

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

  return (
    <InboxProvider>
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarHideOnKeyboard: true,
        tabBarActiveTintColor: t.accentText,
        tabBarInactiveTintColor: t.inkSoft,
        tabBarStyle: [
          { backgroundColor: t.surface, borderTopColor: t.border },
          // On the web there's no safe-area inset, and the default height clips stacked labels.
          Platform.OS === "web" ? { height: 60, paddingTop: 4, paddingBottom: 6 } : null,
        ],
        sceneStyle: { backgroundColor: t.bg },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Chat",
          tabBarIcon: ({ color, size }) => <Ionicons name="chatbubble-ellipses-outline" size={size} color={color} />,
        }}
      />
      <Tabs.Screen
        name="scheduled"
        options={{
          title: "Scheduled",
          tabBarIcon: ({ color, size }) => <Ionicons name="alarm-outline" size={size} color={color} />,
        }}
      />
      <Tabs.Screen
        name="tools-data"
        options={{
          title: "Tools & Data",
          tabBarIcon: ({ color, size }) => <Ionicons name="shield-checkmark-outline" size={size} color={color} />,
        }}
      />
      <Tabs.Screen
        name="account"
        options={{
          title: "Account",
          tabBarIcon: ({ color, size }) => <Ionicons name="person-circle-outline" size={size} color={color} />,
        }}
      />
    </Tabs>
    </InboxProvider>
  );
}
