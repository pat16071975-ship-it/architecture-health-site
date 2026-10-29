"""Temporary preparation of two existing test harnesses; assertions retained."""
from pathlib import Path
import subprocess

BASE='2a0301e28d01a7ebaeae81e1e96b9ce4362930b7'
paths=['tests/verify-paid-periods.cjs','az-baze-auth/test_manual_entry_consistency.py']
for name in paths:
    p=Path(name)
    assert p.read_bytes()==subprocess.check_output(['git','show',f'{BASE}:{name}']), name
    s=p.read_text()
    assert s.count('20261028-paid-patients-1')==1, name
    s=s.replace('20261028-paid-patients-1','20261029-paid-history-1')
    if name.endswith('.cjs'):
        old="part(view,'  function renderDateComparison(){','  let dateStickyRaf=')"
        new="part(view,'  function monthlyReportColumn(','  let dateStickyRaf=')"
        assert s.count(old)==1
        s=s.replace(old,new)
    p.write_text(s)
print('EXISTING_TEST_HARNESS=UPDATED; CALCULATION_ASSERTIONS=UNCHANGED')
