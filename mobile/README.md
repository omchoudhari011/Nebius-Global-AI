# Custodian mobile (Expo)

Step 1 skeleton from `docs/PLAN.md`. `package.json`'s dependency versions are
illustrative — run `npm install` (or regenerate with `npx create-expo-app`) to
resolve real, current versions before first use.

## Setup

```
npm install
```

Web:
```
npm run web
```

Phone (Expo Go): scan the QR code from
```
npx expo start
```
Set `EXPO_PUBLIC_API_BASE` to your machine's LAN IP, not `localhost` — a phone
can't resolve your laptop's localhost.

See `docs/PLAN.md` for what each later step adds to this app.
