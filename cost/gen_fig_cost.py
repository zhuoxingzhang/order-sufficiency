"""Generate the LaTeX of Figure 5 from the cost runs (numbers by fig_cost.py): every
value is the median latency or the throughput of a way of writing relative to the fastest one
two-phase commit of the same block and cell.
Panels: (a) Citus latency, one client; (b) Vitess latency, one client; (c) Citus latency, one client,
1 ms added per round trip; (d) Citus throughput, 64 clients; (e) Vitess throughput, 64 clients.
usage: python -I gen_fig_cost.py RESULTS_DIR OUT.tex"""
import io, os, sys

R, OUT = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fig_cost as F  # noqa: E402

ORDER = ('b', 'L', 'Lo', 'E')
COL = {'b': 'cMany', 'L': 'cOrder', 'Lo': 'cDeep', 'E': 'cSet'}
MARK = {'b': '*', 'L': '*', 'Lo': 'square*', 'E': 'triangle*'}
NAME = {'b': 'one commit per row', 'L': 'leveled commits', 'Lo': 'leveled commits with an outbox',
        'E': 'leveled commits with an eager first round'}
PANELS = [('citus', 'final_citus.jsonl', 1, 'p50_ms', r'(a) Citus latency\\one client'),
          ('vitess', 'final_vitess.jsonl', 1, 'p50_ms', r'(b) Vitess latency\\one client'),
          ('citus', 'final_rtt_citus.jsonl', 1, 'p50_ms', r'(c) Citus latency\\1\,ms delay'),
          ('citus', 'final64_citus.jsonl', 64, 'tput', r'(d) Citus throughput\\64 clients'),
          ('vitess', 'final64_vitess.jsonl', 64, 'tput', r'(e) Vitess throughput\\64 clients')]
YLAT = r'ytick={0.125,0.25,0.5,1,2,4,8,16}, yticklabels={,0.25,,1,,4,,16}, ymin=0.12, ymax=18'
YTPUT = r'ytick={0.0625,0.125,0.25,0.5,1,2,4}, yticklabels={,,0.25,,1,,4}, ymin=0.06, ymax=4.3'

desc, lines = [], []
lines += [r'\begin{figure*}[t]', r'\centering',
          r'\pgfplotsset{rowaxis/.style={axis lines*=left, ymajorgrids, grid style={black!12},',
          r'  tick label style={font=\scriptsize}, label style={font=\scriptsize},',
          r'  xtick={2,8,32,128}, xticklabels={2--3,8,32,128}, xmin=1.7, xmax=150,',
          r'  extra y ticks={1}, extra y tick labels={}, extra y tick style={grid=major, major grid style={black, dashed}},',
          r'  xlabel={Rows}, every axis plot/.append style={line width=0.9pt, mark size=1.3pt}}}',
          r'\begin{tikzpicture}',
          r'\begin{groupplot}[group style={group size=5 by 1, horizontal sep=0.55cm},',
          r'  width=0.235\textwidth, height=3.9cm, title style={font=\scriptsize\bfseries, align=center, yshift=-0.5ex}]']
for i, (eng, name, C, metric, title) in enumerate(PANELS):
    rows = [r for r in F.load(R, name) if r['C'] == C]
    opt = [r'rowaxis, xmode=log, ymode=log, log basis x=2, log basis y=2', 'title={' + title + '}', YTPUT if metric == 'tput' else YLAT]
    if i == 0:
        opt.append(r'ylabel={Relative to the fastest 2PC}')
        opt.append('\n  legend to name=costlegend, legend columns=4, legend style={font=\\scriptsize, draw=none, column sep=6pt}')
    lines.append(r'\nextgroupplot[' + ', '.join(opt) + ']')
    d = []
    for m in ORDER:
        pts = F.series(rows, eng, m, metric)
        lines.append(rf'\addplot[{COL[m]}, mark={MARK[m]}] coordinates {{' +
                     ' '.join(f'({s},{g:.2f})' for s, g, _, _ in pts) + '};')
        d.append(f'{NAME[m]} ' + ', '.join(f'{g:.2f}' for _, g, _, _ in pts))
    if i == 0:
        lines.append(r'\legend{' + ', '.join(NAME[m] for m in ORDER) + '}')
    desc.append(title.replace('\\\\', ', ').replace('\\,', ' ') + ': ' + '; '.join(d))
lines += [r'\end{groupplot}', r'\end{tikzpicture}', '', r'\ref{costlegend}',
          r'\caption{Cost of Leveled Commits Relative to the Fastest Two-Phase Commit}\label{fig:cost}',
          r'\Description{Five line charts with logarithmic axes. Each value is the median latency, or in the last two charts the throughput, '
          r'of a way of writing relative to the fastest one two-phase commit, geometric mean over the insert, delete and reparent templates and three blocks, at 2 or 3, 8, 32 and 128 rows. '
          + '. '.join(desc) + '.}',
          r'\end{figure*}']
io.open(OUT, 'w', encoding='utf-8', newline='\n').write('\n'.join(lines) + '\n')
print('\n'.join(desc))
