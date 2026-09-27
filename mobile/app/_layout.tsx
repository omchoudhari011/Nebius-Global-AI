// Root layout. Expo Router wraps every screen in app/ with this file.
// Tabs adds a bottom tab bar, so you can move between the chat (app/index.tsx)
// and the Tools & Data screen (app/tools-data.tsx). Without this file nothing
// linked the two screens.

import { Tabs } from "expo-router";
import Ionicons from "@expo/vector-icons/Ionicons";
import { tokens } from "../lib/tokens";

export default function RootLayout() {
  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: tokens.accent,
        tabBarInactiveTintColor: tokens.inkSoft,
        tabBarStyle: { backgroundColor: tokens.surface, borderTopColor: tokens.border },
        headerStyle: { backgroundColor: tokens.surface },
        headerTintColor: tokens.ink,
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
        name="tools-data"
        options={{
          title: "Tools & Data",
          tabBarIcon: ({ color, size }) => <Ionicons name="shield-checkmark-outline" size={size} color={color} />,
        }}
      />
    </Tabs>
  );
}
