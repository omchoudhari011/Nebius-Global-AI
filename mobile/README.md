# Custodian mobile (Expo)

Step 1 skeleton from `docs/PLAN.md`: a chat screen (`app/index.tsx`) and a
Tools & Data screen (`app/tools-data.tsx`), joined by a bottom tab bar
(`app/_layout.tsx`).

## Setup

```
npm install
npx expo install --fix
```

`npx expo install --fix` sets every Expo package to the version that matches
SDK 51, so run it after `npm install`.

## Run it

Start the backend first (see `backend/README.md`).

Web:
```
npm run web
```
The backend allows the Expo web address `http://localhost:8081` by default.
If `npm run web` opens a different address, add it to `CORS_ORIGINS` in
`backend/.env`.

Phone (Expo Go): create `mobile/.env` with your laptop's LAN IP, then scan the
QR code from `npx expo start`:
```
EXPO_PUBLIC_API_BASE=http://192.168.1.20:8000
```
A phone can't resolve your laptop's `localhost`, so use the LAN IP (replace
`192.168.1.20` with yours), keep the phone on the same Wi-Fi, and run the
backend with `--host 0.0.0.0`. Restart `npx expo start` after changing `.env`.

- Android emulator: use `http://10.0.2.2:8000`, the emulator's address for
  your computer.
- iOS simulator: the default `http://localhost:8000` works.

See `docs/PLAN.md` for what each later step adds to this app.
