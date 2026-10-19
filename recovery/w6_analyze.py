"""Analysis of the recovery and deadlock runs: tables and the data of one small figure.
  results/rec.jsonl  ->  recovery tables (staged and race cells, replay against validate)
  results/dl.jsonl   ->  deadlock tables per skew; figure: throughput and p99 latency against clients
Writes results/w6_summary.md, results/w6_fig_dl.png and results/w6_fig_dl_pgf.txt (pgfplots
coordinates).  usage: python -I w6_analyze.py [RESULTS_DIR]
"""
import json, math, os, sys
from collections import defaultdict

T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571}
METHODS = ['Lnw', 'Lord', 'Lb100', 'Lb2000', 'h', 'hd']
LABEL = {'Lnw': 'leveled, NOWAIT', 'Lb100': 'leveled, lock_timeout 100 ms', 'Lb2000': 'leveled, lock_timeout 2 s', 'Lord': 'leveled, ordered locks',
         'h': '2PC via coordinator', 'hd': '2PC via worker'}
# validated categorical slots 1-6 (dataviz reference palette, light mode), fixed order
COLOR = {'Lnw': '#2a78d6', 'Lb2000': '#eb6834', 'Lb100': '#1baf7a', 'h': '#eda100', 'hd': '#e87ba4', 'Lord': '#008300'}
MARK = {'Lnw': 'o', 'Lb2000': 's', 'Lb100': '^', 'h': 'D', 'hd': 'v', 'Lord': 'P'}


def load(p):
    return [json.loads(line) for line in open(p)] if os.path.exists(p) else []


def mean_ci(v):
    v = [x for x in v if x is not None]
    if not v:
        return None, None
    m = sum(v) / len(v)
    if len(v) < 2:
        return m, None
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))
    return m, T975[len(v) - 1] * sd / math.sqrt(len(v))


def gmean(v):
    v = [x for x in v if x]
    return math.exp(sum(math.log(x) for x in v) / len(v)) if v else None


def f(x, d=0):
    if x is None:
        return '–'
    return f'{x:,.{d}f}'


# ---------------------------------------------------------------- recovery
def recovery(rows, out):
    for mode in ('staged', 'race'):
        rs = [r for r in rows if r['mode'] == mode]
        if not rs:
            continue
        cells = defaultdict(list)
        for r in rs:
            cells[(r['kind'], r['crash'], r['relay'])].append(r)
        blocks = sorted({r['block'] for r in rs})
        out.append(f'\n### Recovery, {mode} ({len(blocks)} blocks, {rs[0]["trials"]} trials per cell and block)\n')
        out.append('| conflict | crash after | relay | trials | completed | aborted | orphans (final) | lost updates | '
                   'orphan reads / reads | relay p50 (ms) | conflict applied / rejected |')
        out.append('|---|---|---|---|---|---|---|---|---|---|---|')
        tot = defaultdict(lambda: defaultdict(int))
        for key in sorted(cells):
            kind, crash, relay = key
            cs = cells[key]
            n = sum(r['trials'] for r in cs)
            ro = defaultdict(int)
            ko = defaultdict(int)
            for r in cs:
                for k, v in r['relay_outcome'].items():
                    ro[k] += v
                for k, v in r['conflict'].items():
                    ko[k] += v
            orph = sum(r['orphans'] for r in cs)
            lost = sum(r['lost'] for r in cs)
            reads = sum(r['reads'] for r in cs)
            oreads = sum(r['orphan_reads'] for r in cs)
            p50 = sum(r['relay_p50_ms'] for r in cs) / len(cs)
            out.append(f'| {kind} | {crash} | {relay} | {n} | {ro.get("completed", 0)} | {ro.get("aborted", 0)} | '
                       f'{orph} | {lost} | {oreads} / {reads} | {p50:.2f} | '
                       f'{ko.get("applied", 0)} / {ko.get("rejected", 0)} |')
            grp = 'K0' if kind == 'K0' else 'K1-K3'
            t = tot[(relay, grp)]
            for k, v in (('trials', n), ('completed', ro.get('completed', 0)), ('aborted', ro.get('aborted', 0)),
                         ('orphans', orph), ('lost', lost), ('reads', reads), ('oreads', oreads),
                         ('orphan_trials', sum(r['orphan_trials'] for r in cs))):
                t[k] += v
            t['p50s'] = t.get('p50s', 0) + p50 * len(cs)
            t['ncells'] = t.get('ncells', 0) + len(cs)
        out.append('\nTotals:\n')
        out.append('| relay | conflicts | trials | completed | aborted | trials with orphans | orphans | lost updates | '
                   'orphan reads / reads | relay p50 (ms, mean of cells) |')
        out.append('|---|---|---|---|---|---|---|---|---|---|')
        for (relay, grp), t in sorted(tot.items()):
            out.append(f'| {relay} | {grp} | {t["trials"]} | {t["completed"]} | {t["aborted"]} | {t["orphan_trials"]} | '
                       f'{t["orphans"]} | {t["lost"]} | {t["oreads"]} / {t["reads"]} | {t["p50s"] / t["ncells"]:.2f} |')


# ---------------------------------------------------------------- deadlocks
def deadlocks(rows, out, resdir):
    if not rows:
        return
    skews = sorted({(r['G'], r['theta']) for r in rows}, key=lambda s: -s[1])
    Cs = sorted({r['C'] for r in rows})
    agg = {}
    for G, th in skews:
        out.append(f'\n### Deadlocks, Zipf {th} over {G:,} groups '
                   f'({len({r["block"] for r in rows})} blocks, mean [95% t half-width])\n')
        out.append('| clients | method | updates/s | p50 (ms) | p99 (ms) | max (ms) | failed attempts | lock timeouts | '
                   'deadlocks broken by the engine | NOWAIT failures | hidden cross-worker cycles | cycle p50 / max (ms) | '
                   'samples with a lock wait |')
        out.append('|---|---|---|---|---|---|---|---|---|---|---|---|---|')
        for C in Cs:
            for m in METHODS:
                rs = [r for r in rows if r['G'] == G and r['theta'] == th and r['C'] == C and r['method'] == m]
                if not rs:
                    continue
                tp, tci = mean_ci([r['tput'] for r in rs])
                p50, _ = mean_ci([r['p50_ms'] for r in rs])
                p99, p99ci = mean_ci([r['p99_ms'] for r in rs])
                mx = max((r['max_ms'] or 0) for r in rs)
                att = sum(r['attempts'] for r in rs)
                fa = sum(r['failed_attempts'] for r in rs)
                fl = defaultdict(int)
                for r in rs:
                    for k, v in r['fails'].items():
                        if k != 'other_msgs':
                            fl[k] += v
                mon = [r['monitor'] for r in rs if r['monitor']]
                cyc = sum(x['cross_worker_cycles'] for x in mon) if mon else None
                cp50 = [x['cross_cycle_ms_p50'] for x in mon if x['cross_cycle_ms_p50']]
                cmax = [x['cross_cycle_ms_max'] for x in mon if x['cross_cycle_ms_max']]
                ws = (sum(x['samples_with_wait'] for x in mon), sum(x['samples'] for x in mon)) if mon else None
                agg[(G, th, C, m)] = {'tput': tp, 'tci': tci, 'p99': p99, 'p99ci': p99ci, 'p50': p50, 'max': mx,
                                      'fail_rate': fa / att if att else None, 'fails': dict(fl), 'cycles': cyc,
                                      'n': len(rs), 'integrity': all(r['integrity_ok'] for r in rs)}
                out.append(f'| {C} | {m} | {f(tp)} [{f(tci)}] | {f(p50, 2)} | {f(p99, 1)} | {f(mx, 0)} | '
                           f'{fa / att * 100 if att else 0:.1f}% | {fl.get("lock_timeout", 0)} | '
                           f'{fl.get("deadlock_local", 0) + fl.get("deadlock_dist", 0)} | {fl.get("nowait", 0)} | '
                           f'{"–" if cyc is None else cyc} | '
                           f'{f(sum(cp50) / len(cp50)) if cp50 else "–"} / {f(max(cmax)) if cmax else "–"} | '
                           f'{"–" if ws is None else f"{ws[0]}/{ws[1]}"} |')
        bad = [k for k, v in agg.items() if not v['integrity']]
        if bad:
            out.append(f'\n**Integrity check failed for {bad}.**')
    figure(agg, skews, Cs, resdir, out)


def figure(agg, skews, Cs, resdir, out):
    # pgfplots coordinates (hot skew first)
    lines = []
    for G, th in skews:
        for what in ('tput', 'p99'):
            lines.append(f'% Zipf {th} over {G} groups, {what}')
            for m in METHODS:
                pts = ' '.join(f'({C},{agg[(G, th, C, m)][what]:.4g})' for C in Cs if (G, th, C, m) in agg
                               and agg[(G, th, C, m)][what])
                lines.append(f'\\addplot coordinates {{{pts}}}; % {m}: {LABEL[m]}')
    open(os.path.join(resdir, 'w6_fig_dl_pgf.txt'), 'w').write('\n'.join(lines) + '\n')
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        return
    fig, axs = plt.subplots(len(skews), 2, figsize=(7.0, 2.5 * len(skews)), squeeze=False)
    for i, (G, th) in enumerate(skews):
        for j, (what, ylab) in enumerate((('tput', 'updates per second'), ('p99', 'p99 latency (ms)'))):
            ax = axs[i][j]
            for m in METHODS:
                xs = [C for C in Cs if (G, th, C, m) in agg and agg[(G, th, C, m)][what]]
                ys = [agg[(G, th, C, m)][what] for C in xs]
                if xs:
                    ax.plot(xs, ys, color=COLOR[m], marker=MARK[m], markersize=5, linewidth=1.6, label=LABEL[m])
            ax.set_xscale('log', base=2)
            ax.set_yscale('log')
            ax.set_xticks(Cs)
            ax.set_xticklabels([str(C) for C in Cs])
            ax.set_xlabel('clients', color='#52514e')
            ax.set_ylabel(ylab, color='#52514e')
            ax.set_title(f'Zipf {th}, {G:,} groups', fontsize=9, color='#0b0b0b')
            ax.grid(True, which='major', color='#e4e3df', linewidth=0.6)
            for s in ('top', 'right'):
                ax.spines[s].set_visible(False)
            ax.tick_params(colors='#52514e', labelsize=8)
    h, lab = axs[0][0].get_legend_handles_labels()
    fig.legend(h, lab, loc='upper center', ncol=3, fontsize=8, frameon=False)
    fig.tight_layout(rect=(0, 0, 1, 0.9 if len(skews) == 1 else 0.93))
    p = os.path.join(resdir, 'w6_fig_dl.png')
    fig.savefig(p, dpi=170, facecolor='#fcfcfb')
    out.append(f'\nFigure: {p}')


def main():
    resdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')
    out = ['# Recovery and deadlock runs (generated by w6_analyze.py)']
    recovery(load(os.path.join(resdir, 'rec.jsonl')), out)
    deadlocks(load(os.path.join(resdir, 'dl.jsonl')), out, resdir)
    open(os.path.join(resdir, 'w6_summary.md'), 'w', encoding='utf-8').write('\n'.join(out) + '\n')
    print('\n'.join(out))


if __name__ == '__main__':
    main()
