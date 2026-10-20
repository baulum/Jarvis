import 'dart:math' as math;

/// Small dependency-free Markdown parser for chat answers: headings, paragraphs, bullet / numbered lists
/// (nested), block quotes, fenced code, tables, rules and inline bold / italic / strike / code / links.
/// Tolerant of half-finished text while an answer is still streaming (an unclosed code fence is code until
/// the end, an unclosed `**` stays literal until the closing marker arrives).

enum MdAlign { left, center, right }

sealed class MdBlock {
  const MdBlock();
}

class MdHeading extends MdBlock {
  const MdHeading(this.level, this.spans);
  final int level;
  final List<MdSpan> spans;
}

class MdParagraph extends MdBlock {
  const MdParagraph(this.spans);
  final List<MdSpan> spans;
}

class MdCode extends MdBlock {
  const MdCode(this.lang, this.code);
  final String lang;
  final String code;
}

class MdQuote extends MdBlock {
  const MdQuote(this.blocks);
  final List<MdBlock> blocks;
}

class MdList extends MdBlock {
  const MdList({required this.ordered, required this.start, required this.items, required this.checked});
  final bool ordered;
  final int start;
  final List<List<MdBlock>> items;

  /// task-list state per item (`- [x]` / `- [ ]`), null for a normal item
  final List<bool?> checked;
}

class MdTable extends MdBlock {
  const MdTable({required this.header, required this.aligns, required this.rows});
  final List<List<MdSpan>> header;
  final List<MdAlign> aligns;
  final List<List<List<MdSpan>>> rows;
}

class MdRule extends MdBlock {
  const MdRule();
}

class MdSpan {
  const MdSpan(this.text, {this.bold = false, this.italic = false, this.strike = false, this.code = false, this.href});
  final String text;
  final bool bold;
  final bool italic;
  final bool strike;
  final bool code;
  final String? href;
}

// ------------------------------------------------------------------ blocks
final _fenceRe = RegExp(r'^\s{0,3}(`{3,}|~{3,})\s*([^\s`]*).*$');
final _headingRe = RegExp(r'^\s{0,3}(#{1,6})\s+(.+?)(?:\s+#+)?\s*$');
final _ruleRe = RegExp(r'^\s{0,3}([-*_])(?:\s*\1){2,}\s*$');
final _quoteRe = RegExp(r'^\s{0,3}>\s?');
final _listRe = RegExp(r'^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$');
final _tableSepRe = RegExp(r'^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$');
final _taskRe = RegExp(r'^\[([ xX])\]\s+');

/// Only http(s) and mailto links are ever made tappable.
String? safeHref(String raw) {
  final href = raw.trim();
  return RegExp(r'^(https?://|mailto:)', caseSensitive: false).hasMatch(href) ? href : null;
}

List<MdBlock> parseMarkdown(String src) =>
    _blocks(src.replaceAll('\r\n', '\n').replaceAll('\r', '\n').split('\n'));

bool _isOrdered(String marker) => marker.codeUnitAt(0) >= 0x30 && marker.codeUnitAt(0) <= 0x39;

int _indent(String s) {
  var n = 0;
  for (final c in s.codeUnits) {
    if (c == 0x20) {
      n++;
    } else if (c == 0x09) {
      n += 4;
    } else {
      break;
    }
  }
  return n;
}

bool _closesFence(String line, String marker) {
  final t = line.trim();
  return t.length >= marker.length && t.split('').every((c) => c == marker[0]);
}

bool _isTableStart(List<String> lines, int i) =>
    i + 1 < lines.length &&
    lines[i].contains('|') &&
    lines[i + 1].contains('|') &&
    lines[i + 1].contains('-') &&
    _tableSepRe.hasMatch(lines[i + 1]);

bool _startsBlock(List<String> lines, int i) {
  final l = lines[i];
  return _fenceRe.hasMatch(l) ||
      _headingRe.hasMatch(l) ||
      _ruleRe.hasMatch(l) ||
      _quoteRe.hasMatch(l) ||
      _listRe.hasMatch(l) ||
      _isTableStart(lines, i);
}

List<String> _splitRow(String line) {
  var s = line.trim();
  if (s.startsWith('|')) s = s.substring(1);
  if (s.endsWith('|') && !s.endsWith(r'\|')) s = s.substring(0, s.length - 1);
  final cells = <String>[];
  final b = StringBuffer();
  for (var i = 0; i < s.length; i++) {
    final c = s[i];
    if (c == '\\' && i + 1 < s.length && s[i + 1] == '|') {
      b.write('|');
      i++;
    } else if (c == '|') {
      cells.add(b.toString().trim());
      b.clear();
    } else {
      b.write(c);
    }
  }
  cells.add(b.toString().trim());
  return cells;
}

MdAlign _align(String sep) {
  final t = sep.trim();
  final l = t.startsWith(':'), r = t.endsWith(':');
  if (l && r) return MdAlign.center;
  return r ? MdAlign.right : MdAlign.left;
}

List<MdBlock> _blocks(List<String> lines) {
  final out = <MdBlock>[];
  var i = 0;
  while (i < lines.length) {
    final line = lines[i];
    if (line.trim().isEmpty) {
      i++;
      continue;
    }
    final fence = _fenceRe.firstMatch(line);
    if (fence != null) {
      final marker = fence.group(1)!;
      final code = <String>[];
      i++;
      while (i < lines.length && !_closesFence(lines[i], marker)) {
        code.add(lines[i]);
        i++;
      }
      if (i < lines.length) i++; // closing fence; while streaming it is missing and the rest is code
      out.add(MdCode(fence.group(2) ?? '', code.join('\n')));
      continue;
    }
    final h = _headingRe.firstMatch(line);
    if (h != null) {
      out.add(MdHeading(h.group(1)!.length, parseInline(h.group(2)!)));
      i++;
      continue;
    }
    if (_ruleRe.hasMatch(line)) {
      out.add(const MdRule());
      i++;
      continue;
    }
    if (_isTableStart(lines, i)) {
      final header = _splitRow(line);
      final aligns = _splitRow(lines[i + 1]).map(_align).toList();
      final rows = <List<List<MdSpan>>>[];
      i += 2;
      while (i < lines.length && lines[i].trim().isNotEmpty && lines[i].contains('|')) {
        final cells = _splitRow(lines[i]);
        rows.add([for (var c = 0; c < header.length; c++) parseInline(c < cells.length ? cells[c] : '')]);
        i++;
      }
      out.add(MdTable(
        header: [for (final c in header) parseInline(c)],
        aligns: [for (var c = 0; c < header.length; c++) c < aligns.length ? aligns[c] : MdAlign.left],
        rows: rows,
      ));
      continue;
    }
    if (_quoteRe.hasMatch(line)) {
      final inner = <String>[];
      while (i < lines.length && _quoteRe.hasMatch(lines[i])) {
        inner.add(lines[i].replaceFirst(_quoteRe, ''));
        i++;
      }
      out.add(MdQuote(_blocks(inner)));
      continue;
    }
    if (_listRe.hasMatch(line)) {
      final (list, next) = _parseList(lines, i);
      out.add(list);
      i = next;
      continue;
    }
    final para = <String>[line.trim()];
    i++;
    while (i < lines.length && lines[i].trim().isNotEmpty && !_startsBlock(lines, i)) {
      para.add(lines[i].trim());
      i++;
    }
    out.add(MdParagraph(parseInline(para.join('\n'))));
  }
  return out;
}

(MdList, int) _parseList(List<String> lines, int start) {
  final first = _listRe.firstMatch(lines[start])!;
  final base = first.group(1)!.length;
  final ordered = _isOrdered(first.group(2)!);
  final startNo = ordered ? int.tryParse(first.group(2)!.replaceAll(RegExp(r'[.)]'), '')) ?? 1 : 1;
  final items = <List<MdBlock>>[];
  final checked = <bool?>[];
  var i = start;
  while (i < lines.length) {
    final m = _listRe.firstMatch(lines[i]);
    if (m == null || m.group(1)!.length != base || _isOrdered(m.group(2)!) != ordered) break;
    final task = _taskRe.firstMatch(m.group(3)!);
    checked.add(task == null ? null : task.group(1) != ' ');
    final item = <String>[task == null ? m.group(3)! : m.group(3)!.substring(task.end)];
    i++;
    while (i < lines.length) {
      final l = lines[i];
      if (l.trim().isEmpty) {
        var k = i + 1;
        while (k < lines.length && lines[k].trim().isEmpty) {
          k++;
        }
        if (k < lines.length && _indent(lines[k]) > base) {
          item.add('');
          i++;
          continue;
        }
        break;
      }
      if (_indent(l) <= base) break;
      final lead = l.length - l.trimLeft().length;
      item.add(l.substring(math.min(lead, base + 2)));
      i++;
    }
    items.add(_blocks(item));
    // a blank line between two items of the same list does not end it
    var k = i;
    while (k < lines.length && lines[k].trim().isEmpty) {
      k++;
    }
    if (k > i && k < lines.length) {
      final n = _listRe.firstMatch(lines[k]);
      if (n != null && n.group(1)!.length == base && _isOrdered(n.group(2)!) == ordered) i = k;
    }
  }
  return (MdList(ordered: ordered, start: startNo, items: items, checked: checked), i);
}

// ------------------------------------------------------------------ inline
const _escapable = r'\`*_{}[]()#+-.!|~>';
final _brRe = RegExp(r'<br\s*/?>', caseSensitive: false);

final _wordRe = RegExp(r'[A-Za-z0-9]');
bool _isWord(String c) => _wordRe.hasMatch(c);
bool _isSpace(String c) => c.trim().isEmpty;

List<MdSpan> parseInline(String s) {
  final out = <MdSpan>[];
  _inline(s.replaceAll(_brRe, '\n'), false, false, false, null, out);
  return out;
}

/// Index of the `)` that closes a link target starting at [from]; balanced parentheses belong to the URL.
int _closingParen(String s, int from) {
  var depth = 1;
  for (var j = from; j < s.length; j++) {
    final c = s[j];
    if (c == '\n') return -1;
    if (c == '(') {
      depth++;
    } else if (c == ')' && --depth == 0) {
      return j;
    }
  }
  return -1;
}

int _closeStrong(String s, String marker, int from) {
  var j = s.indexOf(marker, from);
  while (j != -1) {
    if (j > from && !_isSpace(s[j - 1])) return j;
    j = s.indexOf(marker, j + 1);
  }
  return -1;
}

int _closeEm(String s, String c, int from) {
  var j = from;
  while (j < s.length) {
    if (s[j] == '\\') {
      j += 2;
      continue;
    }
    if (s[j] == c) {
      if (j + 1 < s.length && s[j + 1] == c) {
        j += 2; // a `**` inside the emphasis
        continue;
      }
      if (j > from && !_isSpace(s[j - 1]) && (c != '_' || j + 1 >= s.length || !_isWord(s[j + 1]))) return j;
    }
    j++;
  }
  return -1;
}

void _inline(String s, bool b, bool it, bool st, String? href, List<MdSpan> out) {
  final buf = StringBuffer();
  void flush() {
    if (buf.isEmpty) return;
    out.add(MdSpan(buf.toString(), bold: b, italic: it, strike: st, href: href));
    buf.clear();
  }

  var i = 0;
  while (i < s.length) {
    final c = s[i];
    if (c == '\\' && i + 1 < s.length && _escapable.contains(s[i + 1])) {
      buf.write(s[i + 1]);
      i += 2;
      continue;
    }
    if (c == '`') {
      final j = s.indexOf('`', i + 1);
      if (j > i + 1) {
        flush();
        out.add(MdSpan(s.substring(i + 1, j).trim(), code: true, bold: b, italic: it, strike: st, href: href));
        i = j + 1;
        continue;
      }
    }
    if (c == '*' || c == '_') {
      final startsWord = c == '_' && i > 0 && _isWord(s[i - 1]);
      if (!startsWord) {
        if (s.startsWith('$c$c$c', i) && i + 3 < s.length && !_isSpace(s[i + 3])) {
          final j = _closeStrong(s, '$c$c$c', i + 3);
          if (j != -1) {
            flush();
            _inline(s.substring(i + 3, j), true, true, st, href, out);
            i = j + 3;
            continue;
          }
        }
        if (s.startsWith('$c$c', i) && i + 2 < s.length && !_isSpace(s[i + 2])) {
          final j = _closeStrong(s, '$c$c', i + 2);
          if (j != -1) {
            flush();
            _inline(s.substring(i + 2, j), true, it, st, href, out);
            i = j + 2;
            continue;
          }
        } else if (i + 1 < s.length && s[i + 1] != c && !_isSpace(s[i + 1])) {
          final j = _closeEm(s, c, i + 1);
          if (j != -1) {
            flush();
            _inline(s.substring(i + 1, j), b, true, st, href, out);
            i = j + 1;
            continue;
          }
        }
      }
    }
    if (c == '~' && s.startsWith('~~', i) && i + 2 < s.length && !_isSpace(s[i + 2])) {
      final j = _closeStrong(s, '~~', i + 2);
      if (j != -1) {
        flush();
        _inline(s.substring(i + 2, j), b, it, true, href, out);
        i = j + 2;
        continue;
      }
    }
    if (c == '[' && href == null) {
      final close = s.indexOf(']', i + 1);
      if (close > i && close + 1 < s.length && s[close + 1] == '(') {
        final end = _closingParen(s, close + 2);
        if (end > close + 1) {
          final raw = s.substring(close + 2, end).trim();
          final url = raw.isEmpty ? '' : raw.split(RegExp(r'\s+')).first;
          if (url.isNotEmpty) {
            flush();
            // unsafe targets (javascript:, data:, file: …) leave just the link text
            _inline(s.substring(i + 1, close), b, it, st, safeHref(url), out);
            i = end + 1;
            continue;
          }
        }
      }
    }
    if (c == 'h' && href == null && (s.startsWith('http://', i) || s.startsWith('https://', i)) && (i == 0 || !_isWord(s[i - 1]))) {
      var end = i;
      while (end < s.length && !_isSpace(s[end]) && !'<>"'.contains(s[end])) {
        end++;
      }
      while (end > i && '.,;:!?)]'.contains(s[end - 1])) {
        end--;
      }
      if (end > i + 8) {
        flush();
        final url = s.substring(i, end);
        out.add(MdSpan(url, bold: b, italic: it, strike: st, href: url));
        i = end;
        continue;
      }
    }
    buf.write(c);
    i++;
  }
  flush();
}

/// Plain text of spans (used for copy and tests).
String spansText(List<MdSpan> spans) => spans.map((s) => s.text).join();
