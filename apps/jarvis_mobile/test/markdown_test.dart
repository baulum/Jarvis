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

  test('emphasis nested in bold closes at a shared marker run', () {
    final p = (parseMarkdown('**Fett *mit kursiv***').single as MdParagraph).spans;
    expect(spansText(p), 'Fett mit kursiv');
    expect(p.where((s) => s.bold && s.italic).single.text, 'mit kursiv');
    expect(spansText((parseMarkdown('**fett***').single as MdParagraph).spans), 'fett*');
  });

  test('list indentation with tabs counts like spaces', () {
    final l = parseMarkdown('\t- eins\n\t- zwei\n\t\t- unter').single as MdList;
    expect(l.items.length, 2);
    expect(l.items[1][1], isA<MdList>());
  });

  test('headings, paragraphs and rules', () {
    final b = parseMarkdown('# Titel\n\nErster Absatz\nzweite Zeile\n\n---\n\n### Klein');
    expect(b.map((x) => x.runtimeType), [MdHeading, MdParagraph, MdRule, MdHeading]);
    expect((b[0] as MdHeading).level, 1);
    expect((b[3] as MdHeading).level, 3);
    expect(spansText((b[1] as MdParagraph).spans), 'Erster Absatz\nzweite Zeile');
  });

  test('fenced code with blank lines and a longer fence stays verbatim', () {
    final b = parseMarkdown('Text\n\n```ts\nconst a = 1;\n\n  indented\n```\nDanach');
    expect(b.map((x) => x.runtimeType), [MdParagraph, MdCode, MdParagraph]);
    expect((b[1] as MdCode).code, 'const a = 1;\n\n  indented');
    final nested = parseMarkdown('````md\n```js\nx\n```\n````').single as MdCode;
    expect(nested.code, '```js\nx\n```');
  });

  test('task lists next to normal lists', () {
    final l = parseMarkdown('- [x] fertig\n- [ ] offen\n- normal').single as MdList;
    expect(l.checked, [true, false, null]);
    expect(spansText((l.items[0].single as MdParagraph).spans), 'fertig');
    expect((parseMarkdown('- eins\n- zwei').single as MdList).checked, [null, null]);
  });

  test('a list may follow a paragraph line directly', () {
    expect(parseMarkdown('Zutaten:\n- Mehl\n- Zucker').map((x) => x.runtimeType), [MdParagraph, MdList]);
  });

  test('short table rows are padded and a pipe in plain text is no table', () {
    final t = parseMarkdown('| Name | Wert | Rest |\n|:--|--:|:-:|\n| a | 1 |').single as MdTable;
    expect(t.aligns, [MdAlign.left, MdAlign.right, MdAlign.center]);
    expect(t.rows.single.map(spansText), ['a', '1', '']);
    expect(parseMarkdown('a | b\nkein Trenner').single, isA<MdParagraph>());
  });

  test('quotes nest blocks', () {
    final q = parseMarkdown('> Zitat\n> - Punkt\n>\n> Ende').single as MdQuote;
    expect(q.blocks.map((x) => x.runtimeType), [MdParagraph, MdList, MdParagraph]);
  });

  test('code spans keep their markers, raw html and escapes stay text', () {
    final code = parseInline('`a*b*c`').single;
    expect(code.code, isTrue);
    expect(code.text, 'a*b*c');
    expect(spansText(parseInline('<img src=x onerror=alert(1)>')), '<img src=x onerror=alert(1)>');
    expect(spansText(parseInline(r'\*kein em\*')), '*kein em*');
  });

  test('links keep balanced parentheses; only web and mail targets become links', () {
    final links = parseInline('Siehe [Doku](https://x.de/a_(b)) oder https://y.de/pfad, ok.').where((s) => s.href != null);
    expect(links.map((s) => s.href), ['https://x.de/a_(b)', 'https://y.de/pfad']);
    final unsafe = parseInline('[klick](javascript:alert(1)) [x](data:text/html,hi)');
    expect(unsafe.where((s) => s.href != null), isEmpty);
    expect(spansText(unsafe), 'klick x');
    expect(safeHref('javascript:alert(1)'), isNull);
    expect(safeHref(' https://ok.de '), 'https://ok.de');
    expect(safeHref('mailto:a@b.de'), 'mailto:a@b.de');
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

  testWidgets('task-list items are tappable and show the stored state', (tester) async {
    final toggles = <(int, bool)>[];
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: MarkdownView(
          '- [ ] Schirm\n- [x] Kalender',
          checks: const {'0': true},
          onToggleTask: (i, c) => toggles.add((i, c)),
        ),
      ),
    ));
    // item 0 is unchecked in the text but checked in the stored state
    expect(find.byIcon(Icons.check_box_rounded), findsNWidgets(2));
    await tester.tap(find.byIcon(Icons.check_box_rounded).last);
    expect(toggles, [(1, false)]);
    expect(tester.takeException(), isNull);
  });
}
