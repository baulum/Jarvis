// Small, dependency-free Markdown parser for chat output (CommonMark subset + GFM tables, task lists,
// strikethrough, autolinks). Same grammar as the desktop (desktop/src/lib/markdown.ts); it yields a tree the
// widgets render node by node – there is no HTML anywhere. Tolerant of half-finished input while streaming.
// Pure Dart (no Flutter imports) so it can be unit-tested without a device.

sealed class Inline {
  const Inline();
}

class IText extends Inline {
  const IText(this.v);
  final String v;
}

class ICode extends Inline {
  const ICode(this.v);
  final String v;
}

class IStrong extends Inline {
  const IStrong(this.c);
  final List<Inline> c;
}

class IEm extends Inline {
  const IEm(this.c);
  final List<Inline> c;
}

class IDel extends Inline {
  const IDel(this.c);
  final List<Inline> c;
}

class ILink extends Inline {
  const ILink(this.href, this.c);
  final String href;
  final List<Inline> c;
}

class IBreak extends Inline {
  const IBreak();
}

sealed class Block {
  const Block();
}

class BHeading extends Block {
  const BHeading(this.level, this.c);
  final int level;
  final List<Inline> c;
}

class BPara extends Block {
  const BPara(this.c);
  final List<Inline> c;
}

class BCode extends Block {
  const BCode(this.lang, this.v, {this.open = false});
  final String lang;
  final String v;

  /// fence not closed yet (answer still streaming)
  final bool open;
}

class BQuote extends Block {
  const BQuote(this.c);
  final List<Block> c;
}

class ListItem {
  const ListItem(this.checked, this.c);
  final bool? checked;
  final List<Block> c;
}

class BList extends Block {
  const BList(this.ordered, this.start, this.items);
  final bool ordered;
  final int start;
  final List<ListItem> items;
}

class BTable extends Block {
  const BTable(this.align, this.head, this.rows);

  /// per column: 'left' | 'center' | 'right' | null
  final List<String?> align;
  final List<List<Inline>> head;
  final List<List<List<Inline>>> rows;
}

class BRule extends Block {
  const BRule();
}

final _fence = RegExp(r'^ {0,3}(`{3,}|~{3,})\s*([^\s`]*)[^`]*$');
final _heading = RegExp(r'^ {0,3}(#{1,6})(?:\s+(.*?))?(?:\s+#+)?\s*$');
final _hr = RegExp(r'^ {0,3}([-*_])(?:\s*\1){2,}\s*$');
final _quote = RegExp(r'^ {0,3}>');
final _listItem = RegExp(r'^(\s*)([-*+]|\d{1,9}[.)])(\s+)(.*)$');
final _tableDelim = RegExp(r'^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$');
final _punct = RegExp(r'[!-/:-@\[-`{-~]');
final _wordChar = RegExp(r'[\p{L}\p{N}]', unicode: true);

bool _isBlank(String l) => l.trim().isEmpty;
bool _isWord(String? c) => c != null && c.isNotEmpty && _wordChar.hasMatch(c);
String? _at(String s, int i) => i >= 0 && i < s.length ? s[i] : null;

/// Only http(s) and mailto links are ever made tappable.
String? safeHref(String raw) {
  final href = raw.trim();
  return RegExp(r'^(https?://|mailto:)', caseSensitive: false).hasMatch(href) ? href : null;
}

List<Block> parseMarkdown(String src) => _parseBlocks(src.replaceAll('\r\n', '\n').replaceAll('\r', '\n').split('\n'));

List<String> _splitRow(String line) {
  var s = line.trim();
  if (s.startsWith('|')) s = s.substring(1);
  if (s.endsWith('|') && !s.endsWith('\\|')) s = s.substring(0, s.length - 1);
  final cells = <String>[];
  final cur = StringBuffer();
  var inCode = false;
  for (var i = 0; i < s.length; i++) {
    final ch = s[i];
    if (ch == '\\' && _at(s, i + 1) == '|') {
      cur.write('|');
      i++;
    } else if (ch == '`') {
      inCode = !inCode;
      cur.write(ch);
    } else if (ch == '|' && !inCode) {
      cells.add(cur.toString().trim());
      cur.clear();
    } else {
      cur.write(ch);
    }
  }
  cells.add(cur.toString().trim());
  return cells;
}

bool _startsTable(List<String> lines, int i) {
  if (i + 1 >= lines.length) return false;
  final head = lines[i];
  final delim = lines[i + 1];
  if (!head.contains('|') || !delim.contains('-')) return false;
  if (!_tableDelim.hasMatch(delim)) return false;
  return _splitRow(head).length == _splitRow(delim).length;
}

class _Marker {
  const _Marker(this.indent, this.ordered, this.num, this.content, this.text);
  final int indent;
  final bool ordered;
  final int num;
  final int content;
  final String text;
}

_Marker? _listMarker(String line) {
  final m = _listItem.firstMatch(line);
  if (m == null || _hr.hasMatch(line)) return null;
  final indent = m.group(1)!.length;
  final marker = m.group(2)!;
  final ordered = RegExp(r'\d').hasMatch(marker);
  return _Marker(
    indent,
    ordered,
    ordered ? (int.tryParse(marker.substring(0, marker.length - 1)) ?? 1) : 0,
    indent + marker.length + m.group(3)!.length,
    m.group(4)!,
  );
}

bool _interruptsParagraph(List<String> lines, int i) {
  final l = lines[i];
  if (_fence.hasMatch(l) || _heading.hasMatch(l) || _hr.hasMatch(l) || _quote.hasMatch(l)) return true;
  final lm = _listMarker(l);
  if (lm != null && lm.text.trim().isNotEmpty && (!lm.ordered || lm.num == 1)) return true;
  return _startsTable(lines, i);
}

int _indentOf(String l) {
  final m = RegExp(r'\S').firstMatch(l);
  return m == null ? -1 : m.start;
}

List<Block> _parseBlocks(List<String> lines) {
  final out = <Block>[];
  var i = 0;
  while (i < lines.length) {
    final line = lines[i];
    if (_isBlank(line)) {
      i++;
      continue;
    }

    final fence = _fence.firstMatch(line);
    if (fence != null) {
      final mark = fence.group(1)!;
      final body = <String>[];
      var closed = false;
      i++;
      while (i < lines.length) {
        final l = lines[i];
        final t = l.trim();
        if (t.startsWith(mark[0] * mark.length) && RegExp(r'^[`~]+$').hasMatch(t)) {
          closed = true;
          i++;
          break;
        }
        body.add(l);
        i++;
      }
      out.add(BCode((fence.group(2) ?? '').toLowerCase(), body.join('\n'), open: !closed));
      continue;
    }

    final h = _heading.firstMatch(line);
    if (h != null) {
      out.add(BHeading(h.group(1)!.length, parseInline((h.group(2) ?? '').trim())));
      i++;
      continue;
    }

    if (_hr.hasMatch(line)) {
      out.add(const BRule());
      i++;
      continue;
    }

    if (_startsTable(lines, i)) {
      final delim = _splitRow(lines[i + 1]);
      final align = <String?>[
        for (final c in delim)
          if (c.startsWith(':') && c.endsWith(':'))
            'center'
          else if (c.endsWith(':'))
            'right'
          else if (c.startsWith(':'))
            'left'
          else
            null,
      ];
      final head = _splitRow(line).map(parseInline).toList();
      final rows = <List<List<Inline>>>[];
      i += 2;
      while (i < lines.length && !_isBlank(lines[i]) && lines[i].contains('|')) {
        final cells = _splitRow(lines[i]);
        rows.add([for (var k = 0; k < align.length; k++) parseInline(k < cells.length ? cells[k] : '')]);
        i++;
      }
      out.add(BTable(align, head, rows));
      continue;
    }

    if (_quote.hasMatch(line)) {
      final inner = <String>[];
      while (i < lines.length && _quote.hasMatch(lines[i])) {
        inner.add(lines[i].replaceFirst(RegExp(r'^ {0,3}> ?'), ''));
        i++;
      }
      out.add(BQuote(_parseBlocks(inner)));
      continue;
    }

    final lm = _listMarker(line);
    if (lm != null) {
      final (block, next) = _parseList(lines, i, lm);
      out.add(block);
      i = next;
      continue;
    }

    final para = <String>[line];
    i++;
    while (i < lines.length && !_isBlank(lines[i]) && !_interruptsParagraph(lines, i)) {
      para.add(lines[i]);
      i++;
    }
    out.add(BPara(parseInline(para.map((l) => l.trim()).join('\n'))));
  }
  return out;
}

(Block, int) _parseList(List<String> lines, int start, _Marker first) {
  final items = <ListItem>[];
  var i = start;
  var cur = first;
  while (true) {
    final body = <String>[cur.text];
    i++;
    while (i < lines.length) {
      final l = lines[i];
      if (_isBlank(l)) {
        var j = i;
        while (j < lines.length && _isBlank(lines[j])) {
          j++;
        }
        if (j < lines.length && _indentOf(lines[j]) >= cur.indent + 2) {
          body.add('');
          i++;
          continue;
        }
        break;
      }
      final ind = _indentOf(l);
      if (ind >= cur.indent + 2) {
        body.add(l.substring(ind < cur.content ? ind : cur.content));
        i++;
        continue;
      }
      if (_listMarker(l) != null || _fence.hasMatch(l) || _heading.hasMatch(l) || _quote.hasMatch(l) || _hr.hasMatch(l)) {
        break;
      }
      body.add(l.trim()); // lazy continuation of the item's paragraph
      i++;
    }
    bool? checked;
    final task = RegExp(r'^\[([ xX])\]\s+').firstMatch(body[0]);
    if (task != null) {
      checked = task.group(1) != ' ';
      body[0] = body[0].substring(task.end);
    }
    items.add(ListItem(checked, _parseBlocks(body)));

    var j = i;
    while (j < lines.length && _isBlank(lines[j])) {
      j++;
    }
    final nm = j < lines.length ? _listMarker(lines[j]) : null;
    if (nm != null && nm.ordered == first.ordered && nm.indent < cur.indent + 2 && nm.indent + 2 > first.indent) {
      cur = nm;
      i = j;
      continue;
    }
    break;
  }
  return (BList(first.ordered, first.num, items), i);
}

// ------------------------------------------------------------------ inline
List<Inline> parseInline(String src) {
  final out = <Inline>[];
  final buf = StringBuffer();
  void flush() {
    if (buf.isNotEmpty) {
      out.add(IText(buf.toString()));
      buf.clear();
    }
  }

  var i = 0;
  while (i < src.length) {
    final ch = src[i];

    if (ch == '\\' && i + 1 < src.length) {
      final nx = src[i + 1];
      if (nx == '\n') {
        flush();
        out.add(const IBreak());
        i += 2;
        continue;
      }
      if (_punct.hasMatch(nx)) {
        buf.write(nx);
        i += 2;
        continue;
      }
    }

    if (ch == '\n') {
      final t = buf.toString().replaceFirst(RegExp(r' +$'), '');
      buf
        ..clear()
        ..write(t);
      flush();
      out.add(const IBreak());
      i++;
      continue;
    }

    if (ch == '`') {
      var n = 1;
      while (_at(src, i + n) == '`') {
        n++;
      }
      final ticks = '`' * n;
      var end = src.indexOf(ticks, i + n);
      while (end != -1 && _at(src, end + n) == '`') {
        end = src.indexOf(ticks, end + n + 1);
      }
      if (end != -1) {
        var v = src.substring(i + n, end).replaceAll('\n', ' ');
        if (v.length > 2 && v.startsWith(' ') && v.endsWith(' ')) v = v.substring(1, v.length - 1);
        flush();
        out.add(ICode(v));
        i = end + n;
        continue;
      }
      buf.write(ticks);
      i += n;
      continue;
    }

    if (ch == '[') {
      final link = _matchLink(src, i);
      if (link != null) {
        flush();
        final href = safeHref(link.href);
        final children = parseInline(link.text);
        if (href != null) {
          out.add(ILink(href, children));
        } else {
          out.addAll(children);
        }
        i = link.end;
        continue;
      }
    }

    if ((ch == 'h' || ch == 'H') && !_isWord(_at(src, i - 1))) {
      final m = RegExp(r'^https?://[^\s<>]+', caseSensitive: false).firstMatch(src.substring(i));
      if (m != null) {
        final url = _trimUrl(m.group(0)!);
        flush();
        out.add(ILink(url, [IText(url)]));
        i += url.length;
        continue;
      }
    }

    if (ch == '*' || ch == '_' || ch == '~') {
      var run = 1;
      while (_at(src, i + run) == ch && run < 3) {
        run++;
      }
      if (ch == '~' && run < 2) {
        buf.write(ch);
        i++;
        continue;
      }
      final n = ch == '~' ? 2 : run;
      final marker = ch * n;
      final closeAt = _findClose(src, i, marker, ch);
      if (closeAt != -1) {
        final kids = parseInline(src.substring(i + n, closeAt));
        flush();
        if (ch == '~') {
          out.add(IDel(kids));
        } else if (n == 3) {
          out.add(IEm([IStrong(kids)]));
        } else if (n == 2) {
          out.add(IStrong(kids));
        } else {
          out.add(IEm(kids));
        }
        i = closeAt + n;
        continue;
      }
      buf.write(ch * run);
      i += run;
      continue;
    }

    buf.write(ch);
    i++;
  }
  flush();
  return out;
}

String _trimUrl(String url) {
  var u = url;
  while (RegExp(r'''[.,;:!?'"*_]$''').hasMatch(u)) {
    u = u.substring(0, u.length - 1);
  }
  int count(String s, String c) => c.allMatches(s).length;
  while (u.endsWith(')') && count(u, ')') > count(u, '(')) {
    u = u.substring(0, u.length - 1);
  }
  return u;
}

int _findClose(String src, int open, String marker, String ch) {
  final n = marker.length;
  final after = _at(src, open + n);
  if (after == null || after.trim().isEmpty) return -1; // opener must hug the text
  if (ch == '_' && _isWord(_at(src, open - 1))) return -1; // snake_case stays literal
  var from = open + n;
  while (from < src.length) {
    final at = src.indexOf(marker, from);
    if (at == -1) return -1;
    // skip code spans so `a*b` inside backticks never closes emphasis
    final tick = src.indexOf('`', from);
    if (tick != -1 && tick < at) {
      final endTick = src.indexOf('`', tick + 1);
      if (endTick != -1 && endTick > at) {
        from = endTick + 1;
        continue;
      }
    }
    final before = _at(src, at - 1);
    final next = _at(src, at + n);
    final longer = next == ch && n < 3; // part of a longer run
    final okClose = before != null && before.trim().isNotEmpty && !longer && !(ch == '_' && _isWord(next));
    if (okClose && at > open + n) return at;
    from = at + (longer ? 2 : 1);
  }
  return -1;
}

class _LinkMatch {
  const _LinkMatch(this.text, this.href, this.end);
  final String text;
  final String href;
  final int end;
}

_LinkMatch? _matchLink(String src, int at) {
  var depth = 0;
  var i = at;
  for (; i < src.length; i++) {
    final c = src[i];
    if (c == '\\') {
      i++;
    } else if (c == '[') {
      depth++;
    } else if (c == ']') {
      depth--;
      if (depth == 0) break;
    }
  }
  if (i >= src.length || _at(src, i + 1) != '(') return null;
  final text = src.substring(at + 1, i);
  var j = i + 2;
  var parens = 1;
  for (; j < src.length; j++) {
    final c = src[j];
    if (c == '\n') return null;
    if (c == '(') {
      parens++;
    } else if (c == ')') {
      parens--;
      if (parens == 0) break;
    }
  }
  if (j >= src.length) return null;
  final inner = src.substring(i + 2, j).trim();
  final href = inner.replaceFirst(RegExp(r'''\s+("[^"]*"|'[^']*')$'''), '').replaceFirstMapped(RegExp(r'^<(.*)>$'), (m) => m.group(1)!);
  return _LinkMatch(text, href, j + 1);
}

/// Plain text of inline nodes (copy, tests).
String inlineText(List<Inline> nodes) {
  final b = StringBuffer();
  for (final n in nodes) {
    switch (n) {
      case IText(:final v):
        b.write(v);
      case ICode(:final v):
        b.write(v);
      case IBreak():
        b.write('\n');
      case IStrong(:final c):
        b.write(inlineText(c));
      case IEm(:final c):
        b.write(inlineText(c));
      case IDel(:final c):
        b.write(inlineText(c));
      case ILink(:final c):
        b.write(inlineText(c));
    }
  }
  return b.toString();
}
