import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme.dart';
import '../../state/providers.dart';
import '../../state/setup.dart';
import '../widgets/hud.dart';

/// Setup after pairing (and later from Einstellungen › Einrichtung): one permission per step, each explained before
/// Android is asked. Nothing is requested without a tap; every step can be skipped; "Fertig" always closes.
class SetupScreen extends ConsumerWidget {
  const SetupScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) => Scaffold(
        backgroundColor: J.void_,
        body: SafeArea(
          child: SetupFlowView(onFinish: () async {
            await ref.read(pairingProvider.notifier).markSetupDone();
            if (!context.mounted) return;
            if (context.canPop()) {
              context.pop();
            } else {
              context.go('/');
            }
          }),
        ),
      );
}

/// The flow itself, independent of routing (tested with a fake [SetupPlatform]).
class SetupFlowView extends ConsumerStatefulWidget {
  const SetupFlowView({super.key, required this.onFinish, this.pollEvery = const Duration(seconds: 2)});
  final Future<void> Function() onFinish;

  /// Re-reads the real state while the flow is visible – some system pages report nothing back.
  final Duration pollEvery;

  @override
  ConsumerState<SetupFlowView> createState() => _SetupFlowViewState();
}

class _SetupFlowViewState extends ConsumerState<SetupFlowView> with WidgetsBindingObserver {
  SetupFlow _flow = const SetupFlow();
  bool _busy = false;
  bool _finishing = false;
  bool _loading = false;
  String? _note;
  Timer? _poll;

  SetupPlatform get _platform => ref.read(setupPlatformProvider);

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _load();
    _poll = Timer.periodic(widget.pollEvery, (_) {
      final life = WidgetsBinding.instance.lifecycleState;
      if (life == null || life == AppLifecycleState.resumed) _load();
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _poll?.cancel();
    super.dispose();
  }

  // back from a system page or dialog: show what is really set now
  @override
  void didChangeAppLifecycleState(AppLifecycleState s) {
    if (s == AppLifecycleState.resumed) _load();
  }

  Future<void> _load() async {
    if (_loading) return;
    _loading = true;
    try {
      final s = await _platform.status().timeout(const Duration(seconds: 5));
      if (mounted) setState(() => _flow = _flow.withStatus(s));
    } catch (e) {
      // never a dead end: without a status the flow still opens (summary with "Fertig")
      if (mounted && !_flow.started) {
        setState(() {
          _flow = _flow.withStatus(const SetupStatus({}));
          _note = 'Der Stand der Berechtigungen ließ sich nicht lesen. Sie können sie später unter Einstellungen prüfen.';
        });
      }
    } finally {
      _loading = false;
    }
  }

  void _go(SetupFlow f) => setState(() {
        _flow = f;
        _note = null;
      });

  Future<void> _grant() async {
    final step = _flow.step;
    if (step == null || _busy) return;
    setState(() {
      _busy = true;
      _note = null;
    });
    String? note;
    try {
      note = await _platform.grant(step.item).timeout(const Duration(minutes: 2));
    } catch (_) {
      note = 'Das hat nicht geklappt. Bitte erneut versuchen oder überspringen.';
    }
    if (!mounted) return;
    setState(() {
      _busy = false;
      if (_flow.current == step.item) _note = note;
    });
    await _load();
  }

  Future<void> _secondary(String kind) async {
    final note = await _platform.openSettings(kind).catchError((Object _) => 'Android konnte die Einstellung nicht öffnen.');
    if (mounted && note != null) setState(() => _note = note);
  }

  Future<void> _finish() async {
    if (_finishing) return;
    setState(() => _finishing = true);
    try {
      await widget.onFinish();
    } finally {
      if (mounted) setState(() => _finishing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final f = _flow;
    if (!f.started) {
      return const Center(child: SizedBox.square(dimension: 18, child: CircularProgressIndicator(strokeWidth: 1.4, color: J.cyan)));
    }
    return PopScope(
      canPop: !f.canGoBack,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop && f.canGoBack) _go(f.back());
      },
      child: Column(children: [
        _header(f),
        Expanded(
          child: AnimatedSwitcher(
            duration: J.base,
            switchInCurve: J.enter,
            child: f.atSummary ? _summary(f) : _stepBody(f, f.step!),
          ),
        ),
        const Hairline(),
        _bar(f),
      ]),
    );
  }

  Widget _header(SetupFlow f) => Padding(
        padding: const EdgeInsets.fromLTRB(J.s5, J.s4, J.s3, J.s2),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(
              child: Caps(f.atSummary ? 'Einrichtung · Übersicht' : 'Einrichtung · ${f.position} von ${f.steps.length}'),
            ),
            if (!f.atSummary) LightAction('Später einrichten', key: const Key('setup-later'), color: J.text2, onTap: () => _go(f.toSummary())),
          ]),
          const SizedBox(height: J.s2),
          Padding(
            padding: const EdgeInsets.only(right: J.s2),
            child: Row(children: [
              for (final s in f.steps)
                Expanded(
                  child: AnimatedContainer(
                    duration: J.base,
                    height: 2,
                    margin: const EdgeInsets.only(right: 3),
                    color: f.current == s.item
                        ? J.cyan
                        : f.stateOf(s.item) == SetupState.granted
                            ? J.green.withValues(alpha: 0.7)
                            : J.lineBright,
                  ),
                ),
            ]),
          ),
        ]),
      );

  Widget _stepBody(SetupFlow f, SetupStep step) {
    final granted = f.stateOf(step.item) == SetupState.granted;
    return ListView(
      key: ValueKey(step.item),
      padding: const EdgeInsets.fromLTRB(J.s5, J.s5, J.s5, J.s5),
      children: [
        Caps(step.need.label, color: step.need == SetupNeed.essential ? J.cyan : J.text3),
        const SizedBox(height: J.s2),
        Text(step.title, style: J.display),
        const SizedBox(height: J.s4),
        Text(step.why, style: J.body),
        const SizedBox(height: J.s5),
        Row(children: [
          Container(
            width: 8,
            height: 8,
            decoration: BoxDecoration(shape: BoxShape.circle, color: granted ? J.green : J.lineBright),
          ),
          const SizedBox(width: J.s3),
          Text(
            granted ? 'Erteilt' : (f.skipped.contains(step.item) ? 'Übersprungen – noch nicht erteilt' : 'Noch nicht erteilt'),
            key: const Key('setup-state'),
            style: J.mono(12, color: granted ? J.green : (step.need == SetupNeed.essential ? J.amber : J.text2)),
          ),
        ]),
        if (!granted) ...[
          const SizedBox(height: J.s5),
          const Caps('So geht es'),
          const SizedBox(height: J.s1),
          Text(step.how, style: J.bodyDim.copyWith(fontSize: 14)),
          if (step.hint != null) ...[
            const SizedBox(height: J.s2),
            Text(step.hint!, style: J.mono(11, color: J.text3)),
          ],
          if (step.secondary != null)
            Align(
              alignment: Alignment.centerLeft,
              child: LightAction(step.secondary!.$1, key: const Key('setup-secondary'), color: J.text2, onTap: () => _secondary(step.secondary!.$2)),
            ),
        ],
        if (_note != null) Padding(padding: const EdgeInsets.only(top: J.s4), child: Text(_note!, style: J.mono(12, color: J.cyan))),
      ],
    );
  }

  Widget _summary(SetupFlow f) {
    final open = f.openEssentials;
    return ListView(
      key: const ValueKey('summary'),
      padding: const EdgeInsets.fromLTRB(J.s5, J.s5, J.s5, J.s5),
      children: [
        Text(open.isEmpty ? 'Eingerichtet.' : 'Fast fertig.', style: J.display),
        const SizedBox(height: J.s3),
        Text(
          '${f.grantedCount} von ${f.steps.length} erteilt. Offenes können Sie jederzeit unter Einstellungen › Einrichtung nachholen.',
          style: J.bodyDim,
        ),
        if (open.isNotEmpty) ...[
          const SizedBox(height: J.s3),
          Text('Ohne ${open.map((s) => s.title).join(' und ')} erreicht Jarvis Sie nicht zuverlässig.', style: J.mono(12, color: J.amber)),
        ],
        if (_note != null) Padding(padding: const EdgeInsets.only(top: J.s3), child: Text(_note!, style: J.mono(12, color: J.cyan))),
        const SizedBox(height: J.s4),
        for (final s in f.steps) ...[
          Padding(
            padding: const EdgeInsets.symmetric(vertical: J.s2),
            child: Row(children: [
              Container(
                width: 8,
                height: 8,
                decoration: BoxDecoration(shape: BoxShape.circle, color: f.stateOf(s.item) == SetupState.granted ? J.green : J.lineBright),
              ),
              const SizedBox(width: J.s4),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(s.title, style: J.body),
                  Text(s.need.label, style: J.mono(10.5, color: J.text3)),
                ]),
              ),
              if (f.stateOf(s.item) == SetupState.granted)
                Text('OK', style: J.mono(11, color: J.green))
              else
                LightAction('Einrichten', key: Key('setup-open-${s.item.name}'), onTap: () => _go(f.open(s.item))),
            ]),
          ),
          const Hairline(),
        ],
      ],
    );
  }

  Widget _bar(SetupFlow f) {
    final step = f.step;
    final granted = step != null && f.stateOf(step.item) == SetupState.granted;
    return Padding(
      padding: const EdgeInsets.fromLTRB(J.s5, J.s3, J.s5, J.s2),
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        if (step == null)
          _Primary('Fertig', key: const Key('setup-finish'), busy: _finishing, onTap: _finish)
        else if (granted)
          _Primary('Weiter', key: const Key('setup-next'), onTap: () => _go(f.next()))
        else
          _Primary(step.action, key: const Key('setup-grant'), busy: _busy, onTap: _grant),
        SizedBox(
          height: J.hit,
          child: Row(children: [
            if (f.canGoBack) LightAction('Zurück', key: const Key('setup-back'), color: J.text2, onTap: () => _go(f.back())),
            const Spacer(),
            // skipping stays possible while Android's dialog is still busy – never a dead end
            if (step != null && !granted) LightAction('Überspringen', key: const Key('setup-skip'), color: J.text2, onTap: () => _go(f.skip())),
          ]),
        ),
      ]),
    );
  }
}

/// The one prominent action per step (min. 48 dp high).
class _Primary extends StatelessWidget {
  const _Primary(this.text, {super.key, required this.onTap, this.busy = false});
  final String text;
  final VoidCallback onTap;
  final bool busy;

  @override
  Widget build(BuildContext context) => Pressable(
        onTap: busy ? null : onTap,
        semantics: text,
        child: Container(
          width: double.infinity,
          alignment: Alignment.center,
          constraints: const BoxConstraints(minHeight: J.hit),
          padding: const EdgeInsets.symmetric(horizontal: J.s5),
          decoration: BoxDecoration(
            color: J.cyan.withValues(alpha: 0.12),
            border: Border.all(color: J.cyan.withValues(alpha: 0.7)),
            borderRadius: BorderRadius.circular(J.radius),
          ),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            if (busy) ...[
              const SizedBox.square(dimension: 12, child: CircularProgressIndicator(strokeWidth: 1.4, color: J.cyan)),
              const SizedBox(width: J.s2),
            ],
            // long labels and large system font sizes wrap instead of overflowing
            Flexible(
              child: Text(text.toUpperCase(),
                  textAlign: TextAlign.center, maxLines: 2, style: J.mono(12, color: J.cyan, weight: 560, spacing: 1.6)),
            ),
          ]),
        ),
      );
}
