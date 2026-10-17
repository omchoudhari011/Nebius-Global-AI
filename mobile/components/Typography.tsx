// Text and TextInput in the app's typeface. Custom fonts on Android ignore `fontWeight`, so a
// weight is turned into the matching Inter file; a style that names its own font (an icon, a
// Cormorant heading) is left alone.

import { forwardRef } from "react";
import {
  StyleSheet,
  Text as RNText,
  TextInput as RNTextInput,
  type TextInputProps,
  type TextProps,
  type TextStyle,
} from "react-native";

import { fonts } from "../lib/tokens";

const BY_WEIGHT: Record<string, string> = {
  "700": fonts.bodyBold, bold: fonts.bodyBold, "800": fonts.bodyBold, "900": fonts.bodyBold,
  "600": fonts.bodySemibold, "500": fonts.bodyMedium,
};

function withFont(style: TextProps["style"]): TextProps["style"] {
  const flat = (StyleSheet.flatten(style) ?? {}) as TextStyle;
  if (flat.fontFamily) return style;
  const family = BY_WEIGHT[String(flat.fontWeight ?? "")] ?? fonts.body;
  return [style, { fontFamily: family, fontWeight: undefined }];
}

export const Text = forwardRef<RNText, TextProps>(function Text({ style, ...props }, ref) {
  return <RNText ref={ref} {...props} style={withFont(style)} />;
});

export const TextInput = forwardRef<RNTextInput, TextInputProps>(function TextInput({ style, ...props }, ref) {
  return <RNTextInput ref={ref} {...props} style={withFont(style as TextProps["style"]) as TextInputProps["style"]} />;
});
