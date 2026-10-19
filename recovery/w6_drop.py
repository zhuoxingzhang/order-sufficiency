"""Drop the tables of the recovery and deadlock runs (end of run_recovery.sh), leaving the tables of
cost/ as they were."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import w6lib as L  # noqa: E402

L.drop_tables()
print('[w6 tables dropped]')
