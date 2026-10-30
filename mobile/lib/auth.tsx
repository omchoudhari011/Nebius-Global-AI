// Session state for the whole app: restores the session on launch, signs in and out, and
// drops back to the sign-in screen whenever the API answers 401.

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Platform } from "react-native";

import * as api from "./api";
import { KEYS, getItem, removeItem, setItem } from "./storage";
import type { ServerInfo, User } from "./types";

type Status = "loading" | "signedOut" | "signedIn";

type AuthValue = {
  status: Status;
  user: User | null;
  server: ServerInfo | null;
  serverUrl: string;
  startupError: string | null;
  signIn: (passcode: string, serverUrl?: string) => Promise<void>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [user, setUser] = useState<User | null>(null);
  const [server, setServer] = useState<ServerInfo | null>(null);
  const [serverUrl, setServerUrl] = useState(api.defaultApiBase());
  const [startupError, setStartupError] = useState<string | null>(null);

  const signedOut = useCallback(() => {
    setUser(null);
    setStatus("signedOut");
  }, []);

  useEffect(() => {
    api.onUnauthorized(() => {
      void removeItem(KEYS.token);
      api.configureApi({ token: null });
      signedOut();
    });
    return () => api.onUnauthorized(null);
  }, [signedOut]);

  const restore = useCallback(async () => {
    setStartupError(null);
    const savedServer = await getItem(KEYS.server);
    const savedToken = await getItem(KEYS.token);
    // The web app served by Custodian always talks to the server it came from; a saved address
    // (from an older build that pinned it to localhost) would break it opened from another device.
    if (savedServer && !(Platform.OS === "web" && !__DEV__)) {
      api.configureApi({ base: savedServer });
      setServerUrl(api.getApiBase());
    }
    if (Platform.OS !== "web" && !savedToken) {
      signedOut();
      return;
    }
    api.configureApi({ token: savedToken });
    try {
      const res = await api.me();
      setUser(res.user);
      setServer(res.server);
      setStatus("signedIn");
    } catch (err) {
      if (err instanceof api.ApiError && err.status !== 401) setStartupError(err.message);
      signedOut();
    }
  }, [signedOut]);

  useEffect(() => {
    void restore();
  }, [restore]);

  const signIn = useCallback(async (passcode: string, url?: string) => {
    let cleanUrl = url?.trim();
    if (cleanUrl && /:8081\/?$/.test(cleanUrl)) {
      cleanUrl = cleanUrl.replace(/:8081\/?$/, ":8000");
    }
    if (cleanUrl !== undefined && Platform.OS !== "web") {
      api.configureApi({ base: cleanUrl });
      setServerUrl(api.getApiBase());
    }
    const res = await api.login(passcode);
    if (Platform.OS !== "web") {
      await setItem(KEYS.server, api.getApiBase());
      if (res.token) {
        await setItem(KEYS.token, res.token);
        api.configureApi({ token: res.token });
      }
    }
    setUser(res.user);
    setServer(res.server);
    setStartupError(null);
    setStatus("signedIn");
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // signing out locally is what matters
    }
    await removeItem(KEYS.token);
    api.configureApi({ token: null });
    signedOut();
  }, [signedOut]);

  const refresh = useCallback(async () => {
    const res = await api.me();
    setUser(res.user);
    setServer(res.server);
  }, []);

  const value = useMemo(
    () => ({ status, user, server, serverUrl, startupError, signIn, signOut, refresh }),
    [status, user, server, serverUrl, startupError, signIn, signOut, refresh],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}
