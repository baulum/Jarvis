import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'core/theme.dart';
import 'state/app_update.dart';
import 'state/providers.dart';
import 'ui/screens/activity_screen.dart';
import 'ui/screens/call_screen.dart';
import 'ui/screens/chat_screen.dart';
import 'ui/screens/home_screen.dart';
import 'ui/screens/job_screen.dart';
import 'ui/screens/memory_screen.dart';
import 'ui/screens/pair_screen.dart';
import 'ui/screens/settings_screen.dart';
import 'ui/screens/social_screen.dart';
import 'ui/screens/uptik_screen.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarColor: Colors.transparent,
    systemNavigationBarColor: J.void_,
    statusBarIconBrightness: Brightness.light,
  ));
  SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge);
  runApp(const ProviderScope(child: JarvisMobile()));
}

/// Maps notification / system deep links (jarvis://…) onto app routes.
String routeForLink(String link) {
  final u = Uri.tryParse(link);
  if (u == null || u.scheme != 'jarvis') return '/';
  final id = u.pathSegments.isNotEmpty ? u.pathSegments.first : null;
  switch (u.host) {
    case 'approval':
      return Uri(path: '/activity', queryParameters: {'approval': ?id, 'decide': ?u.queryParameters['decide']}).toString();
    case 'task':
      return Uri(path: '/activity', queryParameters: {'task': ?id}).toString();
    case 'job': // coding job: its own detail view (status, result / error, steps, PR / remove)
      return id == null ? '/activity' : '/job/${Uri.encodeComponent(id)}';
    case 'call':
      return id == null ? '/call' : Uri(path: '/activity', queryParameters: {'call': id}).toString();
    case 'chat':
      return Uri(path: '/chat', queryParameters: {'session': ?id}).toString();
    case 'activity':
      return '/activity';
    case 'memory':
      return '/memory';
    case 'social': // new daily video, failed production
      return '/social';
    case 'uptik': // published, failed, account stopped by the circuit breaker
      return '/uptik';
    case 'settings': // e.g. the app-update notification
      return '/settings';
    case 'pair': // jarvis://pair?p=<base64url pairing payload> – confirmed on screen before anything happens
      return Uri(path: '/pair', queryParameters: {'p': ?u.queryParameters['p']}).toString();
    default:
      return '/';
  }
}

class JarvisMobile extends ConsumerStatefulWidget {
  const JarvisMobile({super.key});
  @override
  ConsumerState<JarvisMobile> createState() => _JarvisMobileState();
}

class _JarvisMobileState extends ConsumerState<JarvisMobile> with WidgetsBindingObserver {
  late final GoRouter _router;
  StreamSubscription<dynamic>? _native;
  final _refresh = ValueNotifier<int>(0);
  String? _pendingLink;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _router = GoRouter(
      initialLocation: '/',
      refreshListenable: _refresh,
      redirect: (context, state) {
        final p = ref.read(pairingProvider);
        if (p.isLoading && !p.hasValue) return state.matchedLocation == '/boot' ? null : '/boot';
        final paired = p.valueOrNull?.paired ?? false;
        final loc = state.matchedLocation;
        if (!paired && loc != '/pair') return '/pair';
        if (paired && (loc == '/pair' || loc == '/boot')) return '/';
        return null;
      },
      routes: [
        GoRoute(path: '/boot', builder: (_, _) => const Scaffold(backgroundColor: J.void_)),
        GoRoute(path: '/pair', builder: (_, s) => PairScreen(linkPayload: s.uri.queryParameters['p'])),
        GoRoute(path: '/', builder: (_, _) => const HomeScreen()),
        GoRoute(
          path: '/chat',
          pageBuilder: (_, s) => _fadeUp(ChatScreen(sessionId: s.uri.queryParameters['session'], initial: s.extra as String?)),
        ),
        GoRoute(
          path: '/activity',
          pageBuilder: (_, s) => _slide(ActivityScreen(focus: s.uri.queryParameters), const Offset(1, 0)),
        ),
        GoRoute(
          path: '/job/:id',
          pageBuilder: (_, s) => _slide(JobScreen(jobId: s.pathParameters['id']!), const Offset(1, 0)),
        ),
        GoRoute(path: '/memory', pageBuilder: (_, _) => _slide(const MemoryScreen(), const Offset(-1, 0))),
        GoRoute(path: '/settings', pageBuilder: (_, _) => _slide(const SettingsScreen(), const Offset(1, 0))),
        GoRoute(path: '/social', pageBuilder: (_, _) => _slide(const SocialScreen(), const Offset(1, 0))),
        GoRoute(path: '/uptik', pageBuilder: (_, _) => _slide(const UptikScreen(), const Offset(1, 0))),
        GoRoute(path: '/call', pageBuilder: (_, _) => _fadeUp(const CallScreen())),
      ],
    );
    ref.listenManual(pairingProvider, (_, _) => _refresh.value++);
    final native = ref.read(nativeProvider);
    _native = native.events.listen((e) {
      if (e.type == 'link') _openLink(e.data as String);
      if (e.type == 'revoked') ref.read(pairingProvider.notifier).refresh();
      if (e.type == 'call_state') {
        final s = (e.data as Map)['status'];
        final here = _router.routerDelegate.currentConfiguration.uri.path;
        if (s == 'ringing' && here != '/call') _router.push('/call'); // app open while Jarvis rings
      }
    });
    ref.read(callProvider); // start listening to native call state right away
    // Cold start by an incoming call (push woke the app): the ringing event may predate this listener.
    ref.listenManual(pairingProvider, (_, next) async {
      final pending = _pendingLink;
      if (pending != null && next.hasValue) {
        _pendingLink = null;
        _openLink(pending); // the notification that cold-started the app: open its target now
      }
      if (next.valueOrNull?.paired != true) return;
      ref.read(appUpdateProvider.notifier).check(); // throttled natively; downloads/installs on its own
      final s = await native.callState();
      final live = !['idle', 'ended', null].contains(s['status']);
      if (live && _router.routerDelegate.currentConfiguration.uri.path != '/call') _router.push('/call');
    }, fireImmediately: true);
  }

  /// Opens what a notification / deep link points to. At a cold start the pairing state is not loaded yet and the
  /// router would redirect to the start page, so the link waits until the app knows whether it is paired.
  void _openLink(String link) {
    final p = ref.read(pairingProvider);
    if (p.isLoading && !p.hasValue) {
      _pendingLink = link;
      return;
    }
    final route = routeForLink(link);
    if (route == '/' || route.startsWith('/pair')) {
      _router.go(route);
      return;
    }
    // detail views sit on top of the start page, so "back" returns there
    _router.go('/');
    WidgetsBinding.instance.addPostFrameCallback((_) => _router.push(route));
  }

  CustomTransitionPage<void> _fadeUp(Widget child) => CustomTransitionPage(
        child: child,
        transitionDuration: J.slow,
        reverseTransitionDuration: J.base,
        transitionsBuilder: (_, a, _, c) => FadeTransition(
          opacity: CurvedAnimation(parent: a, curve: J.enter),
          child: SlideTransition(
            position: Tween(begin: const Offset(0, 0.04), end: Offset.zero).animate(CurvedAnimation(parent: a, curve: J.enter)),
            child: c,
          ),
        ),
      );

  CustomTransitionPage<void> _slide(Widget child, Offset from) => CustomTransitionPage(
        child: child,
        transitionDuration: J.slow,
        reverseTransitionDuration: J.base,
        transitionsBuilder: (_, a, _, c) => SlideTransition(
          position: Tween(begin: from * 0.18, end: Offset.zero).animate(CurvedAnimation(parent: a, curve: J.enter)),
          child: FadeTransition(opacity: CurvedAnimation(parent: a, curve: J.enter), child: c),
        ),
      );

  @override
  void didChangeAppLifecycleState(AppLifecycleState s) {
    if (s == AppLifecycleState.resumed) {
      ref.read(liveSocketProvider)?.nudge();
      ref.invalidate(availabilityProvider);
      // only sends anything if Sir switched location sharing on (Einstellungen › Standortfreigabe)
      if (ref.read(isPairedProvider)) ref.read(nativeProvider).reportLocation().catchError((Object _) => false);
      if (ref.read(isPairedProvider)) ref.read(appUpdateProvider.notifier).check();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _native?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: 'J.A.R.V.I.S.',
      debugShowCheckedModeBanner: false,
      theme: J.theme(),
      routerConfig: _router,
    );
  }
}
