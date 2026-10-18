// Custodian's face: a soft, warm sphere that breathes - slowly while it waits, quicker while
// it thinks - with a gentle halo. It stays still when the system asks for reduced motion,
// and at avatar size (`still`), where movement next to every message would be noise.

import { LinearGradient } from "expo-linear-gradient";
import { useEffect, useRef, useState } from "react";
import { AccessibilityInfo, Animated, Easing, Platform, View } from "react-native";

import { useTokens } from "../lib/tokens";

export type OrbMood = "idle" | "thinking";

export function Orb({ size = 40, mood = "idle", still = false }: { size?: number; mood?: OrbMood; still?: boolean }) {
  const t = useTokens();
  const pulse = useRef(new Animated.Value(0)).current;
  const [reduceMotion, setReduceMotion] = useState(false);

  useEffect(() => {
    AccessibilityInfo.isReduceMotionEnabled().then(setReduceMotion).catch(() => undefined);
    const subscription = AccessibilityInfo.addEventListener("reduceMotionChanged", setReduceMotion);
    return () => subscription.remove();
  }, []);

  const moving = !still && !reduceMotion;
  useEffect(() => {
    if (!moving) {
      pulse.setValue(0);
      return;
    }
    const half = mood === "thinking" ? 650 : 2600;
    const native = Platform.OS !== "web";
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: half, easing: Easing.inOut(Easing.sin), useNativeDriver: native }),
        Animated.timing(pulse, { toValue: 0, duration: half, easing: Easing.inOut(Easing.sin), useNativeDriver: native }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [moving, mood, pulse]);

  const thinking = mood === "thinking";
  const scale = pulse.interpolate({ inputRange: [0, 1], outputRange: [1, thinking ? 1.08 : 1.04] });
  const haloScale = pulse.interpolate({ inputRange: [0, 1], outputRange: [1.04, thinking ? 1.42 : 1.28] });
  const haloOpacity = pulse.interpolate({ inputRange: [0, 1], outputRange: [0.16, thinking ? 0.5 : 0.3] });
  const colors = t.scheme === "dark" ? (["#FFD6A8", "#F2A65A", "#DE7A50"] as const) : (["#FFCBA6", "#F48D69", "#E0603F"] as const);
  const round = size / 2;

  return (
    <View
      style={{ width: size, height: size, alignItems: "center", justifyContent: "center" }}
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
    >
      {moving ? (
        <Animated.View
          style={{ position: "absolute", width: size, height: size, borderRadius: round, backgroundColor: t.glow,
            opacity: haloOpacity, transform: [{ scale: haloScale }] }}
        />
      ) : null}
      <Animated.View style={{ width: size, height: size, borderRadius: round, overflow: "hidden", transform: [{ scale }] }}>
        <LinearGradient colors={colors} start={{ x: 0.15, y: 0 }} end={{ x: 0.85, y: 1 }} style={{ flex: 1 }} />
        {/* A soft highlight, so it reads as a sphere rather than a flat dot. */}
        <View
          style={{ position: "absolute", top: size * 0.13, left: size * 0.19, width: size * 0.36, height: size * 0.22,
            borderRadius: size, backgroundColor: "rgba(255,255,255,0.42)", transform: [{ rotate: "-22deg" }] }}
        />
      </Animated.View>
    </View>
  );
}
