import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/ui/widgets/generated_media.dart';

// 1×1 transparent PNG
final _png = Uint8List.fromList([
  0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52, 0x00, 0x00, 0x00, 0x01, //
  0x00, 0x00, 0x00, 0x01, 0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4, 0x89, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x44, 0x41,
  0x54, 0x78, 0x9C, 0x63, 0x00, 0x01, 0x00, 0x00, 0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00, 0x00, 0x00, 0x00, 0x49,
  0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
]);

Widget _app(Widget child) => ProviderScope(child: MaterialApp(home: Scaffold(body: child)));

void main() {
  test('only well-formed attachments are shown', () {
    expect(mediaDisplay({'kind': 'image', 'media': 'images', 'file': '20261022-102738-53c75e52.png'}), isNotNull);
    expect(mediaDisplay({'kind': 'video', 'media': 'videos', 'file': 'clip.mp4'}), isNotNull);
    expect(mediaDisplay({'kind': 'image', 'media': 'images', 'file': '../secret.png'}), isNull);
    expect(mediaDisplay({'kind': 'image', 'media': 'images', 'file': '/Users/paul/x.png'}), isNull);
    expect(mediaDisplay({'kind': 'image', 'media': 'videos', 'file': 'a.png'}), isNull);
    expect(mediaDisplay({'kind': 'route'}), isNull);
  });

  testWidgets('loads the image by file name and enlarges it on tap', (tester) async {
    final asked = <String>[];
    await tester.pumpWidget(_app(GeneratedMedia(
      display: const {'kind': 'image', 'media': 'images', 'file': 'img-1.png', 'prompt': 'Leuchtturm'},
      loader: (kind, file) async {
        asked.add('$kind/$file');
        return _png;
      },
    )));
    await tester.pumpAndSettle();
    expect(asked, ['images/img-1.png']);
    expect(find.byType(Image), findsOneWidget);
    await tester.tap(find.byType(Image));
    await tester.pumpAndSettle();
    expect(find.byType(InteractiveViewer), findsOneWidget);
  });

  testWidgets('shows a placeholder when loading fails', (tester) async {
    await tester.pumpWidget(_app(GeneratedMedia(
      display: const {'kind': 'image', 'media': 'images', 'file': 'missing-1.png'},
      loader: (_, _) async => throw Exception('404'),
    )));
    await tester.pumpAndSettle();
    expect(find.text('Bild konnte nicht geladen werden'), findsOneWidget);
  });

  testWidgets('a video shows as a card', (tester) async {
    await tester.pumpWidget(_app(const GeneratedMedia(display: {'kind': 'video', 'media': 'videos', 'file': 'clip.mp4', 'duration_s': 5})));
    expect(find.textContaining('Video erzeugt (5 s)'), findsOneWidget);
  });
}
