import 'package:flutter/material.dart';

/// J.A.R.V.I.S. design tokens. One system for the whole app: HUD hairlines instead of cards,
/// light as the only ornament, mono for telemetry.
abstract final class J {
  // ---------------------------------------------------------------- color
  static const void_ = Color(0xFF020407);
  static const abyss = Color(0xFF060B10);
  static const panel = Color(0xFF0A1219);
  static const line = Color(0xFF13232D);
  static const lineBright = Color(0xFF1F3946);
  static const ice = Color(0xFFCFF4FF);
  static const cyan = Color(0xFF5CE1FF);
  static const cyanDeep = Color(0xFF1FA3C7);
  static const amber = Color(0xFFFFB547);
  static const red = Color(0xFFFF5A5F);
  static const green = Color(0xFF6EF0B4);
  static const text = Color(0xFFE6F7FF);
  static const text2 = Color(0xFF86A9B8);
  static const text3 = Color(0xFF4A6573);

  // ---------------------------------------------------------------- space / shape
  static const s1 = 4.0, s2 = 8.0, s3 = 12.0, s4 = 16.0, s5 = 24.0, s6 = 32.0, s7 = 48.0;
  static const radius = 6.0;
  static const hit = 48.0; // minimum touch target

  // ---------------------------------------------------------------- motion
  static const quick = Duration(milliseconds: 140);
  static const base = Duration(milliseconds: 240);
  static const slow = Duration(milliseconds: 460);
  static const cinematic = Duration(milliseconds: 1600);
  static const enter = Curves.easeOutCubic;
  static const exit = Curves.easeInCubic;
  static const settle = Curves.easeOutBack;

  // ---------------------------------------------------------------- type
  static TextStyle _saira(double size, double weight, {Color color = text, double spacing = 0, double height = 1.3}) =>
      TextStyle(
        fontFamily: 'Saira',
        fontSize: size,
        fontVariations: [FontVariation('wght', weight)],
        color: color,
        letterSpacing: spacing,
        height: height,
      );

  static TextStyle mono(double size, {Color color = text2, double weight = 420, double spacing = 0.6}) => TextStyle(
        fontFamily: 'JetBrainsMono',
        fontSize: size,
        fontVariations: [FontVariation('wght', weight)],
        color: color,
        letterSpacing: spacing,
        height: 1.35,
      );

  static final wordmark = _saira(15, 600, color: ice, spacing: 6.5, height: 1);
  static final display = _saira(30, 300, spacing: -0.4, height: 1.1);
  static final title = _saira(20, 500, spacing: 0.2);
  static final body = _saira(16, 400, height: 1.45);
  static final bodyDim = _saira(15, 400, color: text2, height: 1.45);
  static final label = _saira(13, 500, color: text2, spacing: 0.4);
  static final caps = mono(10.5, color: text3, spacing: 2.2, weight: 500);

  static ThemeData theme() {
    final base = ThemeData(brightness: Brightness.dark, useMaterial3: true);
    return base.copyWith(
      scaffoldBackgroundColor: void_,
      colorScheme: const ColorScheme.dark(
        surface: void_,
        primary: cyan,
        secondary: ice,
        error: red,
        onSurface: text,
      ),
      textTheme: base.textTheme.apply(fontFamily: 'Saira', bodyColor: text, displayColor: text),
      splashFactory: NoSplash.splashFactory,
      highlightColor: Colors.transparent,
      textSelectionTheme: const TextSelectionThemeData(
        cursorColor: cyan,
        selectionColor: Color(0x445CE1FF),
        selectionHandleColor: cyan,
      ),
      pageTransitionsTheme: const PageTransitionsTheme(builders: {
        TargetPlatform.android: FadeForwardsPageTransitionsBuilder(),
      }),
    );
  }
}
