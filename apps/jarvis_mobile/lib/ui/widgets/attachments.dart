import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/attachments.dart';
import '../../core/theme.dart';
import '../../state/providers.dart';
import 'hud.dart';

typedef AttachmentLoader = Future<Uint8List> Function(String file);

// One download per file, shared by every rebuild and by re-opening a chat.
final _cache = <String, Future<Uint8List>>{};

Future<Uint8List> _load(String file, AttachmentLoader load) {
  final f = _cache[file] ??= load(file).catchError((Object e) {
    _cache.remove(file); // retried on the next build of this chat
    throw e;
  });
  if (_cache.length > 40) _cache.remove(_cache.keys.first);
  return f;
}

/// What the paper clip offers.
enum AttachSource { camera, gallery, files }

Future<AttachSource?> pickAttachSource(BuildContext context) => showHudSheet<AttachSource>(
      context,
      scroll: false,
      builder: (ctx) => Padding(
        padding: const EdgeInsets.fromLTRB(J.s2, J.s4, J.s2, J.s5),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          const Padding(padding: EdgeInsets.fromLTRB(J.s4, 0, J.s4, J.s2), child: Caps('Anhängen')),
          _SourceRow(icon: Icons.photo_camera_outlined, label: 'Foto aufnehmen', onTap: () => Navigator.pop(ctx, AttachSource.camera)),
          _SourceRow(icon: Icons.photo_library_outlined, label: 'Aus der Galerie', onTap: () => Navigator.pop(ctx, AttachSource.gallery)),
          _SourceRow(icon: Icons.description_outlined, label: 'Datei wählen', hint: 'PDF, Word, Text, CSV …', onTap: () => Navigator.pop(ctx, AttachSource.files)),
        ]),
      ),
    );

class _SourceRow extends StatelessWidget {
  const _SourceRow({required this.icon, required this.label, required this.onTap, this.hint});
  final IconData icon;
  final String label;
  final String? hint;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) => Pressable(
        onTap: onTap,
        semantics: label,
        child: Container(
          constraints: const BoxConstraints(minHeight: 56),
          padding: const EdgeInsets.symmetric(horizontal: J.s4, vertical: J.s3),
          child: Row(children: [
            Icon(icon, color: J.cyan, size: 22),
            const SizedBox(width: J.s4),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
                Text(label, style: J.body),
                if (hint != null) Text(hint!, style: J.mono(11, color: J.text3, spacing: 0)),
              ]),
            ),
          ]),
        ),
      );
}

/// Small square that says what a file is: the image itself, or its type.
class _Thumb extends StatelessWidget {
  const _Thumb({required this.name, this.bytes, this.size = 40});
  final String name;
  final Uint8List? bytes;
  final double size;
  @override
  Widget build(BuildContext context) {
    final ext = extensionOf(name).toUpperCase();
    return ClipRRect(
      borderRadius: BorderRadius.circular(6),
      child: SizedBox.square(
        dimension: size,
        child: bytes != null
            ? Image.memory(bytes!, fit: BoxFit.cover, cacheWidth: (size * 3).round(), gaplessPlayback: true, errorBuilder: (_, _, _) => _label(ext))
            : _label(ext),
      ),
    );
  }

  Widget _label(String ext) => DecoratedBox(
        decoration: BoxDecoration(border: Border.all(color: J.lineBright), borderRadius: BorderRadius.circular(6)),
        child: Center(child: Text(ext.isEmpty ? 'DATEI' : ext, style: J.mono(9.5, color: J.text2, spacing: 0))),
      );
}

/// The files of the message being written: a chip each, with upload state and a remove button.
class AttachmentTray extends StatelessWidget {
  const AttachmentTray({super.key, required this.items, required this.onRemove, required this.onRetry});
  final List<DraftAttachment> items;
  final void Function(int id) onRemove;
  final void Function(int id) onRetry;

  @override
  Widget build(BuildContext context) {
    if (items.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: J.s2),
      child: Semantics(
        label: 'Anhänge dieser Nachricht',
        child: SizedBox(
          height: 56,
          child: ListView.separated(
            scrollDirection: Axis.horizontal,
            itemCount: items.length,
            separatorBuilder: (_, _) => const SizedBox(width: J.s2),
            itemBuilder: (_, i) => _Chip(item: items[i], onRemove: () => onRemove(items[i].id), onRetry: () => onRetry(items[i].id)),
          ),
        ),
      ),
    );
  }
}

class _Chip extends StatelessWidget {
  const _Chip({required this.item, required this.onRemove, required this.onRetry});
  final DraftAttachment item;
  final VoidCallback onRemove;
  final VoidCallback onRetry;
  @override
  Widget build(BuildContext context) {
    final failed = item.state == DraftState.failed;
    return Container(
      constraints: const BoxConstraints(maxWidth: 230),
      padding: const EdgeInsets.fromLTRB(6, 6, 2, 6),
      decoration: BoxDecoration(
        color: J.panel.withValues(alpha: 0.92),
        border: Border.all(color: failed ? J.red.withValues(alpha: 0.7) : J.lineBright),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Stack(alignment: Alignment.center, children: [
          Opacity(
            opacity: item.state == DraftState.done ? 1 : 0.45,
            child: _Thumb(name: item.name, bytes: item.isImage && item.bytes != null ? Uint8List.fromList(item.bytes!) : null),
          ),
          if (item.state == DraftState.uploading)
            const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 1.6, color: J.cyan)),
        ]),
        const SizedBox(width: J.s2),
        Flexible(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
            Text(item.name, maxLines: 1, overflow: TextOverflow.ellipsis, style: J.body.copyWith(fontSize: 13)),
            Text(
              failed ? (item.error ?? 'Fehlgeschlagen') : item.state == DraftState.uploading ? 'Lädt hoch …' : formatBytes(item.size),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: J.mono(10.5, color: failed ? J.red : J.text3, spacing: 0),
            ),
          ]),
        ),
        if (failed)
          Pressable(onTap: onRetry, semantics: 'Erneut hochladen', child: const SizedBox.square(dimension: 36, child: Icon(Icons.refresh_rounded, size: 18, color: J.amber))),
        Pressable(onTap: onRemove, semantics: '${item.name} entfernen', child: const SizedBox.square(dimension: 36, child: Icon(Icons.close_rounded, size: 18, color: J.text2))),
      ]),
    );
  }
}

/// The files of a sent message inside Sir's bubble: image thumbnails (tap to enlarge) and document chips.
class MessageAttachments extends ConsumerWidget {
  const MessageAttachments({super.key, required this.items, this.loader});
  final List<Attachment> items;
  final AttachmentLoader? loader; // tests
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    if (items.isEmpty) return const SizedBox.shrink();
    final load = loader ?? (String f) => ref.read(apiProvider).media('attachments', f);
    final images = items.where((a) => a.isImage).toList();
    final docs = items.where((a) => !a.isImage).toList();
    return Column(crossAxisAlignment: CrossAxisAlignment.end, mainAxisSize: MainAxisSize.min, children: [
      if (images.isNotEmpty)
        Wrap(
          spacing: J.s2,
          runSpacing: J.s2,
          alignment: WrapAlignment.end,
          children: [for (final a in images) _MessageImage(attachment: a, single: images.length == 1, load: load)],
        ),
      if (images.isNotEmpty && docs.isNotEmpty) const SizedBox(height: J.s2),
      for (final a in docs)
        Padding(
          padding: const EdgeInsets.only(top: 2),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            _Thumb(name: a.name, size: 32),
            const SizedBox(width: J.s2),
            Flexible(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
                Text(a.name, maxLines: 1, overflow: TextOverflow.ellipsis, style: J.body.copyWith(fontSize: 13, color: J.ice)),
                if (a.size > 0) Text(formatBytes(a.size), style: J.mono(10.5, color: J.text3, spacing: 0)),
              ]),
            ),
          ]),
        ),
    ]);
  }
}

class _MessageImage extends StatelessWidget {
  const _MessageImage({required this.attachment, required this.single, required this.load});
  final Attachment attachment;
  final bool single;
  final AttachmentLoader load;
  @override
  Widget build(BuildContext context) {
    final side = single ? 220.0 : 104.0;
    return FutureBuilder<Uint8List>(
      future: _load(attachment.file, load),
      builder: (context, snap) {
        Widget box(Widget child) => Container(
              width: side,
              height: single ? null : side,
              constraints: single ? const BoxConstraints(maxHeight: 280, minHeight: 96) : null,
              decoration: BoxDecoration(color: J.void_, border: Border.all(color: J.lineBright), borderRadius: BorderRadius.circular(10)),
              clipBehavior: Clip.antiAlias,
              alignment: Alignment.center,
              child: child,
            );
        if (snap.hasError) {
          return box(Column(mainAxisSize: MainAxisSize.min, children: [
            const Icon(Icons.broken_image_outlined, size: 20, color: J.text3),
            const SizedBox(height: 4),
            Text(attachment.name, maxLines: 1, overflow: TextOverflow.ellipsis, style: J.mono(10, color: J.text3, spacing: 0)),
          ]));
        }
        if (!snap.hasData) {
          return box(const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 1.6, color: J.text3)));
        }
        final bytes = snap.data!;
        return Semantics(
          button: true,
          label: 'Bild ${attachment.name}, antippen zum Vergrößern',
          child: GestureDetector(
            onTap: () => _enlarge(context, bytes),
            child: box(Image.memory(bytes, fit: single ? BoxFit.contain : BoxFit.cover, width: side, height: single ? null : side, cacheWidth: 660, gaplessPlayback: true)),
          ),
        );
      },
    );
  }

  void _enlarge(BuildContext context, Uint8List bytes) {
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
