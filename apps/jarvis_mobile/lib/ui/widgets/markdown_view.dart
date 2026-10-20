import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../core/markdown.dart';
import '../../core/theme.dart';

/// Renders a chat answer: headings, lists, tables (horizontally scrollable), quotes, inline code,
/// code blocks with syntax colours and a copy button, links. No raw Markdown characters remain visible.
class MarkdownView extends StatefulWidget {
  const MarkdownView(this.data, {super.key, this.onLink});
  final String data;
  final void Function(String url)? onLink;

  @override
  State<MarkdownView> createState() => _MarkdownViewState();
}

class _MarkdownViewState extends State<MarkdownView> {
  final _recognizers = <TapGestureRecognizer>[];

  void _disposeRecognizers() {
    for (final r in _recognizers) {
      r.dispose();
    }
    _recognizers.clear();
  }

  @override
  void dispose() {
    _disposeRecognizers();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    _disposeRecognizers();
    final blocks = parseMarkdown(widget.data);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: _blockWidgets(blocks, depth: 0, dim: false),
    );
  }

  // ---------------------------------------------------------------- inline
  TextSpan _span(List<MdSpan> spans, TextStyle base) {
    return TextSpan(
      style: base,
      children: [
        for (final s in spans)
          TextSpan(text: s.text, style: _spanStyle(s, base), recognizer: _recognizer(s)),
      ],
    );
  }

  GestureRecognizer? _recognizer(MdSpan s) {
    final url = s.href;
    if (url == null) return null;
    final r = TapGestureRecognizer()..onTap = () => widget.onLink?.call(url);
    _recognizers.add(r);
    return r;
  }

  TextStyle _spanStyle(MdSpan s, TextStyle base) {
    var st = const TextStyle();
    if (s.bold) st = st.copyWith(fontVariations: const [FontVariation('wght', 640)], color: J.ice);
    if (s.italic) st = st.copyWith(fontStyle: FontStyle.italic);
    final deco = <TextDecoration>[
      if (s.strike) TextDecoration.lineThrough,
      if (s.href != null) TextDecoration.underline,
    ];
    if (deco.isNotEmpty) st = st.copyWith(decoration: TextDecoration.combine(deco), decorationColor: J.cyanDeep);
    if (s.href != null) st = st.copyWith(color: J.cyan);
    if (s.code) {
      st = st.copyWith(
        fontFamily: 'JetBrainsMono',
        fontSize: (base.fontSize ?? 16) * 0.86,
        color: J.ice,
        backgroundColor: const Color(0xFF0F1D27),
        letterSpacing: 0,
      );
    }
    return st;
  }

  // ---------------------------------------------------------------- blocks
  List<Widget> _blockWidgets(List<MdBlock> blocks, {required int depth, required bool dim, bool tight = false}) {
    final out = <Widget>[];
    for (var i = 0; i < blocks.length; i++) {
      final b = blocks[i];
      final gap = i == 0 ? 0.0 : (tight ? J.s1 : J.s3);
      out.add(Padding(padding: EdgeInsets.only(top: gap), child: _block(b, depth: depth, dim: dim)));
    }
    return out;
  }

  Widget _block(MdBlock b, {required int depth, required bool dim}) {
    final body = dim ? J.body.copyWith(color: J.text2) : J.body;
    switch (b) {
      case MdParagraph p:
        return Text.rich(_span(p.spans, body.copyWith(height: 1.5)));
      case MdHeading h:
        return _heading(h);
      case MdList l:
        return _list(l, depth, dim);
      case MdQuote q:
        return Container(
          padding: const EdgeInsets.only(left: J.s3, top: 2, bottom: 2),
          decoration: const BoxDecoration(border: Border(left: BorderSide(color: J.cyanDeep, width: 2))),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: _blockWidgets(q.blocks, depth: depth, dim: true)),
        );
      case MdCode c:
        return CodeBlock(code: c.code, lang: c.lang);
      case MdTable t:
        return _table(t);
      case MdRule _:
        return const Padding(padding: EdgeInsets.symmetric(vertical: J.s2), child: Divider(height: 1, thickness: 1, color: J.lineBright));
    }
  }

  Widget _heading(MdHeading h) {
    final (size, weight, color, top) = switch (h.level) {
      1 => (24.0, 620.0, J.ice, J.s3),
      2 => (20.5, 580.0, J.ice, J.s3),
      3 => (17.5, 560.0, J.text, J.s2),
      4 => (16.0, 580.0, J.text, J.s1),
      _ => (14.0, 600.0, J.text2, J.s1),
    };
    final style = J.title.copyWith(
      fontSize: size,
      fontVariations: [FontVariation('wght', weight)],
      color: color,
      height: 1.25,
      letterSpacing: h.level <= 2 ? -0.1 : 0.2,
    );
    final text = Text.rich(_span(h.spans, style));
    return Padding(
      padding: EdgeInsets.only(top: top),
      child: h.level <= 2
          ? Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              text,
              const SizedBox(height: J.s2),
              Container(height: 1, color: h.level == 1 ? J.lineBright : J.line),
            ])
          : text,
    );
  }

  Widget _list(MdList l, int depth, bool dim) {
    const bullets = ['•', '–', '◦'];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (var i = 0; i < l.items.length; i++)
          Padding(
            padding: EdgeInsets.only(top: i == 0 ? 0 : J.s1 + 1, left: depth == 0 ? J.s1 : 0),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              SizedBox(
                width: l.ordered ? 28 : 18,
                child: l.checked[i] != null
                    ? Padding(
                        padding: const EdgeInsets.only(top: 3),
                        child: Icon(
                          l.checked[i]! ? Icons.check_box_rounded : Icons.check_box_outline_blank_rounded,
                          size: 17,
                          color: l.checked[i]! ? J.cyan : J.text3,
                        ),
                      )
                    : Padding(
                        padding: const EdgeInsets.only(top: 1),
                        child: Text(
                          l.ordered ? '${l.start + i}.' : bullets[depth % bullets.length],
                          textAlign: TextAlign.right,
                          style: (l.ordered ? J.mono(14, color: J.cyanDeep, weight: 500) : J.body.copyWith(color: J.cyanDeep)).copyWith(height: 1.5),
                        ),
                      ),
              ),
              const SizedBox(width: J.s2),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: _blockWidgets(l.items[i], depth: depth + 1, dim: dim, tight: true),
                ),
              ),
            ]),
          ),
      ],
    );
  }

  Widget _table(MdTable t) {
    TextAlign align(int c) => switch (t.aligns[c]) {
          MdAlign.center => TextAlign.center,
          MdAlign.right => TextAlign.right,
          MdAlign.left => TextAlign.left,
        };
    Widget cell(List<MdSpan> spans, int c, {required bool head}) => ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 260),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: J.s3, vertical: 9),
            child: Text.rich(
              _span(
                head ? [for (final s in spans) MdSpan(s.text, bold: true, italic: s.italic, strike: s.strike, code: s.code, href: s.href)] : spans,
                J.body.copyWith(fontSize: 14.5, height: 1.4, color: head ? J.ice : J.text),
              ),
              textAlign: align(c),
            ),
          ),
        );
    return Container(
      decoration: BoxDecoration(border: Border.all(color: J.lineBright), borderRadius: BorderRadius.circular(J.radius)),
      clipBehavior: Clip.antiAlias,
      // wide tables scroll sideways instead of squeezing the columns on a phone
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Table(
          defaultColumnWidth: const IntrinsicColumnWidth(),
          border: const TableBorder(horizontalInside: BorderSide(color: J.line), verticalInside: BorderSide(color: J.line)),
          children: [
            TableRow(
              decoration: const BoxDecoration(color: Color(0xFF0F1D27)),
              children: [for (var c = 0; c < t.header.length; c++) cell(t.header[c], c, head: true)],
            ),
            for (final r in t.rows) TableRow(children: [for (var c = 0; c < r.length; c++) cell(r[c], c, head: false)]),
          ],
        ),
      ),
    );
  }
}

/// Fenced code with syntax colours; the whole block scrolls sideways, the copy button sits in the header.
class CodeBlock extends StatefulWidget {
  const CodeBlock({super.key, required this.code, required this.lang});
  final String code;
  final String lang;

  @override
  State<CodeBlock> createState() => _CodeBlockState();
}

class _CodeBlockState extends State<CodeBlock> {
  bool _copied = false;

  Future<void> _copy() async {
    await Clipboard.setData(ClipboardData(text: widget.code));
    HapticFeedback.selectionClick();
    if (!mounted) return;
    setState(() => _copied = true);
    await Future<void>.delayed(const Duration(milliseconds: 1600));
    if (mounted) setState(() => _copied = false);
  }

  @override
  Widget build(BuildContext context) {
    final style = J.mono(13, color: J.text, weight: 420, spacing: 0).copyWith(height: 1.45);
    return Container(
      decoration: BoxDecoration(
        color: const Color(0xFF070E14),
        border: Border.all(color: J.lineBright),
        borderRadius: BorderRadius.circular(J.radius + 2),
      ),
      clipBehavior: Clip.antiAlias,
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Container(
          color: const Color(0xFF0C1620),
          padding: const EdgeInsets.only(left: J.s3),
          child: Row(children: [
            Expanded(child: Text(widget.lang.isEmpty ? 'CODE' : widget.lang.toUpperCase(), style: J.caps)),
            GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: _copy,
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: J.s3, vertical: J.s2 + 2),
                child: Row(mainAxisSize: MainAxisSize.min, children: [
                  Icon(_copied ? Icons.check_rounded : Icons.copy_rounded, size: 14, color: _copied ? J.green : J.text2),
                  const SizedBox(width: 6),
                  Text(_copied ? 'Kopiert' : 'Kopieren', style: J.mono(11, color: _copied ? J.green : J.text2)),
                ]),
              ),
            ),
          ]),
        ),
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.all(J.s3),
          child: Text.rich(TextSpan(style: style, children: highlightCode(widget.code, widget.lang))),
        ),
      ]),
    );
  }
}

// ------------------------------------------------------------------ syntax colours
const _kwColor = Color(0xFF5CE1FF);
const _strColor = Color(0xFF8FE3A8);
const _numColor = Color(0xFFFFB547);
const _cmtColor = Color(0xFF5E7F8F);
const _typeColor = Color(0xFFB9A6FF);
const _fnColor = Color(0xFFCFF4FF);

const _keywords = {
  'abstract', 'as', 'async', 'await', 'break', 'case', 'catch', 'class', 'const', 'continue', 'def', 'default', 'del',
  'do', 'elif', 'else', 'enum', 'except', 'export', 'extends', 'false', 'final', 'finally', 'fn', 'for', 'from',
  'func', 'function', 'if', 'implements', 'import', 'in', 'interface', 'is', 'lambda', 'let', 'match', 'mod', 'new',
  'nil', 'none', 'null', 'of', 'override', 'package', 'pass', 'private', 'protected', 'pub', 'public', 'raise',
  'return', 'self', 'static', 'struct', 'super', 'switch', 'this', 'throw', 'trait', 'true', 'try', 'type', 'typeof',
  'undefined', 'use', 'var', 'void', 'while', 'with', 'yield', 'select', 'insert', 'update', 'delete', 'where',
  'join', 'create', 'table', 'into', 'values', 'order', 'by', 'group', 'limit', 'and', 'or', 'not', 'echo', 'fi',
  'then', 'done', 'sudo', 'git', 'npm', 'cd', 'ls', 'dart', 'flutter', 'uv', 'pip',
};

const _hashComment = {
  'python', 'py', 'sh', 'bash', 'zsh', 'shell', 'yaml', 'yml', 'toml', 'ruby', 'rb', 'dockerfile', 'makefile', 'ini',
  'conf', 'r', 'perl', 'env', 'properties',
};
const _dashComment = {'sql', 'lua', 'haskell'};

final Map<String, RegExp> _tokenCache = {};
final _upperStart = RegExp(r'[A-Z]');

bool _isKeyword(String w) =>
    _keywords.contains(w) ||
    const {'True', 'False', 'None', 'NULL'}.contains(w) ||
    (w.length > 1 && w == w.toUpperCase() && _keywords.contains(w.toLowerCase())); // SQL: SELECT, WHERE …

RegExp _tokenizer(String lang) {
  final l = lang.toLowerCase();
  final comment = _hashComment.contains(l)
      ? r'#[^\n]*'
      : _dashComment.contains(l)
          ? r'--[^\n]*'
          : r'//[^\n]*|/\*[\s\S]*?(?:\*/|$)';
  return _tokenCache.putIfAbsent(
    comment,
    () => RegExp(
      '(?<comment>$comment)'
      r'''|(?<string>"""[\s\S]*?(?:"""|$)|"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?|`(?:\\.|[^`\\])*`?)'''
      r'|(?<number>\b0x[0-9a-fA-F]+\b|\b\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?\b)'
      r'|(?<word>[A-Za-z_][A-Za-z0-9_]*)',
    ),
  );
}

/// Light-weight highlighter (comments, strings, numbers, keywords, types, calls); `diff` colours added / removed lines.
List<TextSpan> highlightCode(String code, String lang) {
  if (lang.toLowerCase() == 'diff') {
    return [
      for (final line in code.split('\n'))
        TextSpan(
          text: '$line\n',
          style: TextStyle(
            color: line.startsWith('+') ? _strColor : (line.startsWith('-') ? J.red : (line.startsWith('@@') ? _kwColor : J.text2)),
          ),
        ),
    ];
  }
  final re = _tokenizer(lang);
  final spans = <TextSpan>[];
  var last = 0;
  for (final m in re.allMatches(code)) {
    if (m.start > last) spans.add(TextSpan(text: code.substring(last, m.start)));
    final text = m.group(0)!;
    if (m.namedGroup('comment') != null) {
      spans.add(TextSpan(text: text, style: const TextStyle(color: _cmtColor, fontStyle: FontStyle.italic)));
    } else if (m.namedGroup('string') != null) {
      spans.add(TextSpan(text: text, style: const TextStyle(color: _strColor)));
    } else if (m.namedGroup('number') != null) {
      spans.add(TextSpan(text: text, style: const TextStyle(color: _numColor)));
    } else if (_isKeyword(text)) {
      spans.add(TextSpan(text: text, style: const TextStyle(color: _kwColor)));
    } else if (text.startsWith(_upperStart)) {
      spans.add(TextSpan(text: text, style: const TextStyle(color: _typeColor)));
    } else if (m.end < code.length && code[m.end] == '(') {
      spans.add(TextSpan(text: text, style: const TextStyle(color: _fnColor)));
    } else {
      spans.add(TextSpan(text: text));
    }
    last = m.end;
  }
  if (last < code.length) spans.add(TextSpan(text: code.substring(last)));
  return spans;
}
