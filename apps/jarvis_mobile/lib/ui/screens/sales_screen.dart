import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/api.dart';
import '../../core/theme.dart';
import '../../state/providers.dart';
import '../../state/sales.dart';
import '../widgets/hud.dart';

/// Kundensuche: the PJE-Sales-AI Lead-Tool on the phone – overview, leads with status/campaign filters, campaigns,
/// approval queue with the draft, outbox. Same /api/sales endpoints as the Mac app. „Neu formulieren“ ends in the
/// Core's approvals; approving and sending happen only in the Lead-Tool itself.
class SalesScreen extends ConsumerStatefulWidget {
  const SalesScreen({super.key});
  @override
  ConsumerState<SalesScreen> createState() => _SalesScreenState();
}

enum _Tab { overview, leads, campaigns, review, outbox }

class _SalesScreenState extends ConsumerState<SalesScreen> {
  _Tab _tab = _Tab.overview;
  String _campaign = '';
  String _status = '';

  void _toast(String msg) {
    ScaffoldMessenger.maybeOf(context)?.showSnackBar(SnackBar(content: Text(msg), backgroundColor: J.panel));
  }

  void _refresh() {
    ref.invalidate(salesStatusProvider);
    ref.invalidate(salesAnalyticsProvider);
    ref.invalidate(salesCampaignsProvider);
    ref.invalidate(salesReviewProvider);
    ref.invalidate(salesOutboxProvider);
    ref.invalidate(salesLeadsProvider);
    ref.invalidate(salesLeadProvider);
  }

  void _showLeads(String campaign, [String status = '']) => setState(() {
        _campaign = campaign;
        _status = status;
        _tab = _Tab.leads;
      });

  void _openLead(Map<String, dynamic> status, String id) => showHudSheet<void>(
        context,
        builder: (_) => _LeadSheet(id: id, status: status, toast: _toast),
      );

  @override
  Widget build(BuildContext context) {
    final status = ref.watch(salesStatusProvider);
    final ov = status.valueOrNull?['overview'] as Map?;
    return Scaffold(
      backgroundColor: J.void_,
      body: SafeArea(
        child: Column(children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(J.s2, J.s2, J.s4, J.s2),
            child: Row(children: [
              Pressable(
                onTap: () => context.canPop() ? context.pop() : context.go('/'),
                semantics: 'Zurück',
                child: const SizedBox.square(dimension: J.hit, child: Center(child: Glyph(GlyphKind.back))),
              ),
              Expanded(child: Text('Kundensuche', style: J.title)),
            ]),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: J.s4),
            child: Rail<_Tab>(
              values: _Tab.values,
              labels: [
                'Stand',
                'Leads',
                'Kampagnen',
                ov == null ? 'Freigabe' : 'Freigabe ${ov['review']}',
                'Ausgang',
              ],
              value: _tab,
              colors: {if ((ov?['review'] ?? 0) as int > 0) _Tab.review: J.amber},
              onChanged: (t) => setState(() => _tab = t),
            ),
          ),
          const SizedBox(height: J.s3),
          Expanded(
            child: RefreshIndicator(
              color: J.cyan,
              backgroundColor: J.panel,
              onRefresh: () async => _refresh(),
              child: status.when(
                loading: _loading,
                error: (e, _) => _error(e),
                data: (s) => s['connected'] != true
                    ? _NotConnected(status: s)
                    : switch (_tab) {
                        _Tab.overview => _Overview(onShowLeads: _showLeads),
                        _Tab.leads => _Leads(
                            campaign: _campaign,
                            status: _status,
                            onFilter: (c, st) => setState(() {
                              _campaign = c;
                              _status = st;
                            }),
                            onOpen: (id) => _openLead(s, id),
                          ),
                        _Tab.campaigns => _Campaigns(onShowLeads: _showLeads),
                        _Tab.review => _Review(onOpen: (id) => _openLead(s, id)),
                        _Tab.outbox => _Outbox(status: s),
                      },
              ),
            ),
          ),
        ]),
      ),
    );
  }
}

Widget _loading() => const Center(child: SizedBox.square(dimension: 20, child: CircularProgressIndicator(strokeWidth: 1.5, color: J.text3)));

Widget _error(Object e) => ListView(padding: const EdgeInsets.all(J.s5), children: [
      Text(e is ApiError ? (e.offline ? 'Jarvis ist nicht erreichbar.' : e.message) : '$e', style: J.bodyDim),
    ]);

Widget _section(String title, List<Widget> children, {Color? color}) => Padding(
      padding: const EdgeInsets.only(bottom: J.s5),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Caps(title, color: color),
        const SizedBox(height: J.s2),
        ...children,
      ]),
    );

Widget _kv(String k, String v, {Color color = J.text}) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SizedBox(width: 110, child: Text(k, style: J.bodyDim.copyWith(fontSize: 14))),
        Expanded(child: Text(v, style: J.body.copyWith(fontSize: 14, color: color))),
      ]),
    );

Color _tone(String tone) => switch (tone) { 'ok' => J.green, 'bad' => J.red, 'warn' => J.amber, 'info' => J.cyan, _ => J.text3 };

String _when(Object? iso) {
  final d = DateTime.tryParse('${iso ?? ''}');
  return d == null ? '–' : relDay(d);
}

Widget _row({required String title, String? sub, required String trailing, Color trailingColor = J.text2, VoidCallback? onTap}) =>
    Pressable(
      onTap: onTap,
      semantics: title,
      child: Container(
        constraints: const BoxConstraints(minHeight: J.hit),
        padding: const EdgeInsets.symmetric(vertical: J.s2),
        decoration: const BoxDecoration(border: Border(bottom: BorderSide(color: J.line))),
        child: Row(children: [
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, style: J.body, maxLines: 1, overflow: TextOverflow.ellipsis),
              if (sub != null && sub.isNotEmpty) Text(sub, style: J.bodyDim.copyWith(fontSize: 13), maxLines: 1, overflow: TextOverflow.ellipsis),
            ]),
          ),
          const SizedBox(width: J.s3),
          Text(trailing, style: J.mono(12, color: trailingColor)),
        ]),
      ),
    );

class _NotConnected extends StatelessWidget {
  const _NotConnected({required this.status});
  final Map<String, dynamic> status;
  @override
  Widget build(BuildContext context) => ListView(padding: const EdgeInsets.all(J.s4), children: [
        Text(status['configured'] == true ? 'Das Lead-Tool ist gerade nicht erreichbar.' : 'Das Lead-Tool ist nicht eingerichtet.', style: J.title),
        const SizedBox(height: J.s3),
        Text('${status['error'] ?? ''}', style: J.bodyDim),
        const SizedBox(height: J.s3),
        Text('Jarvis liest Leads und Kampagnen über das Lead-Tool auf dem Mac. Läuft es wieder, zum Aktualisieren nach unten ziehen.',
            style: J.bodyDim),
      ]);
}

// ------------------------------------------------------------------ overview
class _Overview extends ConsumerWidget {
  const _Overview({required this.onShowLeads});
  final void Function(String campaign, [String status]) onShowLeads;
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return ref.watch(salesAnalyticsProvider).when(
          loading: _loading,
          error: (e, _) => _error(e),
          data: (a) {
            final stages = ((a['stages'] as List?) ?? const []).cast<Map>();
            final per = ((a['per_campaign'] as List?) ?? const []).cast<Map>();
            final tasks = (a['tasks'] as Map?) ?? const {};
            final off = (a['off_track'] as Map?) ?? const {};
            if (a['lead_total'] == 0) {
              return ListView(padding: const EdgeInsets.all(J.s4), children: [
                Text('Noch keine Leads. Sie entstehen aus Kampagnen, die du am Mac oder im Lead-Tool startest.', style: J.bodyDim),
              ]);
            }
            final max = stages.fold<int>(1, (m, s) => (s['count'] as int) > m ? s['count'] as int : m);
            return ListView(padding: const EdgeInsets.symmetric(horizontal: J.s4), children: [
              _section('Offen', [
                _kv('Leads', '${a['lead_total']}'),
                _kv('Zur Freigabe', '${tasks['review'] ?? 0}', color: (tasks['review'] ?? 0) as int > 0 ? J.amber : J.text),
                _kv('Postausgang', '${tasks['outbox_preview'] ?? 0}'),
                _kv('Antworten', '${tasks['inbox'] ?? 0}'),
              ]),
              _section('Stand je Station', [
                for (final s in stages)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 5),
                    child: Row(children: [
                      SizedBox(width: 130, child: Text('${s['label']}', style: J.bodyDim.copyWith(fontSize: 14))),
                      Expanded(
                        child: ClipRRect(
                          borderRadius: BorderRadius.circular(2),
                          child: LinearProgressIndicator(
                            value: (s['count'] as int) / max,
                            minHeight: 4,
                            backgroundColor: J.panel,
                            color: s['key'] == 'review' ? J.amber : (s['key'] == 'contact' ? J.green : J.cyanDeep),
                          ),
                        ),
                      ),
                      SizedBox(width: 44, child: Text('${s['count']}', textAlign: TextAlign.right, style: J.mono(12, color: J.text))),
                    ]),
                  ),
                const SizedBox(height: J.s2),
                Text('Aussortiert ${off['out'] ?? 0} · mit Fehler ${off['error'] ?? 0}', style: J.mono(11.5)),
              ]),
              _section('Kampagnen', [
                for (final c in per)
                  _row(
                    title: '${c['name'] ?? 'Ohne Namen'}',
                    sub: '${c['status_label']} · ${c['leads']} Leads',
                    trailing: (c['in_review'] as int) > 0 ? '${c['in_review']} Freigabe' : '',
                    trailingColor: J.amber,
                    onTap: () => onShowLeads('${c['id']}'),
                  ),
                const SizedBox(height: J.s2),
                Text('${a['basis']}', style: J.mono(11)),
              ]),
              const SizedBox(height: J.s6),
            ]);
          },
        );
  }
}

// ------------------------------------------------------------------ leads
class _Leads extends ConsumerWidget {
  const _Leads({required this.campaign, required this.status, required this.onFilter, required this.onOpen});
  final String campaign;
  final String status;
  final void Function(String campaign, String status) onFilter;
  final ValueChanged<String> onOpen;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final statuses = ref.watch(salesStatusesProvider).valueOrNull ?? const [];
    final campaigns = ref.watch(salesCampaignsProvider).valueOrNull ?? const [];
    final page = ref.watch(salesLeadsProvider((campaign, status)));
    Widget dropdown(String hint, String value, List<(String, String)> items, ValueChanged<String> onChanged) => DropdownButton<String>(
          value: value,
          isExpanded: true,
          dropdownColor: J.panel,
          style: J.body.copyWith(fontSize: 14),
          underline: Container(height: 1, color: J.line),
          items: [
            DropdownMenuItem(value: '', child: Text(hint, overflow: TextOverflow.ellipsis)),
            for (final (id, label) in items) DropdownMenuItem(value: id, child: Text(label, overflow: TextOverflow.ellipsis)),
          ],
          onChanged: (v) => onChanged(v ?? ''),
        );
    final statusItems = [for (final s in statuses) ('${s['id']}', '${s['label']}')];
    final campaignItems = [for (final c in campaigns) ('${c['id']}', '${c['name'] ?? c['id']}')];
    return ListView(padding: const EdgeInsets.symmetric(horizontal: J.s4), children: [
      Row(children: [
        Expanded(
          child: dropdown('Alle Status', statusItems.any((s) => s.$1 == status) ? status : '', statusItems, (v) => onFilter(campaign, v)),
        ),
        const SizedBox(width: J.s3),
        Expanded(
          child: dropdown('Alle Kampagnen', campaignItems.any((c) => c.$1 == campaign) ? campaign : '', campaignItems, (v) => onFilter(v, status)),
        ),
      ]),
      const SizedBox(height: J.s3),
      ...page.when(
        loading: () => [const SizedBox(height: J.s6), _loading()],
        error: (e, _) => [_error(e)],
        data: (p) {
          final leads = ((p['leads'] as List?) ?? const []).cast<Map>();
          return [
            Text('${p['total']} Leads${campaign.isNotEmpty || status.isNotEmpty ? ' für diesen Filter' : ''}', style: J.mono(11.5)),
            const SizedBox(height: J.s2),
            if (leads.isEmpty) Text('Keine Leads für diesen Filter.', style: J.bodyDim),
            for (final l in leads)
              _row(
                title: '${l['name'] ?? 'Ohne Namen'}',
                sub: '${l['status_label']} · ${l['campaign_name'] ?? ''}',
                trailing: salesScore(l['score'], l['priority']),
                trailingColor: _tone(salesStageTone(l['stage'])),
                onTap: () => onOpen('${l['id']}'),
              ),
            if ((p['more'] as int? ?? 0) > 0)
              Padding(
                padding: const EdgeInsets.only(top: J.s3),
                child: Text('${p['more']} weitere – Filter eingrenzen oder am Mac ansehen.', style: J.mono(11.5)),
              ),
            const SizedBox(height: J.s6),
          ];
        },
      ),
    ]);
  }
}

// ------------------------------------------------------------------ campaigns
class _Campaigns extends ConsumerWidget {
  const _Campaigns({required this.onShowLeads});
  final void Function(String campaign, [String status]) onShowLeads;
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return ref.watch(salesCampaignsProvider).when(
          loading: _loading,
          error: (e, _) => _error(e),
          data: (list) => ListView(padding: const EdgeInsets.symmetric(horizontal: J.s4), children: [
            if (list.isEmpty) Text('Noch keine Kampagne. Starten kannst du sie am Mac oder im Lead-Tool.', style: J.bodyDim),
            for (final c in list)
              _row(
                title: '${c['name'] ?? 'Ohne Namen'}',
                sub: [c['industry'], c['region'], '${c['lead_count']} Leads'].whereType<Object>().join(' · '),
                trailing: '${c['status_label']}',
                trailingColor: c['status'] == 'failed' ? J.red : (c['status'] == 'done' ? J.green : J.cyan),
                onTap: () => onShowLeads('${c['id']}'),
              ),
            const SizedBox(height: J.s6),
          ]),
        );
  }
}

// ------------------------------------------------------------------ review queue
class _Review extends ConsumerWidget {
  const _Review({required this.onOpen});
  final ValueChanged<String> onOpen;
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return ref.watch(salesReviewProvider).when(
          loading: _loading,
          error: (e, _) => _error(e),
          data: (list) => ListView(padding: const EdgeInsets.symmetric(horizontal: J.s4), children: [
            if (list.isEmpty) Text('Kein Entwurf wartet auf Freigabe.', style: J.bodyDim),
            for (final r in list)
              _row(
                title: '${r['name'] ?? 'Ohne Namen'}',
                sub: '${r['campaign_name'] ?? ''} · seit ${_when(r['since'])}',
                trailing: salesScore(r['score']),
                trailingColor: J.amber,
                onTap: () => onOpen('${r['id']}'),
              ),
            const SizedBox(height: J.s6),
          ]),
        );
  }
}

// ------------------------------------------------------------------ outbox
class _Outbox extends ConsumerWidget {
  const _Outbox({required this.status});
  final Map<String, dynamic> status;
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final link = salesLeadToolLink(status['web_url'], '/outbox');
    return ref.watch(salesOutboxProvider).when(
          loading: _loading,
          error: (e, _) => _error(e),
          data: (o) => ListView(padding: const EdgeInsets.symmetric(horizontal: J.s4), children: [
            _section('Postausgang', [
              Text('${o['preview_count']} ${o['preview_count'] == 1 ? 'Nachricht wartet' : 'Nachrichten warten'} auf Prüfung und Versand.', style: J.body),
              const SizedBox(height: J.s2),
              Text('${o['reason']}', style: J.bodyDim),
              const SizedBox(height: J.s3),
              if (link != null)
                LightAction('Postausgang im Lead-Tool', onTap: () => ref.read(nativeProvider).openUrl(link))
              else
                Text('Das Lead-Tool läuft auf dem Mac; Versand dort.', style: J.mono(11.5)),
            ]),
          ]),
        );
  }
}

// ------------------------------------------------------------------ lead file (bottom sheet)
class _LeadSheet extends ConsumerWidget {
  const _LeadSheet({required this.id, required this.status, required this.toast});
  final String id;
  final Map<String, dynamic> status;
  final ValueChanged<String> toast;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final lead = ref.watch(salesLeadProvider(id));
    final caps = (status['capabilities'] as Map?) ?? const {};
    return ConstrainedBox(
      constraints: BoxConstraints(maxHeight: MediaQuery.of(context).size.height * 0.85),
      child: lead.when(
        loading: () => const Padding(padding: EdgeInsets.all(J.s6), child: SizedBox(height: 80)),
        error: (e, _) => Padding(padding: const EdgeInsets.all(J.s5), child: _error(e)),
        data: (d) {
          final company = (d['company'] as Map?) ?? const {};
          final draft = d['draft'] as Map?;
          final score = d['score'] as Map?;
          final evidence = ((d['evidence'] as List?) ?? const []).cast<Map>();
          final link = salesLeadToolLink(status['web_url'], '/review?lead=$id');
          return ListView(shrinkWrap: true, padding: const EdgeInsets.fromLTRB(J.s5, J.s4, J.s5, J.s5), children: [
            Text('${company['name'] ?? 'Ohne Namen'}', style: J.title),
            const SizedBox(height: J.s1),
            Text('${d['status_label']} · ${d['campaign_name'] ?? ''}', style: J.mono(12, color: _tone(salesStageTone(d['stage'])))),
            if (d['last_error'] != null) ...[const SizedBox(height: J.s2), Text('${d['last_error']}', style: J.mono(12, color: J.red))],
            const SizedBox(height: J.s4),
            _kv('Branche', '${company['industry'] ?? '–'}'),
            _kv('Website', '${company['website'] ?? '–'}'),
            _kv('E-Mail', '${company['email'] ?? '–'}'),
            _kv('Telefon', '${company['phone'] ?? '–'}'),
            _kv('Adresse', (company['address'] as String?)?.isNotEmpty == true ? '${company['address']}' : '–'),
            _kv('Score', score == null ? 'noch nicht bewertet' : salesScore(score['value'], score['priority'])),
            const SizedBox(height: J.s4),
            _section('Entwurf', [
              if (draft == null) Text('Noch kein ausgewählter Entwurf.', style: J.bodyDim),
              if (draft?['subject'] != null) Text('${draft!['subject']}', style: J.body),
              if (draft?['body'] != null) ...[const SizedBox(height: J.s2), SelectableText('${draft!['body']}', style: J.bodyDim)],
              if (d['status'] == 'AWAITING_APPROVAL') ...[
                const SizedBox(height: J.s3),
                Wrap(spacing: J.s4, children: [
                  if (d['can_redraft'] == true && caps['redraft'] == true)
                    LightAction('Neu formulieren', onTap: () async {
                      final note = await showDialog<String>(context: context, builder: (_) => const _RedraftDialog());
                      if (note == null) return;
                      try {
                        final r = await ref.read(apiProvider).post('/api/sales/leads/$id/redraft', {'note': note, 'request_id': salesRequestId()});
                        toast(salesActionText(r, 'Neu formulieren eingereiht'));
                        ref.invalidate(salesLeadProvider(id));
                        ref.invalidate(salesReviewProvider);
                      } on ApiError catch (e) {
                        toast(e.offline ? 'Jarvis ist nicht erreichbar' : e.message);
                      }
                    }),
                  if (link != null) LightAction('Im Lead-Tool freigeben', color: J.amber, onTap: () => ref.read(nativeProvider).openUrl(link)),
                ]),
                const SizedBox(height: J.s2),
                Text('Freigeben und Versenden passieren im Lead-Tool am Mac, mit dessen Prüfungen.', style: J.mono(11)),
              ],
            ]),
            if (evidence.isNotEmpty)
              _section('Belege', [
                for (final e in evidence)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 4),
                    child: Text('${e['claim'] ?? ''}', style: J.bodyDim.copyWith(fontSize: 14)),
                  ),
              ]),
          ]);
        },
      ),
    );
  }
}

class _RedraftDialog extends StatefulWidget {
  const _RedraftDialog();
  @override
  State<_RedraftDialog> createState() => _RedraftDialogState();
}

class _RedraftDialogState extends State<_RedraftDialog> {
  final _note = TextEditingController();

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        backgroundColor: J.panel,
        title: Text('Entwurf neu formulieren', style: J.title),
        content: TextField(
          controller: _note,
          maxLength: 500,
          minLines: 2,
          maxLines: 5,
          style: J.body,
          decoration: InputDecoration(hintText: 'Anweisung, z. B. kürzer und ohne Preisnennung', hintStyle: J.bodyDim),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: Text('Abbrechen', style: J.mono(12))),
          TextButton(
            onPressed: () {
              final t = _note.text.trim();
              if (t.length >= 5) Navigator.pop(context, t);
            },
            child: Text('Neu formulieren', style: J.mono(12, color: J.cyan)),
          ),
        ],
      );
}
