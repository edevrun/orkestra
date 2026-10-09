# Stack checklist: mobile (on demand — React Native / Expo; distilled from vendor/ecc: rules/react-native)

Load for projects with `app.json` / Expo, together with stack-web's Types / Async / React sections. Only the diff; cite file:line.

## Blocker
- Private key/token in the JS bundle, app.config `extra` or `EXPO_PUBLIC_*` — the binary is public; only anon keys backed
  by server-side rules are acceptable.
- Auth token or other sensitive value in AsyncStorage / plain MMKV / a persisted store → `expo-secure-store`.
- Deep-link, universal-link or push-payload params used for navigation or access without validation.
- Native change (new native module, permission, config plugin, SDK bump) treated as an OTA update: it needs a new store
  build plus `version` / `ios.buildNumber` / `android.versionCode` bump — say so in the report.

## Major
- Large data rendered with `.map()` inside a ScrollView → FlatList/FlashList with `keyExtractor` and memoized `renderItem`.
- Listener, subscription, timer or AppState handler without cleanup; work that continues after unmount or in background.
- Network/mutation failure not surfaced to the user (offline, timeout) — silent failure.
- New permission requested at launch or without a usage description (iOS `NS…UsageDescription`, Android manifest);
  store privacy disclosure affected → flag it for the user.
- New native dependency not checked against the installed Expo SDK / New Architecture (`npx expo install --check`,
  `npx expo-doctor`).
- Platform parity: iOS-only or Android-only API without a `Platform` guard; safe area, keyboard avoidance or the Android
  back button ignored on a new screen.
- JS-driven animation on a hot path (legacy `Animated` without `useNativeDriver`) → Reanimated.

## Minor
- Full-resolution images in thumbnails (use `expo-image` with sized sources); inline object/function props on list
  rows; heavy synchronous work on the JS thread (defer with `InteractionManager`).

## Verification honesty
State exactly what was exercised: unit tests, simulator/emulator, or a physical device — per platform. Never claim a
device check that was not run.
