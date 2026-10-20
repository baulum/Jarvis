// Tiny syntax highlighter for chat code blocks – same rules as the desktop (desktop/src/lib/highlight.ts).
// One scanner for C-like / script languages, parameterised by keywords and comment syntax; JSON, diff and
// HTML/XML have their own small rules. Pure Dart: the widget layer only maps token kinds to colors.

enum Tok { kw, str, num, com, fn, type, attr }

class Token {
  Token(this.v, [this.k]);
  String v;
  final Tok? k;
}

class _Lang {
  const _Lang(this.kw, {this.line = const [], this.block, this.quotes = '', this.triple = false, this.dollar = false, this.ci = false});
  final Set<String> kw;
  final List<String> line;
  final (String, String)? block;
  final String quotes;
  final bool triple;
  final bool dollar;
  final bool ci;
}

Set<String> _w(String s) => s.split(RegExp(r'\s+')).where((e) => e.isNotEmpty).toSet();

final _js = _w('const let var function return if else for while do switch case break continue new class extends super this import export default from as async await yield try catch finally throw typeof instanceof in of void delete null undefined true false interface type enum implements private public protected readonly static abstract declare namespace keyof satisfies get set');
final _py = _w('def class return if elif else for while break continue pass import from as with try except finally raise lambda yield async await in is not and or None True False self global nonlocal del assert match case');
final _sh = _w('if then else elif fi for while until do done case esac in function return exit local export readonly set unset source alias cd echo true false');
final _rust = _w('fn let mut const static struct enum impl trait pub use mod crate self Self super match if else for while loop break continue return as in ref move async await dyn where type unsafe extern true false');
final _go = _w('func var const type struct interface map chan package import return if else for range switch case default break continue go defer select fallthrough nil true false');
final _swift = _w('func let var class struct enum protocol extension init deinit return if else guard switch case default for while repeat break continue import in is as try catch throw throws async await actor nil true false self super private public internal fileprivate static final override some any where');
final _kotlin = _w('fun val var class object interface data sealed enum companion return if else when for while do break continue import package in is as try catch finally throw null true false this super override open abstract private public internal protected suspend inline lateinit by init typealias');
final _dart = _w('class extends implements with mixin abstract final const var void dynamic late required static return if else for while do switch case default break continue import export part library as is in try catch finally throw on rethrow new null true false this super async await yield Future Stream enum typedef factory get set operator');
final _java = _w('class interface enum extends implements public private protected static final abstract void int long double float boolean char byte short return if else for while do switch case default break continue new import package try catch finally throw throws this super null true false instanceof synchronized volatile var record');
final _clike = _w('int long short char float double void unsigned signed const static struct union enum typedef class public private protected namespace template typename using return if else for while do switch case default break continue new delete sizeof nullptr NULL true false include define auto inline virtual override');
final _sql = _w('select from where group by order having limit offset insert into values update set delete create table alter drop index join left right inner outer full on as and or not null is in like between exists union all distinct case when then else end primary key foreign references default unique count sum avg min max asc desc with');

final _langs = <String, _Lang>{
  'js': _Lang(_js, line: ['//'], block: ('/*', '*/'), quotes: '"\'`'),
  'py': _Lang(_py, line: ['#'], quotes: '"\'', triple: true),
  'sh': _Lang(_sh, line: ['#'], quotes: '"\'', dollar: true),
  'rust': _Lang(_rust, line: ['//'], block: ('/*', '*/'), quotes: '"'),
  'go': _Lang(_go, line: ['//'], block: ('/*', '*/'), quotes: '"\'`'),
  'swift': _Lang(_swift, line: ['//'], block: ('/*', '*/'), quotes: '"'),
  'kotlin': _Lang(_kotlin, line: ['//'], block: ('/*', '*/'), quotes: '"'),
  'dart': _Lang(_dart, line: ['//'], block: ('/*', '*/'), quotes: '"\''),
  'java': _Lang(_java, line: ['//'], block: ('/*', '*/'), quotes: '"'),
  'c': _Lang(_clike, line: ['//'], block: ('/*', '*/'), quotes: '"\''),
  'sql': _Lang(_sql, line: ['--'], block: ('/*', '*/'), quotes: '\'"', ci: true),
  'css': _Lang(<String>{}, block: ('/*', '*/'), quotes: '"\''),
  'yaml': _Lang(_w('true false null yes no'), line: ['#'], quotes: '"\''),
  'toml': _Lang(_w('true false'), line: ['#'], quotes: '"\''),
};

const _aliases = <String, String>{
  'javascript': 'js', 'jsx': 'js', 'ts': 'js', 'tsx': 'js', 'typescript': 'js', 'mjs': 'js', 'cjs': 'js',
  'python': 'py', 'python3': 'py',
  'bash': 'sh', 'shell': 'sh', 'zsh': 'sh', 'console': 'sh', 'terminal': 'sh',
  'rs': 'rust', 'golang': 'go', 'kt': 'kotlin', 'kts': 'kotlin',
  'cpp': 'c', 'c++': 'c', 'h': 'c', 'hpp': 'c', 'cc': 'c', 'cs': 'java', 'csharp': 'java', 'objc': 'c',
  'postgres': 'sql', 'postgresql': 'sql', 'mysql': 'sql', 'sqlite': 'sql',
  'scss': 'css', 'less': 'css', 'yml': 'yaml',
};

String canonicalLang(String lang) {
  final l = lang.toLowerCase();
  return _aliases[l] ?? l;
}

const _maxHighlight = 20000;
final _number = RegExp(r'0[xX][\da-fA-F_]+|0[bB][01_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?');
final _ident = RegExp(r'[A-Za-z_$][\w$]*');
final _digit = RegExp(r'\d');
final _wordCh = RegExp(r'[\w$]');
final _identStart = RegExp(r'[A-Za-z_$]');
final _callParen = RegExp(r'^\s*\(');
final _capitalized = RegExp(r'^[A-Z][a-z0-9]');
// comments, tags, attribute names, quoted values (double quote or apostrophe)
const _markupPattern = '<!--[\\s\\S]*?-->|</?[A-Za-z][\\w:-]*|/?>|[A-Za-z_:][\\w:.-]*(?==)|"[^"\\n]*"|\'[^\'\\n]*\'';
final _dollarVar =RegExp(r'\$(?:\{[^}]*\}|[A-Za-z_]\w*|[0-9@#?*!$-])');

void _push(List<Token> out, String v, [Tok? k]) {
  if (v.isEmpty) return;
  if (out.isNotEmpty && out.last.k == k) {
    out.last.v += v;
  } else {
    out.add(Token(v, k));
  }
}

String? _matchAt(RegExp re, String text, int at) {
  final m = re.matchAsPrefix(text, at);
  return m?.group(0);
}

List<Token> _scan(String code, _Lang lang) {
  final out = <Token>[];
  var i = 0;
  final n = code.length;
  while (i < n) {
    String? lineC;
    for (final c in lang.line) {
      if (code.startsWith(c, i)) {
        lineC = c;
        break;
      }
    }
    if (lineC != null) {
      var end = code.indexOf('\n', i);
      if (end == -1) end = n;
      _push(out, code.substring(i, end), Tok.com);
      i = end;
      continue;
    }
    final block = lang.block;
    if (block != null && code.startsWith(block.$1, i)) {
      final end = code.indexOf(block.$2, i + block.$1.length);
      final stop = end == -1 ? n : end + block.$2.length;
      _push(out, code.substring(i, stop), Tok.com);
      i = stop;
      continue;
    }
    final ch = code[i];
    if (lang.quotes.contains(ch)) {
      final triple = lang.triple && code.startsWith(ch * 3, i);
      final close = triple ? ch * 3 : ch;
      final multi = triple || ch == '`';
      var j = i + close.length;
      while (j < n) {
        if (code[j] == '\\') {
          j += 2;
          continue;
        }
        if (code.startsWith(close, j)) {
          j += close.length;
          break;
        }
        if (!multi && code[j] == '\n') break; // unterminated: stop at the line end
        j++;
      }
      final stop = j < n ? j : n;
      _push(out, code.substring(i, stop), Tok.str);
      i = stop;
      continue;
    }
    if (lang.dollar && ch == '\$') {
      final m = _matchAt(_dollarVar, code, i);
      if (m != null) {
        _push(out, m, Tok.attr);
        i += m.length;
        continue;
      }
    }
    if (_digit.hasMatch(ch) && !(i > 0 && _wordCh.hasMatch(code[i - 1]))) {
      final m = _matchAt(_number, code, i);
      if (m != null) {
        _push(out, m, Tok.num);
        i += m.length;
        continue;
      }
    }
    if (_identStart.hasMatch(ch)) {
      final m = _matchAt(_ident, code, i)!;
      final key = lang.ci ? m.toLowerCase() : m;
      Tok? kind;
      if (lang.kw.contains(key)) {
        kind = Tok.kw;
      } else if (_callParen.hasMatch(code.substring(i + m.length, (i + m.length + 8) < n ? i + m.length + 8 : n))) {
        kind = Tok.fn;
      } else if (_capitalized.hasMatch(m)) {
        kind = Tok.type;
      }
      _push(out, m, kind);
      i += m.length;
      continue;
    }
    _push(out, ch);
    i++;
  }
  return out;
}

List<Token> _json(String code) {
  final out = <Token>[];
  final re = RegExp(r'"(?:[^"\\\n]|\\.)*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\btrue\b|\bfalse\b|\bnull\b');
  var last = 0;
  for (final m in re.allMatches(code)) {
    _push(out, code.substring(last, m.start));
    final v = m.group(0)!;
    final tailEnd = m.end + 6 < code.length ? m.end + 6 : code.length;
    final isKey = v.startsWith('"') && RegExp(r'^\s*:').hasMatch(code.substring(m.end, tailEnd));
    _push(out, v, isKey ? Tok.attr : v.startsWith('"') ? Tok.str : RegExp(r'^[-\d]').hasMatch(v) ? Tok.num : Tok.kw);
    last = m.end;
  }
  _push(out, code.substring(last));
  return out;
}

List<Token> _markup(String code) {
  final out = <Token>[];
  final re = RegExp(_markupPattern);
  var last = 0;
  for (final m in re.allMatches(code)) {
    _push(out, code.substring(last, m.start));
    final v = m.group(0)!;
    _push(
      out,
      v,
      v.startsWith('<!--')
          ? Tok.com
          : (v.startsWith('<') || v.endsWith('>'))
              ? Tok.kw
              : RegExp(r'''^["']''').hasMatch(v)
                  ? Tok.str
                  : Tok.attr,
    );
    last = m.end;
  }
  _push(out, code.substring(last));
  return out;
}

List<Token> _diff(String code) {
  final out = <Token>[];
  final lines = code.split('\n');
  for (var idx = 0; idx < lines.length; idx++) {
    final l = lines[idx];
    Tok? kind;
    if (l.startsWith('+++') || l.startsWith('---') || l.startsWith('diff ') || l.startsWith('index ')) {
      kind = Tok.com;
    } else if (l.startsWith('@@')) {
      kind = Tok.type;
    } else if (l.startsWith('+')) {
      kind = Tok.fn;
    } else if (l.startsWith('-')) {
      kind = Tok.attr;
    }
    out.add(Token(l, kind));
    if (idx < lines.length - 1) out.add(Token('\n'));
  }
  return out;
}

/// Token list for [code]; unknown languages come back as one plain token.
List<Token> highlight(String code, String lang) {
  final l = canonicalLang(lang);
  if (code.length > _maxHighlight) return [Token(code)];
  if (l == 'json' || l == 'jsonc' || l == 'json5') return _json(code);
  if (l == 'diff' || l == 'patch') return _diff(code);
  if (l == 'html' || l == 'xml' || l == 'svg' || l == 'vue') return _markup(code);
  final spec = _langs[l];
  if (spec == null) return [Token(code)];
  return _scan(code, spec);
}
