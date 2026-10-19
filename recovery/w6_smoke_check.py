"""Smoke check of the deadlock runs: every method ran, committed updates, and passed the integrity
check; NOWAIT never waited for a lock.  usage: python -I w6_smoke_check.py results/smoke_dl.jsonl"""
import json, sys

rows = [json.loads(line) for line in open(sys.argv[1])]
bad = []
for m in ('Lnw', 'Lb2000', 'Lb100', 'Lord', 'h', 'hd'):
    rs = [r for r in rows if r['method'] == m]
    if not rs:
        bad.append(f'{m}: no run')
        continue
    r = rs[-1]
    if not r['ops'] or not r['integrity_ok']:
        bad.append(f'{m}: ops {r["ops"]}, integrity {r["integrity_ok"]}')
    if m == 'Lnw' and (r['monitor'] or {}).get('samples_with_wait'):
        bad.append(f'Lnw waited for a lock: {r["monitor"]}')
    if m == 'Lord' and (r['monitor'] or {}).get('cycles'):
        bad.append(f'Lord formed a wait-for cycle: {r["monitor"]}')
    if r['fails'].get('other'):
        bad.append(f'{m}: unexpected errors {r["fails"].get("other_msgs")}')
print('\n'.join(bad) if bad else 'W6 deadlock smoke ok')
sys.exit(1 if bad else 0)
