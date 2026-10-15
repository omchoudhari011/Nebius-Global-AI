# Custodian app (Expo)

One codebase for iOS, Android and the web: Expo SDK 57, Expo Router, TypeScript (strict),
light and dark mode.

| Route | File | What it is |
|---|---|---|
| `/sign-in` | `app/sign-in.tsx` | Passcode (and server address on phones) |
| `/` | `app/(tabs)/index.tsx` | Chat, with a provenance line (and accuracy check) under each reply, the Active Context panel, off-the-record chats, attach a file, voice messages, read aloud, rate replies, Think harder, and the inbox (bell) |
| `/scheduled` | `app/(tabs)/scheduled.tsx` | Scheduled tasks: run now, pause, output, new task |
| `/tools-data` | `app/(tabs)/tools-data.tsx` | Google account, files, upcoming reminders, permission switches, memories, audit log |
| `/account` | `app/(tabs)/account.tsx` | Plan and usage, license key, where data goes, retention, export and erase, signed-in devices, sign out |

`lib/api.ts` is the typed API client, `lib/auth.tsx` the session state, `lib/tokens.ts` the
light/dark colour tokens, and `components/` the shared pieces.

`lib/inbox.tsx` polls the inbox and the server's features every 30 s; `lib/notify.ts` shows
browser notifications on the web and schedules reminders as local notifications on phones;
`lib/media.ts` picks files and reads replies aloud; `components/VoiceButton.tsx` records
voice messages (MediaRecorder on the web, expo-audio on phones).

## Run it

Start the backend first (see `../backend/README.md`), then:

```bash
npm install
npm run web          # http://localhost:8081, talks to http://localhost:8000
npx expo start       # Expo Go on a phone (scan the QR code)
```

**Where the app finds the API**

- Web in development: `http://localhost:8000` (the backend allows the 8081 origin by default).
- Web in production: the same origin it was served from (the Docker image serves both).
- Phones: type the server address on the sign-in screen, e.g. `http://192.168.1.20:8000`
  on your Wi-Fi (the phone can't reach your computer's `localhost`; run the backend with
  `--host 0.0.0.0`). The Android emulator reaches your computer at `http://10.0.2.2:8000`;
  the iOS simulator can use `http://localhost:8000`.
- `EXPO_PUBLIC_API_BASE` (in `mobile/.env` or the build environment) sets the default.

On phones the session token is kept in the device keychain (`expo-secure-store`); on the web
it's an httpOnly cookie the page can't read.

## Checks

```bash
npm run typecheck
npm run export:web   # production web build in dist/
```

## Store builds

```bash
npx eas build --profile preview      # installable test builds (Android APK)
npx eas build --profile production
```

The app id (`ios.bundleIdentifier`, `android.package`) is `io.github.omchoudhari011.custodian`;
a fork should change it to one it owns. Release builds should use an HTTPS server.
