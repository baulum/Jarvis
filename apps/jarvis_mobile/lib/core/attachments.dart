// Chat attachments: which files Sir may send. Same rules as the core (core/src/jarvis/agent/attachments.py) and
// the desktop (desktop/src/lib/attachments.ts) – the core checks everything again.

const maxAttachmentBytes = 20 * 1024 * 1024;
const maxAttachments = 8;
const supportedLabel = 'PNG, JPG, WEBP, GIF, PDF, TXT, MD, CSV, JSON, DOCX';

const imageExts = {'png', 'jpg', 'jpeg', 'webp', 'gif'};
const documentExts = {'pdf', 'txt', 'md', 'csv', 'json', 'docx'};
final allowedExts = {...imageExts, ...documentExts};

// a bare stored name, never a path (same rule as the core)
final _stored = RegExp(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,100}\.[a-z0-9]{2,5}$');

String extensionOf(String name) {
  final dot = name.lastIndexOf('.');
  return dot > 0 ? name.substring(dot + 1).toLowerCase() : '';
}

/// Last path part – a picker may hand over a full path.
String baseName(String path) => path.split(RegExp(r'[\\/]')).last;

/// 1,2 MB · 340 KB · 12 B
String formatBytes(int n) {
  if (n < 1024) return '$n B';
  if (n < 1024 * 1024) return '${(n / 1024).round()} KB';
  return '${(n / (1024 * 1024)).toStringAsFixed(1).replaceAll('.', ',')} MB';
}

/// Why a file cannot be attached, or null when it can.
String? rejectReason(String name, int size) {
  if (size <= 0) return '„$name“ ist leer.';
  if (size > maxAttachmentBytes) return '„$name“ ist ${formatBytes(size)} groß – erlaubt sind höchstens ${formatBytes(maxAttachmentBytes)}.';
  if (!allowedExts.contains(extensionOf(name))) return '„$name“: Dieser Dateityp wird nicht unterstützt. Erlaubt: $supportedLabel.';
  return null;
}

/// A stored attachment as the core returns it (and as the user message in the timeline carries it).
class Attachment {
  const Attachment({required this.file, required this.name, required this.kind, required this.size});

  factory Attachment.fromJson(Map<dynamic, dynamic> j) => Attachment(
        file: j['file'].toString(),
        name: j['name']?.toString() ?? '',
        kind: j['kind']?.toString() ?? 'document',
        size: (j['size'] as num?)?.toInt() ?? 0,
      );

  final String file; // stored name – identifies the file on the core
  final String name; // display name
  final String kind; // image | document
  final int size;

  bool get isImage => kind == 'image' && _stored.hasMatch(file) && imageExts.contains(extensionOf(file));

  Map<String, String> toRef() => {'file': file, 'name': name};

  /// The attachments of a user message in the timeline; malformed entries are dropped.
  static List<Attachment> fromData(Object? raw) {
    if (raw is! List) return const [];
    return [
      for (final a in raw)
        if (a is Map && a['file'] is String && _stored.hasMatch(a['file'] as String)) Attachment.fromJson(a),
    ];
  }
}

enum DraftState { uploading, done, failed }

/// A file in the message being written: upload starts when it is picked.
class DraftAttachment {
  const DraftAttachment({required this.id, required this.name, required this.size, this.bytes, this.state = DraftState.uploading, this.stored, this.error});
  final int id;
  final String name;
  final int size;
  final List<int>? bytes; // kept for a retry and for the thumbnail until the message is sent
  final DraftState state;
  final Attachment? stored;
  final String? error;

  bool get isImage => imageExts.contains(extensionOf(name));

  DraftAttachment copyWith({DraftState? state, Attachment? stored, String? error}) =>
      DraftAttachment(id: id, name: name, size: size, bytes: bytes, state: state ?? this.state, stored: stored ?? this.stored, error: error);
}
