#!/usr/bin/env python3
"""Puts the DLSS files next to out/LBPPC-Game.exe from a bbport Windows release (its zip or unpacked folder):
bbport_dlss.dll (gpu/dlss_bridge, the same source here), NVIDIA's nvngx_dlss.dll and nvngx_dlssg.dll, and their
licenses. build_dlss.sh builds the same files from source but needs Visual Studio and the NVIDIA DLSS SDK.

usage: python packaging/windows/import_dlss.py <bbport-windows-X.zip | bbport-windows folder>
"""
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'out'
FILES = {'bin/bbport_dlss.dll': 'lbppc_dlss.dll', 'bin/nvngx_dlss.dll': 'nvngx_dlss.dll',
         'bin/nvngx_dlssg.dll': 'nvngx_dlssg.dll', 'licenses/NVIDIA-DLSS-LICENSE.txt': 'NVIDIA-DLSS-LICENSE.txt'}


def main(argv):
    if len(argv) != 1:
        sys.exit(__doc__)
    source = Path(argv[0])
    OUT.mkdir(exist_ok=True)
    if source.is_file():
        with zipfile.ZipFile(source) as archive:
            names = {name.split('/', 1)[1]: name for name in archive.namelist() if '/' in name}
            missing = [path for path in FILES if path not in names]
            if missing:
                sys.exit(f'{source} has no {", ".join(missing)}')
            for path, target in FILES.items():
                with archive.open(names[path]) as src, (OUT / target).open('wb') as dst:
                    shutil.copyfileobj(src, dst)
    else:
        missing = [path for path in FILES if not (source / path).is_file()]
        if missing:
            sys.exit(f'{source} has no {", ".join(missing)}')
        for path, target in FILES.items():
            shutil.copyfile(source / path, OUT / target)
    print('DLSS ready: ' + ', '.join(f'out/{target}' for target in FILES.values()))


if __name__ == '__main__':
    main(sys.argv[1:])
