// Keychain-backed storage for the native session token and server address.
// The web build keeps nothing here: its session is an httpOnly cookie and it talks to its
// own origin, so there is no token for page scripts to read.

import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

const native = Platform.OS !== "web";

export const KEYS = { token: "custodian.token", server: "custodian.server" } as const;

export async function getItem(key: string): Promise<string | null> {
  if (!native) return null;
  try {
    return await SecureStore.getItemAsync(key);
  } catch {
    return null;
  }
}

export async function setItem(key: string, value: string): Promise<void> {
  if (!native) return;
  await SecureStore.setItemAsync(key, value);
}

export async function removeItem(key: string): Promise<void> {
  if (!native) return;
  try {
    await SecureStore.deleteItemAsync(key);
  } catch {
    // already gone
  }
}
