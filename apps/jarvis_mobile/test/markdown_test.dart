import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/highlight.dart';
import 'package:jarvis_mobile/core/markdown.dart';

void main() {
  group('blocks', () {
    test('headings, paragraphs, rules', () {
      final b = parseMarkdown('# Titel\n\nErster Absatz\nzweite Zeile\n\n---\n\n### Klein');
      expect(b.map((x) => x.runtimeType), [BHeading, BPara, BRule, BHeading]);
      expect((b[0] as BHeading).level, 1);
      expect((b[3] as BHeading).level, 3);
      expect(inlineText((b[1] as BPara).c), 'Erster Absatz\nzweite Zeile');
    });

    test('fenced code keeps its content; unterminated fence is open (streaming)', () {
      final b = parseMarkdown('Text\n\n```ts\nconst a = 1;\n\n  indented\n```\nDanach');
      expect(b.map((x) => x.runtimeType), [BPara, BCode, BPara]);
      final code = b[1] as BCode;
      expect((code.lang, code.v, code.open), ('ts', 'const a = 1;\n\n  indented', false));
      final open = parseMarkdown('```python\nprint(1)').single as BCode;
      expect((open.lang, open.v, open.open), ('python', 'print(1)', true));
    });

    test('lists: bullets with nesting, ordered, tasks', () {
      final b = parseMarkdown('- eins\n- zwei\n  - innen a\n  - innen b\n- drei\n\n1. a\n2. b\n\n- [x] fertig\n- [ ] offen');
      expect(b.map((x) => x.runtimeType), [BList, BList, BList]);
      final l0 = b[0] as BList;
      expect(l0.items.length, 3);
      final nested = l0.items[1].c.whereType<BList>().single;
      expect(nested.items.length, 2);
      expect((b[1] as BList).ordered, true);
      expect((b[2] as BList).items.map((i) => i.checked), [true, false]);
    });

    test('a list may follow a paragraph line directly', () {
      expect(parseMarkdown('Zutaten:\n- Mehl\n- Zucker').map((x) => x.runtimeType), [BPara, BList]);
    });

    test('tables: alignment, short rows are padded, pipes in code stay', () {
      final t = parseMarkdown('| Name | Wert | Rest |\n|:--|--:|:-:|\n| a | 1 |\n| `b|c` | 2 | x |').single as BTable;
      expect(t.align, ['left', 'right', 'center']);
      expect(t.head.map(inlineText), ['Name', 'Wert', 'Rest']);
      expect(t.rows.length, 2);
      expect(t.rows[0].map(inlineText), ['a', '1', '']);
      expect(inlineText(t.rows[1][0]), 'b|c');
    });

    test('a pipe in plain text is not a table', () {
      expect(parseMarkdown('a | b\nkein Trenner').single, isA<BPara>());
    });

    test('quotes nest blocks', () {
      final q = parseMarkdown('> Zitat\n> - Punkt\n>\n> Ende').single as BQuote;
      expect(q.c.map((x) => x.runtimeType), [BPara, BList, BPara]);
    });
  });

  group('inline', () {
    test('emphasis, strong, strike, code', () {
      final n = parseInline('**fett** und *kursiv* und ~~weg~~ und `code`');
      expect(n[0], isA<IStrong>());
      expect(n[2], isA<IEm>());
      expect(n[4], isA<IDel>());
      expect((n[6] as ICode).v, 'code');
    });

    test('snake_case, math stars and unmatched markers stay literal', () {
      expect(inlineText(parseInline('my_var_name und 5 * 3 * 2 und **offen')), 'my_var_name und 5 * 3 * 2 und **offen');
      expect(parseInline('my_var_name').single, isA<IText>());
      expect((parseInline('`a*b*c`').single as ICode).v, 'a*b*c');
    });

    test('links and autolinks; trailing punctuation is trimmed', () {
      final n = parseInline('Siehe [Doku](https://x.de/a_(b)) oder https://y.de/pfad, ok.');
      expect((n[1] as ILink).href, 'https://x.de/a_(b)');
      expect((n[3] as ILink).href, 'https://y.de/pfad');
      expect(inlineText(n), 'Siehe Doku oder https://y.de/pfad, ok.');
    });

    test('unsafe schemes never become links; raw html stays text', () {
      final n = parseInline('[klick](javascript:alert(1)) [x](data:text/html,hi)');
      expect(n.whereType<ILink>(), isEmpty);
      expect(safeHref('javascript:alert(1)'), isNull);
      expect(safeHref(' https://ok.de '), 'https://ok.de');
      expect(safeHref('mailto:a@b.de'), 'mailto:a@b.de');
      expect((parseInline('<img src=x onerror=alert(1)>').single as IText).v, '<img src=x onerror=alert(1)>');
    });

    test('escapes and line breaks', () {
      expect(inlineText(parseInline(r'\*kein em\*')), '*kein em*');
      expect(parseInline('a\nb').map((x) => x.runtimeType), [IText, IBreak, IText]);
    });
  });

  group('highlight', () {
    String join(String code, String lang) => highlight(code, lang).map((t) => t.v).join();

    test('never changes the text', () {
      const src = 'def f(x):\n    # kommentar\n    return "s" + str(3.5)  # ende\n';
      for (final l in ['python', 'ts', 'bash', 'json', 'html', 'diff', 'sql', 'unbekannt', '']) {
        expect(join(src, l), src, reason: l);
      }
    });

    test('keywords, strings, numbers, comments', () {
      final t = highlight('const x = "hi"; // c\nlet n = 42', 'typescript');
      List<String> by(Tok k) => t.where((x) => x.k == k).map((x) => x.v).toList();
      expect(by(Tok.kw), ['const', 'let']);
      expect(by(Tok.str), ['"hi"']);
      expect(by(Tok.com), ['// c']);
      expect(by(Tok.num), ['42']);
    });

    test('json keys differ from string values', () {
      final t = highlight('{"a": "b", "n": 1}', 'json');
      expect(t.where((x) => x.k == Tok.attr).map((x) => x.v), ['"a"', '"n"']);
      expect(t.where((x) => x.k == Tok.str).map((x) => x.v), ['"b"']);
    });

    test('unterminated strings and comments do not run away', () {
      expect(join("let s = 'offen\nnext", 'js'), "let s = 'offen\nnext");
      expect(join('/* offen', 'js'), '/* offen');
    });
  });
}
