import 'dart:async';

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../core/highlight.dart';
import '../../core/markdown.dart';
import '../../core/theme.dart';
import 'hud.dart';

const _tokenColors = <Tok, Color>{
  Tok.kw: J.cyan,
  Tok.str: J.amber,
  Tok.num: Color(0xFFB49CF0),
  Tok.com: J.text3,
  Tok.fn: J.green,
  Tok.type: Color(0xFFE8C27A),
  Tok.attr: Color(0xFFE5846F),
};

/// Chat answer as formatted text: headings, lists, quotes, inline code, code blocks with highlighting and a
/// copy button, and tables that scroll sideways instead of breaking. Put it inside a [SelectionArea] to make
/// the text selectable.
class MarkdownView extends StatefulWidget {
  const MarkdownView({super.key, required this.text, this.streaming = false});
  final String text;
  final bool streaming;

  @override
  State<MarkdownView> createState() => _MarkdownViewState();
}

class _MarkdownViewState extends State<MarkdownView> {
  // The widget tree is built here (not in build) so link recognizers have a clear lifetime:
  // they are disposed exactly when the tree that owns them is replaced.
  final _taps = <TapGestureRecognizer>[];
  List<Block> _blocks = const [];
  Widget _tree = const SizedBox.shrink();

  void _rebuild({bool reparse = true}) {
    _disposeTaps();
    if (reparse) _blocks = parseMarkdown(widget.text);
    _tree = _blocks_(_blocks);
  }

  void _disposeTaps() {
    for (final r in _taps) {
      r.dispose();
    }
    _taps.clear();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _rebuild();
  }

  @override
  void didUpdateWidget(MarkdownView old) {
    super.didUpdateWidget(old);
    if (old.text != widget.text) {
      _rebuild();
    } else if (old.streaming != widget.streaming) {
      _rebuild(reparse: false);
    }
  }

  @override
  void dispose() {
    _disposeTaps();
    super.dispose();
  }

  void _openLink(String href) {
    Clipboard.setData(ClipboardData(text: href));
    final m = ScaffoldMessenger.maybeOf(context);
    m?.hideCurrentSnackBar();
    m?.showSnackBar(SnackBar(content: Text('Link kopiert: $href', maxLines: 2, overflow: TextOverflow.ellipsis), duration: const Duration(seconds: 2)));
  }

  // ------------------------------------------------------------------ inline
  List<InlineSpan> _spans(List<Inline> nodes, TextStyle style) {
    final out = <InlineSpan>[];
    for (final n in nodes) {
      switch (n) {
        case IText(:final v):
          out.add(TextSpan(text: v, style: style));
        case IBreak():
          out.add(TextSpan(text: '\n', style: style));
        case ICode(:final v):
          out.add(TextSpan(
            text: v,
            style: J.mono(13.5, color: J.cyan).copyWith(backgroundColor: J.panel, height: style.height),
          ));
        case IStrong(:final c):
          out.addAll(_spans(c, style.copyWith(fontVariations: const [FontVariation('wght', 650)], color: J.ice)));
        case IEm(:final c):
          out.addAll(_spans(c, style.copyWith(fontStyle: FontStyle.italic)));
        case IDel(:final c):
          out.addAll(_spans(c, style.copyWith(decoration: TextDecoration.lineThrough, color: J.text2)));
        case ILink(:final href, :final c):
          final tap = TapGestureRecognizer()..onTap = () => _openLink(href);
          _taps.add(tap);
          final linkStyle = style.copyWith(color: J.cyan, decoration: TextDecoration.underline, decorationColor: J.cyanDeep);
          for (final s in _spans(c, linkStyle)) {
            out.add(s is TextSpan ? TextSpan(text: s.text, style: s.style, recognizer: tap, children: s.children) : s);
          }
      }
    }
    return out;
  }

  Widget _text(List<Inline> nodes, TextStyle style, {bool caret = false}) {
    final spans = _spans(nodes, style);
    if (caret) spans.add(TextSpan(text: ' ▍', style: style.copyWith(color: J.cyan)));
    return Text.rich(TextSpan(children: spans), style: style);
  }

  // ------------------------------------------------------------------ blocks
  Widget _block(Block b, {bool last = false, double gap = J.s3}) {
    final caret = widget.streaming && last;
    switch (b) {
      case BHeading(:final level, :final c):
        final size = switch (level) { 1 => 22.0, 2 => 20.0, 3 => 18.0, _ => 16.5 };
        final style = J.body.copyWith(
          fontSize: size,
          height: 1.3,
          color: level >= 5 ? J.text2 : J.ice,
          fontVariations: const [FontVariation('wght', 600)],
        );
        return Padding(padding: EdgeInsets.only(top: gap, bottom: J.s2), child: _text(c, style, caret: caret));
      case BPara(:final c):
        return Padding(padding: EdgeInsets.only(bottom: last ? 0 : gap), child: _text(c, J.body, caret: caret));
      case BCode(:final lang, :final v, :final open):
        return Padding(padding: EdgeInsets.only(bottom: last ? 0 : gap), child: _CodeBlock(lang: lang, code: v, open: open && widget.streaming));
      case BQuote(:final c):
        return Padding(
          padding: EdgeInsets.only(bottom: last ? 0 : gap),
          child: Container(
            padding: const EdgeInsets.only(left: J.s3),
            decoration: const BoxDecoration(border: Border(left: BorderSide(color: J.cyanDeep, width: 2))),
            child: _blocks_(c, gap: J.s2),
          ),
        );
      case BRule():
        return Padding(padding: EdgeInsets.only(top: J.s2, bottom: last ? 0 : gap), child: const Hairline());
      case BList(:final ordered, :final start, :final items):
        return Padding(
          padding: EdgeInsets.only(bottom: last ? 0 : gap),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            for (var i = 0; i < items.length; i++)
              Padding(
                padding: const EdgeInsets.only(bottom: J.s1),
                child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  SizedBox(
                    width: ordered ? 28 : 20,
                    child: items[i].checked != null
                        ? Padding(
                            padding: const EdgeInsets.only(top: 3, right: 4),
                            child: Icon(items[i].checked! ? Icons.check_box_rounded : Icons.check_box_outline_blank_rounded,
                                size: 17, color: items[i].checked! ? J.cyan : J.text3),
                          )
                        : Text(ordered ? '${start + i}.' : '•', style: J.body.copyWith(color: J.text3)),
                  ),
                  Expanded(child: _blocks_(items[i].c, gap: J.s1)),
                ]),
              ),
          ]),
        );
      case BTable table:
        return Padding(padding: EdgeInsets.only(bottom: last ? 0 : gap), child: _table(table));
    }
  }

  Widget _blocks_(List<Block> blocks, {double gap = J.s3}) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [for (var i = 0; i < blocks.length; i++) _block(blocks[i], last: i == blocks.length - 1, gap: gap)],
      );

  Widget _table(BTable t) {
    TextAlign align(int k) => switch (t.align[k]) { 'center' => TextAlign.center, 'right' => TextAlign.right, _ => TextAlign.left };
    final cellStyle = J.body.copyWith(fontSize: 14, height: 1.35);
    Widget cell(List<Inline> nodes, int k, TextStyle style) => ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 260),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: J.s3, vertical: J.s2),
            child: Text.rich(TextSpan(children: _spans(nodes, style)), style: style, textAlign: align(k)),
          ),
        );
    final headStyle = cellStyle.copyWith(color: J.ice, fontVariations: const [FontVariation('wght', 600)]);
    return Container(
      decoration: BoxDecoration(border: Border.all(color: J.line), borderRadius: BorderRadius.circular(J.radius)),
      clipBehavior: Clip.antiAlias,
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        physics: const ClampingScrollPhysics(),
        child: Table(
          defaultColumnWidth: const IntrinsicColumnWidth(),
          defaultVerticalAlignment: TableCellVerticalAlignment.top,
          border: const TableBorder(horizontalInside: BorderSide(color: J.line)),
          children: [
            TableRow(
              decoration: const BoxDecoration(color: J.panel),
              children: [for (var k = 0; k < t.align.length; k++) cell(t.head[k], k, headStyle)],
            ),
            for (final r in t.rows) TableRow(children: [for (var k = 0; k < t.align.length; k++) cell(r[k], k, cellStyle)]),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) => _tree;
}

class _CodeBlock extends StatefulWidget {
  const _CodeBlock({required this.lang, required this.code, required this.open});
  final String lang;
  final String code;
  final bool open;
  @override
  State<_CodeBlock> createState() => _CodeBlockState();
}

class _CodeBlockState extends State<_CodeBlock> {
  bool _copied = false;
  Timer? _timer;

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _copy() async {
    await Clipboard.setData(ClipboardData(text: widget.code));
    HapticFeedback.selectionClick();
    setState(() => _copied = true);
    _timer?.cancel();
    _timer = Timer(const Duration(milliseconds: 1600), () {
      if (mounted) setState(() => _copied = false);
    });
  }

  @override
  Widget build(BuildContext context) {
    final base = J.mono(12.5, color: J.text, spacing: 0);
    final spans = <InlineSpan>[
      for (final t in highlight(widget.code, widget.lang))
        TextSpan(
          text: t.v,
          style: t.k == null ? null : base.copyWith(color: _tokenColors[t.k], fontStyle: t.k == Tok.com ? FontStyle.italic : null),
        ),
      if (widget.open) TextSpan(text: '▍', style: base.copyWith(color: J.cyan)),
    ];
    return Container(
      decoration: BoxDecoration(color: J.panel, border: Border.all(color: J.line), borderRadius: BorderRadius.circular(J.radius)),
      clipBehavior: Clip.antiAlias,
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Container(
          padding: const EdgeInsets.only(left: J.s3),
          decoration: const BoxDecoration(border: Border(bottom: BorderSide(color: J.line))),
          child: Row(children: [
            Text(widget.lang.isEmpty ? 'TEXT' : widget.lang.toUpperCase(), style: J.caps),
            const Spacer(),
            Pressable(
              onTap: _copy,
              haptic: false,
              semantics: _copied ? 'Kopiert' : 'Code kopieren',
              child: SizedBox(
                height: 36,
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: J.s3),
                  child: Row(mainAxisSize: MainAxisSize.min, children: [
                    Icon(_copied ? Icons.check_rounded : Icons.copy_rounded, size: 15, color: _copied ? J.green : J.text2),
                    const SizedBox(width: 6),
                    Text(_copied ? 'Kopiert' : 'Kopieren', style: J.mono(11, color: _copied ? J.green : J.text2)),
                  ]),
                ),
              ),
            ),
          ]),
        ),
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          physics: const ClampingScrollPhysics(),
          padding: const EdgeInsets.all(J.s3),
          child: Text.rich(TextSpan(children: spans, style: base), softWrap: false),
        ),
      ]),
    );
  }
}
