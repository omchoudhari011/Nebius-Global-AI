// Bioluminescent Kinetic Intelligence AI Core (The JARVIS Intelligence Core).
//
// Represents the living AI assistant through a fluid, organic, self-evolving intelligence core:
// - State 1: Idle       - Slow fluid breathing, soft emerald internal illumination, delicate turquoise reflections.
// - State 2: Listening  - Expands subtly, fluid motion energizes, turquoise intensifies, surface wave propagation.
// - State 3: Thinking   - Circulating computational vortex, luminous threads, emerald/turquoise energy interplay.
// - State 4: Speaking   - Fluid oscillations, rhythmic turquoise and lime pulses, radiant micro-particle embers.
// - State 5: Error      - Subdued glow, low-energy drift, clear non-flashing state.

import { LinearGradient } from "expo-linear-gradient";
import { useEffect, useRef, useState } from "react";
import { AccessibilityInfo, Animated, Easing, Platform, View } from "react-native";

import { useTokens } from "../lib/tokens";

export type OrbMood = "idle" | "listening" | "thinking" | "speaking" | "error" | "disconnected";

export type OrbProps = {
  size?: number;
  mood?: OrbMood;
  still?: boolean;
  level?: number; // 0..1 audio/voice amplitude
  interactive?: boolean;
};

// Web fluid canvas implementation for dynamic, organic bioluminescent motion
function WebBioluminescentOrb({ size = 80, mood = "idle", still = false, level = 0 }: OrbProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const animFrameRef = useRef<number | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // Retina display scaling
    const dpr = typeof window !== "undefined" ? Math.min(window.devicePixelRatio || 1, 2) : 1;
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    canvas.style.width = `${size}px`;
    canvas.style.height = `${size}px`;

    let time = Math.random() * 100;
    let targetEnergy = mood === "thinking" ? 1.5 : mood === "speaking" ? 1.8 : mood === "listening" ? 1.4 : mood === "error" ? 0.3 : 1.0;
    let currentEnergy = targetEnergy;

    // Internal particles
    const particleCount = size > 60 ? 16 : 8;
    const particles = Array.from({ length: particleCount }, (_, i) => ({
      angle: (i / particleCount) * Math.PI * 2,
      radius: (size * 0.22) * (0.6 + Math.random() * 0.6),
      speed: (0.008 + Math.random() * 0.015) * (i % 2 === 0 ? 1 : -1),
      size: 1 + Math.random() * 2,
      opacity: 0.3 + Math.random() * 0.6,
      pulse: Math.random() * Math.PI * 2,
    }));

    let isVisible = true;
    const handleVisibility = () => {
      isVisible = !document.hidden;
    };
    document.addEventListener("visibilitychange", handleVisibility);

    function render() {
      if (!ctx || !canvas) return;
      if (!isVisible) {
        animFrameRef.current = requestAnimationFrame(render);
        return;
      }

      ctx.save();
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, size, size);

      const cx = size / 2;
      const cy = size / 2;
      const baseR = size * 0.35;

      // Smooth energy transition
      const targetByMood =
        mood === "thinking" ? 1.6 :
        mood === "speaking" ? 1.9 + level * 0.6 :
        mood === "listening" ? 1.3 + level * 0.8 :
        mood === "error" || mood === "disconnected" ? 0.35 : 1.0;
      currentEnergy += (targetByMood - currentEnergy) * 0.08;

      const speedFactor = still ? 0 : currentEnergy * 0.024;
      time += speedFactor;

      // 1. Outer Volumetric Atmosphere Glow
      const glowGrad = ctx.createRadialGradient(cx, cy, baseR * 0.2, cx, cy, size * 0.49);
      if (mood === "error" || mood === "disconnected") {
        glowGrad.addColorStop(0, "rgba(255, 98, 98, 0.25)");
        glowGrad.addColorStop(0.5, "rgba(180, 50, 50, 0.12)");
        glowGrad.addColorStop(1, "rgba(14, 27, 26, 0)");
      } else {
        const glowAlpha = 0.22 + Math.sin(time * 0.8) * 0.08 + (mood === "speaking" ? 0.18 : 0);
        glowGrad.addColorStop(0, `rgba(35, 223, 188, ${glowAlpha * 1.2})`);
        glowGrad.addColorStop(0.4, `rgba(23, 132, 75, ${glowAlpha * 0.7})`);
        glowGrad.addColorStop(0.75, `rgba(14, 27, 26, ${glowAlpha * 0.3})`);
        glowGrad.addColorStop(1, "rgba(14, 27, 26, 0)");
      }
      ctx.fillStyle = glowGrad;
      ctx.beginPath();
      ctx.arc(cx, cy, size * 0.49, 0, Math.PI * 2);
      ctx.fill();

      // 2. Concentric Acoustic Waves for Listening / Speaking
      if ((mood === "listening" || mood === "speaking") && !still) {
        const ripplePhase = (time * 1.5) % 1;
        const rippleR = baseR * (1.05 + ripplePhase * 0.35);
        ctx.beginPath();
        ctx.arc(cx, cy, rippleR, 0, Math.PI * 2);
        ctx.strokeStyle = mood === "speaking"
          ? `rgba(201, 231, 80, ${(1 - ripplePhase) * 0.5})`
          : `rgba(35, 223, 188, ${(1 - ripplePhase) * 0.55})`;
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }

      // 3. Fluid Organic Membrane Deformation
      const points = 48;
      const harmonic1 = mood === "thinking" ? 5 : mood === "speaking" ? 4 : 3;
      const harmonic2 = mood === "thinking" ? 7 : 4;
      const deformAmp = still ? 0.02 : 0.07 * currentEnergy;

      ctx.beginPath();
      for (let i = 0; i <= points; i++) {
        const theta = (i / points) * Math.PI * 2;
        // Harmonic synthesis
        const wave1 = Math.sin(theta * harmonic1 + time * 1.2) * deformAmp;
        const wave2 = Math.cos(theta * harmonic2 - time * 0.9) * (deformAmp * 0.5);
        const wave3 = Math.sin(theta * 2 + time * 0.5) * (deformAmp * 0.7);
        const r = baseR * (1 + wave1 + wave2 + wave3);

        const x = cx + Math.cos(theta) * r;
        const y = cy + Math.sin(theta) * r;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.closePath();

      // Core Internal Fluid Gradient
      const coreGrad = ctx.createRadialGradient(
        cx - baseR * 0.22,
        cy - baseR * 0.25,
        baseR * 0.05,
        cx,
        cy,
        baseR * 1.15
      );

      if (mood === "error" || mood === "disconnected") {
        coreGrad.addColorStop(0, "#FFB2B2");
        coreGrad.addColorStop(0.35, "#FF6262");
        coreGrad.addColorStop(0.7, "#6A2222");
        coreGrad.addColorStop(1, "#1B0F0F");
      } else if (mood === "thinking") {
        // Swirling energetic computational interaction (Turquoise <-> Lime <-> Emerald)
        const shift = Math.sin(time * 2) * 0.1;
        coreGrad.addColorStop(0, "#FFFFFF");
        coreGrad.addColorStop(0.25 + shift, "#C9E750"); // Luminous lime thread
        coreGrad.addColorStop(0.55 - shift, "#23DFBC"); // Electric turquoise
        coreGrad.addColorStop(0.85, "#17844B"); // Deep emerald
        coreGrad.addColorStop(1, "#0A241B");
      } else if (mood === "speaking") {
        // Luminous rhythmic pulse (Lime-dominated burst)
        const pulse = 0.5 + Math.sin(time * 3.5) * 0.5;
        coreGrad.addColorStop(0, "#FFFFFF");
        coreGrad.addColorStop(0.28, pulse > 0.5 ? "#C9E750" : "#5AD14C");
        coreGrad.addColorStop(0.65, "#23DFBC");
        coreGrad.addColorStop(0.9, "#17844B");
        coreGrad.addColorStop(1, "#081F17");
      } else if (mood === "listening") {
        // Electric turquoise acoustic focus
        coreGrad.addColorStop(0, "#FFFFFF");
        coreGrad.addColorStop(0.28, "#23DFBC");
        coreGrad.addColorStop(0.65, "#17844B");
        coreGrad.addColorStop(0.88, "#5AD14C");
        coreGrad.addColorStop(1, "#092219");
      } else {
        // Idle: Deep living emerald organism with serene turquoise & lime whispers
        const breath = Math.sin(time * 0.8) * 0.05;
        coreGrad.addColorStop(0, "#F2FFF8");
        coreGrad.addColorStop(0.3 + breath, "#23DFBC");
        coreGrad.addColorStop(0.68, "#17844B");
        coreGrad.addColorStop(0.92, "#10422B");
        coreGrad.addColorStop(1, "#071D15");
      }

      ctx.fillStyle = coreGrad;
      ctx.fill();

      // Fluid Membrane Edge Highlight
      ctx.strokeStyle = mood === "error" || mood === "disconnected"
        ? "rgba(255, 120, 120, 0.6)"
        : mood === "speaking"
        ? "rgba(201, 231, 80, 0.85)"
        : "rgba(35, 223, 188, 0.75)";
      ctx.lineWidth = 1.4;
      ctx.shadowColor = mood === "speaking" ? "#C9E750" : "#23DFBC";
      ctx.shadowBlur = size * 0.12;
      ctx.stroke();
      ctx.shadowBlur = 0;

      // 4. Internal Bioluminescent Filaments (Computational Streams)
      if (!still && mood !== "error" && mood !== "disconnected") {
        const filamentCount = mood === "thinking" ? 3 : 2;
        for (let f = 0; f < filamentCount; f++) {
          ctx.save();
          ctx.beginPath();
          const fAngle = time * (0.8 + f * 0.4) * (f % 2 === 0 ? 1 : -1) + (f * Math.PI) / 2;
          const fx1 = cx + Math.cos(fAngle) * (baseR * 0.6);
          const fy1 = cy + Math.sin(fAngle) * (baseR * 0.6);
          const fx2 = cx - Math.cos(fAngle + 1.2) * (baseR * 0.6);
          const fy2 = cy - Math.sin(fAngle + 1.2) * (baseR * 0.6);
          const ctrlX = cx + Math.cos(time + f) * (baseR * 0.3);
          const ctrlY = cy + Math.sin(time * 1.2 + f) * (baseR * 0.3);

          ctx.moveTo(fx1, fy1);
          ctx.quadraticCurveTo(ctrlX, ctrlY, fx2, fy2);
          ctx.strokeStyle = f === 0 ? "rgba(201, 231, 80, 0.45)" : "rgba(35, 223, 188, 0.4)";
          ctx.lineWidth = 1.2;
          ctx.stroke();
          ctx.restore();
        }
      }

      // 5. Specular Bioluminescent Refraction
      ctx.save();
      ctx.beginPath();
      const specR = baseR * 0.32;
      ctx.ellipse(cx - baseR * 0.28, cy - baseR * 0.32, specR, specR * 0.55, -Math.PI / 4, 0, Math.PI * 2);
      const specGrad = ctx.createLinearGradient(
        cx - baseR * 0.45,
        cy - baseR * 0.45,
        cx - baseR * 0.1,
        cy - baseR * 0.1
      );
      specGrad.addColorStop(0, "rgba(255, 255, 255, 0.65)");
      specGrad.addColorStop(0.6, "rgba(201, 231, 80, 0.25)");
      specGrad.addColorStop(1, "rgba(255, 255, 255, 0)");
      ctx.fillStyle = specGrad;
      ctx.fill();
      ctx.restore();

      // 6. Ambient Bioluminescent Particle Embers
      if (!still && size >= 48) {
        particles.forEach((p) => {
          p.angle += p.speed * currentEnergy;
          p.pulse += 0.03;
          const currentR = p.radius + Math.sin(p.pulse) * 4;
          const px = cx + Math.cos(p.angle) * currentR;
          const py = cy + Math.sin(p.angle) * currentR;
          const alpha = p.opacity * (0.6 + Math.sin(p.pulse) * 0.4);

          ctx.beginPath();
          ctx.arc(px, py, p.size, 0, Math.PI * 2);
          ctx.fillStyle = mood === "speaking" || p.speed > 0.015
            ? `rgba(201, 231, 80, ${alpha * 0.8})`
            : `rgba(35, 223, 188, ${alpha})`;
          ctx.fill();
        });
      }

      ctx.restore();

      if (!still) {
        animFrameRef.current = requestAnimationFrame(render);
      }
    }

    render();

    return () => {
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [size, mood, still, level]);

  return (
    <View style={{ width: size, height: size, alignItems: "center", justifyContent: "center" }}>
      <canvas
        ref={canvasRef}
        style={{
          width: `${size}px`,
          height: `${size}px`,
          display: "block",
          pointerEvents: "none",
        }}
      />
    </View>
  );
}

// Native React Native multi-layered Animated implementation
function NativeBioluminescentOrb({ size = 40, mood = "idle", still = false, level = 0 }: OrbProps) {
  const t = useTokens();
  const pulse = useRef(new Animated.Value(0)).current;
  const audioLevel = Math.max(0, Math.min(1, level));
  const rotate = useRef(new Animated.Value(0)).current;
  const [reduceMotion, setReduceMotion] = useState(false);

  useEffect(() => {
    AccessibilityInfo.isReduceMotionEnabled().then(setReduceMotion).catch(() => undefined);
    const sub = AccessibilityInfo.addEventListener("reduceMotionChanged", setReduceMotion);
    return () => sub.remove();
  }, []);

  const moving = !still && !reduceMotion;

  useEffect(() => {
    if (!moving) {
      pulse.setValue(0);
      rotate.setValue(0);
      return;
    }

    const duration =
      mood === "thinking" ? 800 :
      mood === "speaking" ? 650 :
      mood === "listening" ? 1200 : 2800;

    const pulseAnim = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0, duration, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
      ])
    );

    const rotateAnim = Animated.loop(
      Animated.timing(rotate, { toValue: 1, duration: mood === "thinking" ? 3000 : 12000, easing: Easing.linear, useNativeDriver: true })
    );

    pulseAnim.start();
    rotateAnim.start();

    return () => {
      pulseAnim.stop();
      rotateAnim.stop();
    };
  }, [moving, mood, pulse, rotate]);

  const scale = pulse.interpolate({
    inputRange: [0, 1],
    outputRange: [1, mood === "thinking" ? 1.08 : mood === "speaking" ? 1.12 + audioLevel * 0.1 : 1.04],
  });

  const haloScale = pulse.interpolate({
    inputRange: [0, 1],
    outputRange: [1.06, mood === "speaking" ? 1.45 : 1.3],
  });

  const haloOpacity = pulse.interpolate({
    inputRange: [0, 1],
    outputRange: [0.2, mood === "speaking" ? 0.6 : 0.38],
  });

  const spin = rotate.interpolate({
    inputRange: [0, 1],
    outputRange: ["0deg", "360deg"],
  });

  const round = size / 2;

  // Bioluminescent multi-color gradient palette
  const colors: readonly [string, string, ...string[]] =
    mood === "error" || mood === "disconnected"
      ? ["#FF9999", "#FF6262", "#7A2020"]
      : mood === "speaking"
      ? ["#FFFFFF", "#C9E750", "#23DFBC", "#17844B"]
      : mood === "thinking"
      ? ["#FFFFFF", "#23DFBC", "#C9E750", "#17844B"]
      : mood === "listening"
      ? ["#E6FAF3", "#23DFBC", "#5AD14C", "#17844B"]
      : ["#F0FFF8", "#23DFBC", "#17844B", "#0E281F"];

  return (
    <View
      style={{ width: size, height: size, alignItems: "center", justifyContent: "center" }}
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
    >
      {/* Outer Atmospheric Bioluminescent Glow */}
      {moving ? (
        <Animated.View
          style={{
            position: "absolute",
            width: size,
            height: size,
            borderRadius: round,
            backgroundColor: mood === "speaking" ? "#C9E750" : t.glow,
            opacity: haloOpacity,
            transform: [{ scale: haloScale }],
          }}
        />
      ) : null}

      {/* Rotating Energy Ring */}
      {moving && size >= 48 ? (
        <Animated.View
          style={{
            position: "absolute",
            width: size * 0.94,
            height: size * 0.94,
            borderRadius: (size * 0.94) / 2,
            borderWidth: 1.5,
            borderColor: t.turquoise,
            borderStyle: "dashed",
            opacity: 0.45,
            transform: [{ rotate: spin }],
          }}
        />
      ) : null}

      {/* Fluid Dynamic Core */}
      <Animated.View
        style={{
          width: size * 0.84,
          height: size * 0.84,
          borderRadius: (size * 0.84) / 2,
          overflow: "hidden",
          transform: [{ scale }],
        }}
      >
        <LinearGradient
          colors={colors}
          start={{ x: 0.15, y: 0 }}
          end={{ x: 0.85, y: 1 }}
          style={{ flex: 1 }}
        />
        {/* Specular bioluminescent highlight */}
        <View
          style={{
            position: "absolute",
            top: size * 0.1,
            left: size * 0.16,
            width: size * 0.32,
            height: size * 0.18,
            borderRadius: size,
            backgroundColor: "rgba(255,255,255,0.55)",
            transform: [{ rotate: "-24deg" }],
          }}
        />
      </Animated.View>
    </View>
  );
}

export function Orb(props: OrbProps) {
  if (Platform.OS === "web") {
    return <WebBioluminescentOrb {...props} />;
  }
  return <NativeBioluminescentOrb {...props} />;
}
