import 'dart:math' as math;

/// Chat timeline: the conversation as one list of items ordered by `seq` (core/src/jarvis/agent/timeline.py,
/// same rules as desktop/src/lib/timeline.ts). Pure functions, no Flutter – the controller folds events in,
/// the chat screen turns the list into rows.
class TItem {
  TItem({
    required this.id,
    required this.sessionId,
    required this.seq,
    required this.ts,
    required this.kind,
    required this.data,
    this.turnId,
    this.local = false,
    String? key,
  }) : key = key ?? id;

  factory TItem.fromJson(Map<String, dynamic> j) => TItem(
        id: j['id'].toString(),
        sessionId: j['session_id']?.toString() ?? '',
        turnId: j['turn_id']?.toString(),
        seq: (j['seq'] as num?)?.toInt() ?? 0,
        ts: (j['ts'] as num?)?.toInt() ?? 0,
        kind: j['kind']?.toString() ?? 'message',
        data: ((j['data'] as Map?) ?? const {}).cast<String, dynamic>(),
      );

  final String id;
  final String sessionId;
  final String? turnId;
  final int seq;
  final int ts; // creation time, ms since epoch
  final String kind; // message | tool | process | turn
  final Map<String, dynamic> data;
  final bool local; // optimistic, not yet confirmed by the core

  /// widget key: stays the same when the core's copy replaces an optimistic item (no re-mount)
  final String key;

  String get role => data['role']?.toString() ?? '';
  String get text => data['text']?.toString() ?? '';
  String get status => data['status']?.toString() ?? '';
  String get name => data['name']?.toString() ?? '';
  String get step => data['step']?.toString() ?? '';
  String get source => data['source']?.toString() ?? '';
  int? intField(String k) => (data[k] as num?)?.toInt();

  bool get isMessage => kind == 'message';
  bool get isTool => kind == 'tool';
  bool get isProcess => kind == 'process';
  bool get isTurn => kind == 'turn';

  TItem copyWith({Map<String, dynamic>? data, String? key, int? seq}) => TItem(
        id: id,
        sessionId: sessionId,
        turnId: turnId,
        seq: seq ?? this.seq,
        ts: ts,
        kind: kind,
        data: data ?? this.data,
        local: local,
        key: key ?? this.key,
      );

  /// task-list state stored on the message (`index -> checked`)
  Map<String, bool> get checks =>
      ((data['checks'] as Map?) ?? const {}).map((k, v) => MapEntry(k.toString(), v == true));
}

class Timeline {
  const Timeline({this.items = const [], this.running = false, this.hasMore = false, this.loaded = false});
  final List<TItem> items;
  final bool running;
  final bool hasMore; // older turns exist on the core
  final bool loaded;

  Timeline copy({List<TItem>? items, bool? running, bool? hasMore, bool? loaded}) => Timeline(
        items: items ?? this.items,
        running: running ?? this.running,
        hasMore: hasMore ?? this.hasMore,
        loaded: loaded ?? this.loaded,
      );
}

int _localSeq = 0;

/// Optimistic message: sorts after everything the core has sent until the core's copy replaces it.
Timeline addOptimisticUser(Timeline t, String sessionId, String text, {List<Map<String, dynamic>> attachments = const []}) {
  _localSeq++;
  final id = 'local-${DateTime.now().millisecondsSinceEpoch}-$_localSeq';
  final item = TItem(
    id: id,
    sessionId: sessionId,
    seq: 1 << 52 | _localSeq,
    ts: DateTime.now().millisecondsSinceEpoch,
    kind: 'message',
    data: {'role': 'user', 'text': text, 'status': 'done', if (attachments.isNotEmpty) 'attachments': attachments},
    local: true,
  );
  return t.copy(items: [...t.items, item], running: true);
}

/// The message went to another conversation than the one shown (new, or the old one had expired on the core):
/// only the optimistic message comes along – `seq` is per conversation, the old items do not belong in this order.
Timeline startSession(Timeline t) => Timeline(items: [for (final e in t.items) if (e.local) e], running: true);

List<TItem> _insertSorted(List<TItem> items, TItem item) {
  var i = items.length;
  while (i > 0 && items[i - 1].seq > item.seq) {
    i--;
  }
  return [...items.sublist(0, i), item, ...items.sublist(i)];
}

/// Insert or replace one item; the order is by `seq` only, never by arrival.
List<TItem> upsertItem(List<TItem> items, TItem incoming) {
  final idx = items.indexWhere((e) => e.id == incoming.id);
  if (idx >= 0) {
    final cur = items[idx];
    var data = incoming.data;
    // a snapshot of a still-streaming segment may lag behind the deltas already applied here
    final streaming = incoming.status == 'streaming' || incoming.status == 'running';
    if (streaming && (cur.isMessage || cur.isProcess) && cur.text.length > incoming.text.length) {
      data = {...incoming.data, 'text': cur.text};
    }
    final next = incoming.copyWith(data: data, key: cur.key);
    final rest = [...items]..removeAt(idx);
    if (next.seq == cur.seq) return [...rest.sublist(0, idx), next, ...rest.sublist(idx)];
    return _insertSorted(rest, next);
  }
  if (incoming.isMessage && incoming.role == 'user') {
    final local = items.indexWhere((e) => e.local && e.isMessage && e.role == 'user' && e.text == incoming.text);
    if (local >= 0) {
      final key = items[local].key;
      return _insertSorted([...items]..removeAt(local), incoming.copyWith(key: key));
    }
  }
  return _insertSorted(items, incoming);
}

/// Appends streamed text to a message segment or a reasoning block; unknown ids are ignored.
List<TItem> appendText(List<TItem> items, String id, String text) {
  final idx = items.indexWhere((e) => e.id == id);
  if (idx < 0 || text.isEmpty) return items;
  final cur = items[idx];
  if (!cur.isMessage && !cur.isProcess) return items;
  final out = [...items];
  out[idx] = cur.copyWith(data: {...cur.data, 'text': cur.text + text});
  return out;
}

Timeline setCheck(Timeline t, String id, int index, bool checked) {
  final idx = t.items.indexWhere((e) => e.id == id);
  if (idx < 0 || !t.items[idx].isMessage) return t;
  final cur = t.items[idx];
  final out = [...t.items];
  out[idx] = cur.copyWith(data: {
    ...cur.data,
    'checks': {...cur.checks, '$index': checked},
  });
  return t.copy(items: out);
}

/// Stored timeline (REST) merged into what is already shown live.
Timeline mergeServer(Timeline t, List<TItem> items, {required bool hasMore, bool older = false}) {
  var out = t.items;
  for (final i in items) {
    out = upsertItem(out, i);
  }
  final turns = out.where((e) => e.isTurn).toList();
  return t.copy(
    items: out,
    hasMore: older || !t.loaded ? hasMore : t.hasMore,
    loaded: true,
    running: turns.isEmpty ? t.running : turns.last.status == 'running',
  );
}

/// Folds one bus event (`chat.*`) of this conversation into the timeline.
Timeline applyChatEvent(Timeline t, String type, Map<String, dynamic> data) {
  switch (type) {
    case 'chat.event':
      final raw = data['event'];
      if (raw is! Map) return t;
      final item = TItem.fromJson(raw.cast<String, dynamic>());
      final items = upsertItem(t.items, item);
      return t.copy(items: items, running: item.isTurn ? item.status == 'running' : t.running);
    case 'chat.delta':
    case 'chat.thinking':
      final id = data['event_id']?.toString();
      if (id == null) return t;
      return t.copy(items: appendText(t.items, id, data['text']?.toString() ?? ''), running: true);
    case 'chat.check':
      final id = data['event_id']?.toString();
      final index = (data['index'] as num?)?.toInt();
      if (id == null || index == null) return t;
      return setCheck(t, id, index, data['checked'] == true);
    case 'chat.user':
      return t.running ? t : t.copy(running: true);
    case 'chat.done':
      return t.running ? t.copy(running: false) : t;
    default:
      return t;
  }
}

/// Buffered deltas (`item id -> text`) applied in one update – streaming is throttled.
Timeline applyDeltas(Timeline t, Map<String, String> deltas) {
  var items = t.items;
  deltas.forEach((id, text) => items = appendText(items, id, text));
  return identical(items, t.items) ? t : t.copy(items: items, running: true);
}

// ------------------------------------------------------------------ rows
sealed class TRow {
  const TRow(this.key);
  final String key;
}

class UserRow extends TRow {
  UserRow(this.item) : super(item.key);
  final TItem item;
}

class SystemRow extends TRow {
  SystemRow(this.item) : super(item.key);
  final TItem item;
}

class AnswerRow extends TRow {
  AnswerRow(this.turnId) : super('turn-$turnId');
  final String turnId;
  TItem? turn;
  final List<TItem> items = []; // assistant text segments and tool calls, in event order
  final List<TItem> process = [];
  final List<TItem> tools = [];

  bool get running => turn?.status == 'running';
}

/// Groups the ordered items into what the chat shows: Sir's messages, Jarvis' answers per turn, system notes.
List<TRow> buildRows(List<TItem> items) {
  final rows = <TRow>[];
  final answers = <String, AnswerRow>{};
  AnswerRow answer(TItem e) {
    final turnId = e.turnId ?? 'solo-${e.id}';
    return answers.putIfAbsent(turnId, () {
      final row = AnswerRow(turnId);
      rows.add(row);
      return row;
    });
  }

  for (final e in items) {
    if (e.isMessage) {
      if (e.role == 'user') {
        rows.add(UserRow(e));
      } else if (e.role == 'system') {
        rows.add(SystemRow(e));
      } else {
        answer(e).items.add(e);
      }
    } else if (e.isTool) {
      answer(e)
        ..items.add(e)
        ..tools.add(e);
    } else if (e.isProcess) {
      answer(e).process.add(e);
    } else if (e.isTurn) {
      answer(e).turn = e;
    }
  }
  return rows;
}

class ToolSpan {
  const ToolSpan(this.offset, this.width, this.parallel);
  final double offset; // 0..1 on the shared time axis of the answer
  final double width;
  final bool parallel; // overlaps another call of the same answer
}

Map<String, ToolSpan> toolSpans(List<TItem> tools, int now) {
  final timed = tools.where((t) => t.intField('started_ts') != null).toList();
  if (timed.isEmpty) return const {};
  int start(TItem t) => t.intField('started_ts')!;
  int end(TItem t) => t.intField('ended_ts') ?? (t.status == 'running' ? now : start(t));
  final lo = timed.map(start).reduce(math.min);
  final hi = math.max(timed.map(end).reduce(math.max), lo + 1);
  return {
    for (final t in timed)
      t.id: ToolSpan(
        (start(t) - lo) / (hi - lo),
        math.max(0.015, (end(t) - start(t)) / (hi - lo)),
        timed.any((o) => !identical(o, t) && start(o) < end(t) && start(t) < end(o)),
      ),
  };
}

/// Latest step for the collapsed "Gedankenprozess" bar (the closing summary does not count).
TItem? currentStep(List<TItem> process) {
  for (var i = process.length - 1; i >= 0; i--) {
    if (process[i].step != 'summary') return process[i];
  }
  return null;
}

bool hasModelReasoning(List<TItem> process) => process.any((p) => p.source == 'model' && p.text.isNotEmpty);

const sourceLabel = {'model': 'Modell', 'protocol': 'Protokoll', 'summary': 'Zusammenfassung'};
