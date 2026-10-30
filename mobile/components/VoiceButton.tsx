// The microphone button: press to record a voice message, press again to stop; the server's
// local speech-to-text turns it into text in the message box, for you to check and send.
//
// Web records with the browser's MediaRecorder; phones with expo-audio. Shown only when the
// server has speech-to-text set up (WHISPER_URL).

import Ionicons from "@expo/vector-icons/Ionicons";
import { AudioModule, RecordingPresets, setAudioModeAsync, useAudioRecorder } from "expo-audio";
import { File } from "expo-file-system";
import { LinearGradient } from "expo-linear-gradient";
import { useRef, useState } from "react";
import { ActivityIndicator, Platform, Pressable } from "react-native";

import * as api from "../lib/api";
import { softShadow, useTokens } from "../lib/tokens";

type Props = { disabled?: boolean; onText: (text: string) => void; onError: (message: string) => void };

const styles = { width: 44, height: 44, borderRadius: 22, alignItems: "center", justifyContent: "center" } as const;

function WebVoiceButton({ disabled, onText, onError }: Props) {
  const t = useTokens();
  const [state, setState] = useState<"idle" | "recording" | "working">("idle");
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);

  async function press() {
    if (state === "recording") {
      recorder.current?.stop();
      return;
    }
    if (state !== "idle") return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const media = new MediaRecorder(stream);
      chunks.current = [];
      media.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
      media.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        setState("working");
        try {
          const blob = new Blob(chunks.current, { type: media.mimeType || "audio/webm" });
          onText(await api.transcribe(blob, blob.type));
        } catch (err) {
          onError(api.errorMessage(err));
        } finally {
          setState("idle");
        }
      };
      recorder.current = media;
      media.start();
      setState("recording");
    } catch {
      onError("Custodian couldn't use the microphone. Allow microphone access for this site and try again.");
    }
  }

  return <Mic state={state} disabled={disabled} onPress={press} color={state === "recording" ? t.turquoise : t.inkSoft} />;
}

function NativeVoiceButton({ disabled, onText, onError }: Props) {
  const t = useTokens();
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const [state, setState] = useState<"idle" | "recording" | "working">("idle");

  async function press() {
    if (state === "working") return;
    if (state === "recording") {
      setState("working");
      try {
        await recorder.stop();
        if (!recorder.uri) throw new Error("No recording was made.");
        const bytes = await new File(recorder.uri).arrayBuffer();
        onText(await api.transcribe(bytes, "audio/mp4"));
      } catch (err) {
        onError(api.errorMessage(err));
      } finally {
        setState("idle");
      }
      return;
    }
    const permission = await AudioModule.requestRecordingPermissionsAsync();
    if (!permission.granted) {
      onError("Custodian needs microphone access for voice messages. Allow it in Settings.");
      return;
    }
    await setAudioModeAsync({ playsInSilentMode: true, allowsRecording: true });
    await recorder.prepareToRecordAsync();
    recorder.record();
    setState("recording");
  }

  return <Mic state={state} disabled={disabled} onPress={press} color={state === "recording" ? t.turquoise : t.inkSoft} />;
}

function Mic({ state, disabled, onPress, color }: { state: string; disabled?: boolean; onPress: () => void; color: string }) {
  const t = useTokens();
  const label = state === "recording" ? "Stop recording" : state === "working" ? "Transcribing" : "Record a voice message";
  const recording = state === "recording";
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled: !!disabled, busy: state === "working" }}
      disabled={disabled}
      onPress={onPress}
      style={[
        styles,
        {
          overflow: "hidden",
          ...softShadow(t, 2),
        },
        disabled && { opacity: 0.4 },
      ]}
    >
      <LinearGradient
        colors={recording ? t.gradients.gaugeRed : t.gradients.card}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={{ ...styles, position: "absolute", top: 0, left: 0 }}
      />
      {state === "working" ? (
        <ActivityIndicator size="small" color={t.accent} />
      ) : (
        <Ionicons name={recording ? "radio" : "mic-outline"} size={22} color={recording ? "#FFFFFF" : color} />
      )}
    </Pressable>
  );
}

export function VoiceButton(props: Props) {
  return Platform.OS === "web" ? <WebVoiceButton {...props} /> : <NativeVoiceButton {...props} />;
}

