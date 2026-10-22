import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme.dart';
import '../../state/providers.dart';

typedef MediaLoader = Future<Uint8List> Function(String kind, String file);

// Same rule as the core (kie_creative.resolve_media_file): a bare file name, never a path.
final _name = RegExp(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,100}\.[a-z0-9]{2,5}$');
final _suffix = {'image': RegExp(r'\.(png|jpe?g|webp)$', caseSensitive: false), 'video': RegExp(r'\.(mp4|webm|mov)$', caseSensitive: false)};

/// The attachment of an image_generate / video_generate call, or null when it is malformed.
Map<String, dynamic>? mediaDisplay(Object? display) {
  if (display is! Map) return null;
  final kind = display['kind'];
  final file = display['file'];
  if (kind != 'image' && kind != 'video') return null;
  if (display['media'] != (kind == 'image' ? 'images' : 'videos')) return null;
  if (file is! String || !_name.hasMatch(file) || !_suffix[kind]!.hasMatch(file)) return null;
  return display.cast<String, dynamic>();
}

// One download per file, shared by every rebuild and by re-opening a chat.
final _cache = <String, Future<Uint8List>>{};

/// A generated image inside Jarvis' answer: capped in height, tap to enlarge, placeholder when it cannot be
/// loaded. Videos have no player in the app (no video plugin) – they show as a card, playable in the Mac app.
class GeneratedMedia extends ConsumerStatefulWidget {
  const GeneratedMedia({super.key, required this.display, this.loader});
  final Map<String, dynamic> display;
  final MediaLoader? loader; // tests
  @override
  ConsumerState<GeneratedMedia> createState() => _GeneratedMediaState();
}

class _GeneratedMediaState extends ConsumerState<GeneratedMedia> {
  Future<Uint8List>? _bytes;

  String get _kind => widget.display['kind'] as String;
  String get _file => widget.display['file'] as String;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void didUpdateWidget(GeneratedMedia old) {
    super.didUpdateWidget(old);
    if (old.display['file'] != widget.display['file']) _load();
  }

  void _load() {
    if (_kind != 'image') return;
    final load = widget.loader ?? ref.read(apiProvider).media;
    final key = 'images/$_file';
    _bytes = _cache[key] ??= load('images', _file).catchError((Object e) {
      _cache.remove(key); // retried on the next build of this chat
      throw e;
    });
    if (_cache.length > 40) _cache.remove(_cache.keys.first);
  }

  @override
  Widget build(BuildContext context) {
    if (_kind == 'video') return _videoCard();
    return FutureBuilder<Uint8List>(
      future: _bytes,
      builder: (context, snap) {
        if (snap.hasError) return _placeholder();
        if (!snap.hasData) {
          return Container(
            height: 160,
            alignment: Alignment.center,
            decoration: _frame,
            child: const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 1.6, color: J.text3)),
          );
        }
        final prompt = widget.display['prompt']?.toString();
        return Semantics(
          button: true,
          label: prompt == null ? 'Erzeugtes Bild, antippen zum Vergrößern' : 'Erzeugtes Bild: $prompt, antippen zum Vergrößern',
          child: GestureDetector(
            onTap: () => _enlarge(snap.data!),
            child: Container(
              constraints: const BoxConstraints(maxHeight: 320),
              decoration: _frame,
              clipBehavior: Clip.antiAlias,
              child: Image.memory(snap.data!, fit: BoxFit.contain, gaplessPlayback: true, errorBuilder: (_, _, _) => _placeholder()),
            ),
          ),
        );
      },
    );
  }

  static final _frame = BoxDecoration(color: J.void_, border: Border.all(color: J.lineBright), borderRadius: BorderRadius.circular(J.radius + 2));

  Widget _placeholder() => Container(
        height: 120,
        decoration: BoxDecoration(border: Border.all(color: J.line), borderRadius: BorderRadius.circular(J.radius + 2)),
        alignment: Alignment.center,
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Icon(Icons.broken_image_outlined, size: 22, color: J.text3),
          const SizedBox(height: J.s1),
          Text('Bild konnte nicht geladen werden', style: J.mono(11, color: J.text3, spacing: 0)),
        ]),
      );

  Widget _videoCard() {
    final secs = widget.display['duration_s'];
    return Container(
      padding: const EdgeInsets.all(J.s3),
      decoration: _frame,
      child: Row(children: [
        const Icon(Icons.movie_outlined, size: 22, color: J.cyan),
        const SizedBox(width: J.s3),
        Expanded(
          child: Text(
            'Video erzeugt${secs is num ? ' (${secs.round()} s)' : ''} – abspielbar in der Mac-App',
            style: J.mono(11.5, color: J.text2, spacing: 0),
          ),
        ),
      ]),
    );
  }

  void _enlarge(Uint8List bytes) {
    showDialog<void>(
      context: context,
      barrierColor: J.void_.withValues(alpha: 0.92),
      builder: (ctx) => GestureDetector(
        onTap: () => Navigator.of(ctx).pop(),
        child: Semantics(
          label: 'Bild vergrößert, antippen zum Schließen',
          child: InteractiveViewer(maxScale: 5, child: Center(child: Image.memory(bytes, fit: BoxFit.contain))),
        ),
      ),
    );
  }
}
