"""Package Coding sources only; databases, logs and keys are excluded."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parent
FILES=('.gitignore','README.md','requirements.txt','app.py','coding_config.py',
       'coding_models.py','coding_reranker.py','coding_store.py','code_windows.py',
       'watcher.ps1','test_code_windows.py','test_coding_store.py','test_api_contract.py',
       'test_coding_models.py','test_coding_reranker.py','prepare_submission.py',
       'evidence_packing.py','test_evidence_packing.py','ranking_copies.py',
       'test_ranking_copies.py','logging.json','code_structure.py')

def main():
    payloads={name:(ROOT/name).read_bytes() for name in FILES}
    manifest={'system':'MemoryShield+ Coding','version':'v4.1.1','track':'coding',
              'repository':'https://github.com/GFDDFDGHJ/memoryshield-plus-aml',
              'files':{name:hashlib.sha256(value).hexdigest() for name,value in payloads.items()}}
    raw=(json.dumps(manifest,indent=2)+'\n').encode('utf-8')
    (ROOT/'submission-manifest.json').write_bytes(raw)
    with zipfile.ZipFile(ROOT/'submission-source.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,value in sorted({**payloads,'submission-manifest.json':raw}.items()):
            info=zipfile.ZipInfo(name,date_time=(2026,10,24,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,value)
    print('Coding source files:',len(FILES))
    print('Coding archive SHA256:',hashlib.sha256((ROOT/'submission-source.zip').read_bytes()).hexdigest())

if __name__=='__main__':main()
