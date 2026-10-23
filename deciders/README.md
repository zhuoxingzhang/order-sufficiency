# Deciders and design (Section 6.4, Appendix A)

| Paper | Produces the results | Numbers and figure from |
|---|---|---|
| Section 6.4, decision time | `run_all.sh`, steps 1-3 | `results/selfcheck_*`, `results/x3_*` |
| Section 6.4, Figure 8, Appendix B and Figure 9, shard keys, unique keys and freezing on GitLab | `run_all.sh`, step 4 | `x4_shardk.py`, `x4_unique.py`, `x4_freq.py`, `results/x4_*` |
| Appendix A.3, Proposition 4.7 | `run_all.sh`, step 6 | `results/fresh_k*` |
| Appendix A.3, Theorem 4.9 | `run_all.sh`, step 9 | `cind_analyze.py` |
| Appendix A.3, Proposition A.8 | `run_all.sh`, step 7 | `uk_tally.py` |
| Appendix A.3, Proposition A.8 with overlapping pointers | `uk_overlap.py`, `uk_overlap_big.py` | `uk_overlap_tally.py` |
| Appendix A.3, beyond the exact classes | `run_all.sh`, step 5 | `results/k_beyond*` |
| Appendix A.4, Theorem 5.3 | `run_all.sh`, step 8 | `kshard_tally.py` |

The stored results reproduce the numbers of the paper without a database:

```bash
python -I x4_shardk.py x4_shardk.json && python -I x4_unique.py x4_unique.json && python -I x4_freq.py x4_freq.json
python -I uk_tally.py results/uk_n5d5.jsonl results/uk_big_n7.jsonl
python -I uk_overlap_tally.py results/uk_overlap_n5d5.jsonl results/uk_overlap_big_n8.jsonl
python -I kshard_tally.py results/kshard_n4d4.jsonl,results/kshard_n5d5.jsonl results/kshard_recheck_n4d4.jsonl,results/kshard_recheck_n5d5.jsonl,results/kshard_recheck2_n5d5.jsonl results/kshard_recheck_n4d4_fresh.jsonl
python -I cind_analyze.py results/cind_random_n4.jsonl results/cind_random_n5.jsonl results/cind_recheck_random_n5.jsonl
python -I cind_analyze.py results/cind_named_n5.jsonl results/cind_recheck_named_n6.jsonl
```

`deciders.py` implements the tests of order-sufficiency: `dec_fd` (Theorem 3.6), `dec_unary` (Theorem 4.1), `dec_A` (Theorem 4.2) and `dec_K` (Theorem 4.5).
`m2.py` solves MIN-FREEZE (Theorem 5.1; "M2" in the code) by a minimum cut on bipartite conflict graphs and by a 2-approximation otherwise.
The shard-key condition of Theorem 5.2 is "B-pi" in the code.

```bash
PROCS=64 sh run_all.sh
```

- Step 1 compares the deciders with an exhaustive search over small updates (`selfcheck.py`, reference semantics in `ref_order2.py`) and builds witnesses for rejected schemas (`k_witness.py`).
- Steps 2 and 3 time the deciders single-threaded on random schemas with 10 attributes, 10 FDs and 2 INDs per relation (median of 5 seeds) and on the schema of GitLab (median of 20 runs), and compare the minimum cut with the 2-approximation.
- Step 4 compares the shard keys of Theorem 5.2 with containment rules and with the keys that GitLab declares, and the optimal freeze with freezing every copied source.
  `x4_unique.py` counts the tables with a single-column shard key that makes every fresh update executable and under which a shard checks every unique key (1,040 of 1,127); the rule of Citus leaves 600 tables without such a key (`none_citus` in `results/x4_shardkey.json`), so it admits one for 527.
  `x4_shardk.py` checks Theorem 5.3 on the 903 copies that GitLab maintains, 808 of sharding keys and 95 that callbacks write, under co-location by the declared sharding keys (Figure 8): the copies whose IND violates condition (4) of Theorem 4.5, and how many of them co-location carries, are 126 and 93 if every write counts as a modification, 45 and 35 without the initialisations of new rows, and 4 and 4 with frozen sharding keys.
  `x4_freq.py` draws template frequencies from four distributions and reports the weight that the optimal freeze saves over freezing every copied source (Figure 9 in Appendix B; medians 18%, 54%, 36% and 70%).
- Step 5 runs the checks of Appendix A.3 beyond the exact classes; it needs pycosat, as do steps 6 to 9.
- Step 6 checks Proposition 4.7 (fresh updates on class K) on 403 random schemas of class K by a bounded SAT search that admits only fresh updates (`fresh_k.py`, `fsat.py`), including the order that writes referencing relations first; `seq_sanity.py` confirms that the order check detects violations. It needs pycosat.
- Step 7 checks Proposition A.8 (unique keys that no IND references) by the same search on 4,172 pairs of random schemas of class K with one or two unique keys of one or two non-key attributes and freezes, with 5 entities and 5 values (`fresh_k_uk.py`).
  For the 6 pairs in which the prediction under Fz* finds no witness with 5 entities, `uk_big.py` finds one with 7.
  Fz* is the least fixpoint of Appendix A.3 (`uk_big.pinned`): until 2026-10-20 the function left the attributes of the referenced key out of X_rho, which changes no prediction on these 4,172 pairs, since their pointers do not overlap.
  `uk_overlap.py` checks 8,498 pairs of 144 schemas in which two INDs out of one relation share pointer attributes, with unique keys on sources, on sources together with key attributes and on copies (5 entities, 5 values); `uk_overlap_big.py` searches again, with 8 entities of which 3 change, the 487 pairs that some prediction rejects without a counterexample at 5.
  `uk_overlap_tally.py` scores three predictions: the fixpoint rejects 4,518 pairs, all confirmed, and refutes none of the others; computing every X_rho under Fz without iteration rejects 28 pairs wrongly, and the earlier `uk_big.pinned` 244.
  `uk_tally.py` scores the predictions: under Fz* none is wrong; under Fz, 157 pairs are predicted wrongly (168 checks, 12 of executability and 156 of order-insensitivity).
- Step 8 checks Theorem 5.3 under hash placement with co-location groups on 13,199 combinations of 280 random schemas of class K, freezes, shard keys of one attribute and groups (`kshard.py`), and searches again with more changed entities and unchanged rows for the predicted counterexamples that the first run did not find (`kshard_recheck.py`).
  `kshard_tally.py` prints the outcome: every combination predicted executable has no counterexample within the bounds, every combination predicted not executable has one, and for fresh updates the necessary condition of Appendix A.4 fails to be sufficient in 4 combinations.
- Step 9 checks Theorem 4.9 on 270 random schemas of class C (6,040 pairs of a schema and a freeze, 42,280 checks) and on 21 named families with 552 pairs (`cind_check.py`, `cind_sat.py`).
  For every pair it searches for a modification update without a legal order, one with an illegal intermediate state, one whose canonical order of part (1) fails, the same for updates that also insert and delete, and the same for fresh updates.
  On class C every prediction of part (1) holds, and part (2) agrees on 12,072 of 12,080 checks; the other 8 predict a violation that needs 5 entities, which `cind_recheck.py` finds.
  The named families cover the shapes of Section 6.2 (repositories of a disabled integration, SCIM flags, linked identities, the SCIM denial of GitLab, archiving in the running example), each restriction of class C (two tested attributes, sets that form no chain, a cyclic IND graph, a mutable unique key as the referenced key), the two restrictions on pointers (without them, no violation is found up to 6 entities in 7 pairs where the condition of part (2) fails), and combinations with FDs, unique keys and copies of class K, each with the prediction stated in `cind_check.py`.
  `cind_analyze.py` prints the agreement per family and check; `cind_smoke.py` runs a few families quickly.
- `data/gitlab_mutable.json` lists the columns that the code of GitLab commit 9d4cbde6 modifies, with every resolved write site by table, columns, file, line, method and operation (`gl_mutable.py`, which runs the analyzer of `apps/`); `data/gitlab_mutable_filtered.json` leaves out the initialisations of new rows (`gl_sites.py`), and `gl_sk_sites.py` lists the sites that still modify a declared sharding key.
- `data/gitlab_schema.json` holds the tables, keys, foreign keys, sharding keys and loose foreign keys of GitLab commit 9d4cbde6; `gitlab_extract.py` rebuilds it from that commit (usage in `run_all.sh`).
- The large per-task result files are stored gzipped.
