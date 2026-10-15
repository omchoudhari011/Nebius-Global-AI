// Files and speech: pick a file to upload, and read replies aloud. (Recording a voice
// message lives in components/VoiceButton.tsx, because expo-audio's recorder is a hook.)
//
// The web build reads a picked file with FileReader; phones read it with expo-file-system.
// Speech uses the device's own voice (expo-speech: the system TTS on phones, the browser's
// speechSynthesis on the web), so replies read aloud never leave the device.

import * as DocumentPicker from "expo-document-picker";
import { File } from "expo-file-system";
import * as Speech from "expo-speech";
import { Platform } from "react-native";

export const UPLOAD_TYPES = ["application/pdf", "text/*", "image/png", "image/jpeg", "image/webp", "application/json"];
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

export type PickedFile = { name: string; mimeType: string; base64: string; size: number };

function readAsBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
    reader.onerror = () => reject(reader.error ?? new Error("couldn't read the file"));
    reader.readAsDataURL(blob);
  });
}

/** Lets the user choose a PDF, text file or photo; null if they cancelled. */
export async function pickFile(): Promise<PickedFile | null> {
  const result = await DocumentPicker.getDocumentAsync({ type: UPLOAD_TYPES, copyToCacheDirectory: true, multiple: false });
  if (result.canceled || !result.assets?.length) return null;
  const asset = result.assets[0];
  const size = asset.size ?? 0;
  if (size > MAX_UPLOAD_BYTES) throw new Error("Files can be up to 10 MB.");
  const base64 =
    Platform.OS === "web" && asset.file ? await readAsBase64(asset.file) : await new File(asset.uri).base64();
  return { name: asset.name, mimeType: asset.mimeType ?? "application/octet-stream", base64, size };
}

let speakingKey: string | null = null;

/** Reads `text` aloud, or stops if this same message is already being read. Returns whether it started. */
export function toggleSpeech(key: string, text: string, onDone: () => void): boolean {
  Speech.stop();
  if (speakingKey === key) {
    speakingKey = null;
    onDone();
    return false;
  }
  speakingKey = key;
  const finish = () => {
    if (speakingKey === key) speakingKey = null;
    onDone();
  };
  // Leave out the check notes' prefix so it reads naturally.
  Speech.speak(text.replace(/Custodian check: /g, "Note: "), { onDone: finish, onStopped: finish, onError: finish });
  return true;
}

export function stopSpeech(): void {
  speakingKey = null;
  Speech.stop();
}
