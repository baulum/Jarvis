import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/api.dart';
import 'package:jarvis_mobile/core/attachments.dart';
import 'package:jarvis_mobile/core/live.dart';
import 'package:jarvis_mobile/core/native.dart';
import 'package:jarvis_mobile/state/chat.dart';
import 'package:jarvis_mobile/state/providers.dart';
import 'package:jarvis_mobile/state/timeline.dart';
import 'package:jarvis_mobile/ui/widgets/attachments.dart';

// 1×1 PNG
final _png = Uint8List.fromList([
  0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52, 0x00, 0x00, 0x00, 0x01, //
  0x00, 0x00, 0x00, 0x01, 0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4, 0x89, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x44, 0x41,
  0x54, 0x78, 0x9C, 0x63, 0x00, 0x01, 0x00, 0x00, 0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00, 0x00, 0x00, 0x00, 0x49,
  0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
]);

class _Api extends Api {
  _Api() : super(NativeBridge());
  final sent = <Map<String, dynamic>>[];
  @override
  Future<dynamic> post(String path, [Object? body]) async {
    sent.add((body as Map).cast<String, dynamic>());
    return {'session_id': 'S', 'accepted': true};
  }

  @override
  Future<WebSocket> socket(String path) => throw UnimplementedError();
}

class _Socket extends LiveSocket {
  _Socket(super.api) {
    current = Link.online;
  }
  @override
  Stream<CoreEvent> get events => const Stream.empty();
}

void main() {
  test('the same limits as the core', () {
    expect(rejectReason('rechnung.pdf', 1200), isNull);
    expect(rejectReason('IMG_1.JPG', 1200), isNull);
    expect(rejectReason('leer.pdf', 0), contains('leer'));
    expect(rejectReason('video.mp4', 1200), contains('nicht unterstützt'));
    expect(rejectReason('programm.exe', 1200), contains('nicht unterstützt'));
    expect(rejectReason('gross.pdf', maxAttachmentBytes + 1), contains('groß'));
    expect(rejectReason('ohneendung', 10), contains('nicht unterstützt'));
    expect(formatBytes(340 * 1024), '340 KB');
    expect(formatBytes((1.2 * 1024 * 1024).round()), '1,2 MB');
    expect(baseName('/storage/emulated/0/Download/a.pdf'), 'a.pdf');
  });

  test('only well-formed attachments of a message are shown', () {
    final got = Attachment.fromData([
      {'file': '20261022-141500-1a2b3c4d-foto.jpg', 'name': 'foto.jpg', 'kind': 'image', 'size': 5},
      {'file': '../geheim.png', 'name': 'x.png', 'kind': 'image', 'size': 1},
      {'file': '/etc/passwd', 'name': 'p', 'kind': 'document'},
      'quatsch',
    ]);
    expect(got.map((a) => a.file), ['20261022-141500-1a2b3c4d-foto.jpg']);
    expect(got.single.isImage, isTrue);
    expect(Attachment.fromData(null), isEmpty);
    // a "document" that is called .png is not loaded as image
    expect(Attachment.fromJson({'file': 'a-b.pdf', 'name': 'a.pdf', 'kind': 'image'}).isImage, isFalse);
  });

  test('send: the message names the stored files; attachments only is allowed, nothing at all is not', () async {
    final api = _Api();
    final c = ProviderContainer(overrides: [apiProvider.overrideWithValue(api), liveSocketProvider.overrideWithValue(_Socket(api))]);
    addTearDown(c.dispose);
    final chat = c.read(chatProvider.notifier);
    await chat.send('   ');
    expect(api.sent, isEmpty);

    const foto = Attachment(file: '20261022-141500-1a2b3c4d-foto.jpg', name: 'foto.jpg', kind: 'image', size: 5);
    await chat.send('', files: [foto]);
    expect(api.sent.single['message'], '');
    expect(api.sent.single['channel'], 'mobile');
    expect(api.sent.single['attachments'], [
      {'file': '20261022-141500-1a2b3c4d-foto.jpg', 'name': 'foto.jpg'},
    ]);
    final user = c.read(chatProvider).timeline.items.single;
    expect(user.local, isTrue);
    expect(Attachment.fromData(user.data['attachments']).single.name, 'foto.jpg');
  });

  test("the core's copy replaces the optimistic message with attachments", () {
    final t = addOptimisticUser(const Timeline(), 'S', '', attachments: [
      {'file': 'a-b.pdf', 'name': 'a.pdf', 'kind': 'document', 'size': 1},
    ]);
    final core = TItem.fromJson({
      'id': 'u1', 'session_id': 'S', 'seq': 1, 'ts': 1, 'kind': 'message', //
      'data': {'role': 'user', 'text': '', 'status': 'done', 'attachments': [{'file': 'a-b.pdf', 'name': 'a.pdf', 'kind': 'document', 'size': 1}]},
    });
    final merged = upsertItem(t.items, core);
    expect(merged, hasLength(1));
    expect(merged.single.local, isFalse);
  });

  testWidgets('a sent message shows the photo and the document', (tester) async {
    final asked = <String>[];
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: ProviderScope(
          child: MessageAttachments(
            items: const [
              Attachment(file: 'foto-1.png', name: 'foto.png', kind: 'image', size: 70),
              Attachment(file: 'rechnung.pdf', name: 'Rechnung Müller.pdf', kind: 'document', size: 340 * 1024),
            ],
            loader: (f) async {
              asked.add(f);
              return _png;
            },
          ),
        ),
      ),
    ));
    await tester.pumpAndSettle();
    expect(asked, ['foto-1.png']); // documents are not downloaded
    expect(find.byType(Image), findsOneWidget);
    expect(find.text('Rechnung Müller.pdf'), findsOneWidget);
    expect(find.text('340 KB'), findsOneWidget);
    await tester.tap(find.byType(GestureDetector).first);
    await tester.pumpAndSettle();
    expect(find.byType(InteractiveViewer), findsOneWidget);
  });

  testWidgets('the tray shows state, retry and remove', (tester) async {
    final removed = <int>[], retried = <int>[];
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: AttachmentTray(
          items: const [
            DraftAttachment(id: 1, name: 'a.pdf', size: 2048, state: DraftState.uploading),
            DraftAttachment(id: 2, name: 'b.pdf', size: 2048, state: DraftState.failed, error: 'Zu groß'),
            DraftAttachment(id: 3, name: 'c.pdf', size: 2048, state: DraftState.done),
          ],
          onRemove: removed.add,
          onRetry: retried.add,
        ),
      ),
    ));
    expect(find.text('Lädt hoch …'), findsOneWidget);
    expect(find.text('Zu groß'), findsOneWidget);
    expect(find.text('2 KB'), findsOneWidget);
    await tester.tap(find.byIcon(Icons.refresh_rounded));
    await tester.tap(find.byIcon(Icons.close_rounded).last);
    expect(retried, [2]);
    expect(removed, [3]);
  });
}
