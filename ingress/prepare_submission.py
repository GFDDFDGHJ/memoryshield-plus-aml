"""Package shared transport source; no runtime files or auth credentials."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parent
FILES=('.gitignore','README.md','gateway.py','watcher.ps1','test_gateway.py','prepare_submission.py',
       'legacy_mode.py','test_legacy_mode.py','set_legacy_target.py','test_set_legacy_target.py',
       'test_local_target.py')

def main():
    payloads={name:(ROOT/name).read_bytes() for name in FILES}
    manifest={'system':'MemoryShield+ track gateway','version':'v1.1.0',
              'repository':'https://github.com/GFDDFDGHJ/memoryshield-plus-aml',
              'files':{name:hashlib.sha256(value).hexdigest() for name,value in payloads.items()}}
    raw=(json.dumps(manifest,indent=2)+'\n').encode('utf-8')
    (ROOT/'submission-manifest.json').write_bytes(raw)
    with zipfile.ZipFile(ROOT/'submission-source.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,value in sorted({**payloads,'submission-manifest.json':raw}.items()):
            info=zipfile.ZipInfo(name,date_time=(2026,10,24,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,value)
    print('Gateway archive SHA256:',hashlib.sha256((ROOT/'submission-source.zip').read_bytes()).hexdigest())

if __name__=='__main__':main()
