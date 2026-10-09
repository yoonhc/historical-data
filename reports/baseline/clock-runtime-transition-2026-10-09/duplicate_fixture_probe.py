"""Old-to-old control for identical synthetic input/receive-time reuse."""
import json
import sys
import tempfile
from pathlib import Path

import marketdata

NEW=Path('/tmp/marketdata-clock-evidence-verified-45t091g0')
sys.path.append(str(NEW/'.venv/lib/python3.12/site-packages'))
sys.path.insert(0,str(NEW/'tests'))
import pytest
import test_depth as d
from marketdata.store import Store
from marketdata import depth
root=Path(tempfile.mkdtemp(prefix='clock-duplicate-control-'))
s=Store(root)
result={'module':marketdata.__file__,'fixture':str(root),'input':'same update IDs, payloads and synthetic received_at reused in both collections','runs':[]}
try:
 for n in range(2):
  with pytest.MonkeyPatch.context() as mp:
   r=d.capture(s,mp,[[d.update(100),d.update(101)]],[d.snapshot()])
  q=depth.replay(s,r['id'])
  result['runs'].append({'id':r['id'],'replay_matches_live':q['replay_matches_live'],'rows':q['rows'],'report_messages':r['messages']})
 assert result['runs'][0]['replay_matches_live'] and not result['runs'][1]['replay_matches_live']
 result['preexisting_on_old_code']=True
finally:
 s.close()
 Path(__file__).with_name('duplicate-fixture-control.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result))
