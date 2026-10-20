import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/markdown.dart';
import 'package:jarvis_mobile/ui/widgets/markdown_view.dart';

void main() {
  test('headings, emphasis and inline code lose their markers', () {
    final b = parseMarkdown('## Titel\n\nEin **fetter** und *kursiver* Text mit `code` und ~~weg~~.');
    expect((b[0] as MdHeading).level, 2);
    expect(spansText((b[0] as MdHeading).spans), 'Titel');
    final p = (b[1] as MdParagraph).spans;
    expect(spansText(p), 'Ein fetter und kursiver Text mit code und weg.');
    expect(p.where((s) => s.bold).single.text, 'fetter');
    expect(p.where((s) => s.italic).single.text, 'kursiver');
    expect(p.where((s) => s.code).single.text, 'code');
    expect(p.where((s) => s.strike).single.text, 'weg');
  });

  test('nested bullet and numbered lists', () {
    final b = parseMarkdown('- eins\n  - unter a\n  - unter b\n- zwei\n\n1. erst\n2. dann');
    final bullets = b[0] as MdList;
    expect(bullets.ordered, isFalse);
    expect(bullets.items.length, 2);
    final nested = bullets.items[0][1] as MdList;
    expect(nested.items.length, 2);
    final numbered = b[1] as MdList;
    expect(numbered.ordered, isTrue);
    expect(numbered.items.length, 2);
  });

  test('table with alignment, quote, rule and links', () {
    final b = parseMarkdown('| A | B |\n|:--|--:|\n| 1 | 2 |\n\n> zitiert\n\n---\n\n[Doku](https://example.com/x) und https://a.de/b.');
    final t = b[0] as MdTable;
    expect(t.aligns, [MdAlign.left, MdAlign.right]);
    expect(t.rows.length, 1);
    expect(b[1], isA<MdQuote>());
    expect(b[2], isA<MdRule>());
    final links = (b[3] as MdParagraph).spans.where((s) => s.href != null).toList();
    expect(links.map((s) => s.href), ['https://example.com/x', 'https://a.de/b']);
  });

  test('fenced code keeps its content; an unclosed fence (streaming) is code until the end', () {
    final done = parseMarkdown('```dart\nvar a = **1**;\n```\nDanach');
    expect((done[0] as MdCode).lang, 'dart');
    expect((done[0] as MdCode).code, 'var a = **1**;');
    expect(done[1], isA<MdParagraph>());
    final open = parseMarkdown('```py\nprint(1)');
    expect((open.single as MdCode).code, 'print(1)');
  });

  test('snake_case and unmatched markers stay literal', () {
    final p = parseMarkdown('mobile_call_owner und 2 * 3 und **offen').single as MdParagraph;
    expect(spansText(p.spans), 'mobile_call_owner und 2 * 3 und **offen');
  });

  test('syntax highlighting colours keywords, strings, numbers and comments', () {
    final spans = highlightCode('def f(x):\n  return "a" + 42  # fertig', 'python');
    final byText = {for (final s in spans) s.text: s.style?.color};
    expect(byText['def'], isNotNull);
    expect(byText['"a"'], isNotNull);
    expect(byText['42'], isNotNull);
    expect(byText['# fertig'], isNotNull);
  });

  testWidgets('wide tables scroll horizontally, code block has a copy button', (tester) async {
    await tester.pumpWidget(const MaterialApp(
      home: Scaffold(
        body: SizedBox(
          width: 320,
          child: SingleChildScrollView(
            child: MarkdownView('| Spalte eins | Spalte zwei | Spalte drei | Spalte vier |\n|---|---|---|---|\n| aaaaaaaaaaaaaaa | bbbbbbbbbbbbbbb | ccccccccccccccc | ddddddddddddddd |\n\n```sh\nls -la\n```'),
          ),
        ),
      ),
    ));
    expect(find.byType(Table), findsOneWidget);
    expect(find.byWidgetPredicate((w) => w is SingleChildScrollView && w.scrollDirection == Axis.horizontal), findsWidgets);
    expect(find.text('Kopieren'), findsOneWidget);
    expect(find.textContaining('**'), findsNothing);
    expect(tester.takeException(), isNull);
  });
}
