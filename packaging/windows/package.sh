#!/usr/bin/env bash
# Builds dist/LBPPC-<version>-windows.zip: LBPPC.exe (the launcher, frozen with PyInstaller so players need no Python;
# it also runs run.py and the preparation scripts with --script), bin/LBPPC-Game.exe (the game, built as out/LBPPC-Game.exe) with the MSYS2 CLANG64 DLLs it
# loads, DLSS when imported (import_dlss.py), the scripts, tools and documents. No game files: players point the
# launcher at their own dump. Run from an MSYS2 CLANG64 shell after `bash build.sh`.
# Freezing uses a Windows Python 3.10+ (python.org; WINPYTHON overrides) and a private venv in out/pyenv.
set -euo pipefail
cd -- "$(dirname -- "$0")/../.."
source ./msys2-env.sh
[[ -f out/LBPPC-Game.exe ]] || { echo 'Build first: bash build.sh' >&2; exit 1; }
version=$(sed -n "s/^VERSION = '\(.*\)'$/\1/p" launcher/lbppc_launcher.py)

# A Windows Python (not MSYS2's) for PyInstaller.
python=${WINPYTHON:-}
if [[ -z $python ]]; then
    for candidate in /c/Python3*/python.exe "${LOCALAPPDATA:-/c/Users/$USER/AppData/Local}"/Programs/Python/Python3*/python.exe; do
        [[ -x $candidate ]] && python=$candidate
    done
fi
[[ -n $python ]] || { echo 'Need a Windows Python 3 (python.org) or WINPYTHON=path\to\python.exe' >&2; exit 1; }
export USERPROFILE=${USERPROFILE:-$(cygpath -w "/c/Users/$(id -un)")}
if [[ ! -x out/pyenv/Scripts/python.exe ]]; then
    "$python" -m venv out/pyenv
fi
# Pillow draws the launcher's banner from the player's own dump.
out/pyenv/Scripts/python.exe -m pip install -q --disable-pip-version-check pyinstaller pillow
# The scripts run inside LBPPC.exe (--script): the standard modules they import come along.
hidden=()
for module in argparse base64 bisect collections contextlib ctypes ctypes.wintypes hashlib importlib.util json \
              platform re runpy secrets shutil socket struct subprocess tempfile time urllib.request \
              urllib.error zlib; do
    hidden+=(--hidden-import "$module")
done
out/pyenv/Scripts/python.exe -m PyInstaller --noconfirm --clean --log-level WARN --windowed --name LBPPC \
    --distpath out/pyi-dist --workpath out/pyi-work --specpath out/pyi-work \
    --paths "$(cygpath -w "$PWD/scripts")" "${hidden[@]}" \
    --add-data "$(cygpath -w "$PWD/launcher/fonts");fonts" --add-data "$(cygpath -w "$PWD/launcher/assets");assets" \
    --add-data "$(cygpath -w "$PWD/launcher/lang");lang" \
    --icon "$(cygpath -w "$PWD/launcher/assets/lbppc.ico")" "$(cygpath -w "$PWD/launcher/lbppc_launcher.py")"

dest=out/stage/LBPPC
rm -rf -- out/stage
mkdir -p "$dest/bin" "$dest/docs" "$dest/licenses" "$dest/tools"
cp -r out/pyi-dist/LBPPC/. "$dest/"
# The runtime without debug information (out/ keeps the symbols for crash reports).
# The game, stripped of its debug information.
llvm-strip --strip-debug -o "$dest/bin/LBPPC-Game.exe" out/LBPPC-Game.exe
# Every DLL it loads from the CLANG64 tree (SDL3, FFmpeg, OpenSSL, protobuf, ...). Not the Vulkan loader: the one
# the graphics driver installs in System32 is used.
ldd "$dest/bin/LBPPC-Game.exe" | awk '/\/clang64\/bin\// && !/vulkan-1\.dll/ {print $3}' | sort -u | while read -r dll; do
    cp -u "$dll" "$dest/bin/"
done
# DLSS (NVIDIA RTX): the bridge and NVIDIA's runtime (packaging/windows/import_dlss.py or build_dlss.sh).
if [[ -f out/lbppc_dlss.dll && -f out/nvngx_dlss.dll ]]; then
    cp out/lbppc_dlss.dll out/nvngx_dlss.dll "$dest/bin/"
    [[ -f out/nvngx_dlssg.dll ]] && cp out/nvngx_dlssg.dll "$dest/bin/"
    cp out/NVIDIA-DLSS-LICENSE.txt "$dest/licenses/"
    cp gpu/dlss_bridge/LICENSE.txt "$dest/licenses/lbppc_dlss-LICENSE.txt"
else
    echo 'No DLSS in this package (packaging/windows/import_dlss.py)' >&2
fi
cp -r scripts "$dest/"
cp tools/shadnet_account.py tools/refresh_account.py "$dest/tools/"
cp run.py input.ini LICENSE LICENSE-GPL-2.0 README.md "$dest/"
mkdir -p "$dest/assets" && cp launcher/assets/logo-128.rgba "$dest/assets/"  # the game window's icon (run.py)
cp docs/online.md docs/troubleshooting.md "$dest/docs/"
cp gpu/third_party/wepoll/LICENSE "$dest/licenses/wepoll-LICENSE.txt"
cp gpu/third_party/httplib/LICENSE "$dest/licenses/cpp-httplib-LICENSE.txt"
cp packaging/windows/licenses/*.txt "$dest/licenses/" 2>/dev/null || true
cp launcher/fonts/Fredoka-OFL.txt "$dest/licenses/"
cp gpu/bbnet/SHADNET-PORT.md "$dest/licenses/online-code-NOTICE.md"
for package in protobuf abseil-cpp openssl miniupnpc zlib SDL3 ffmpeg; do
    license=/clang64/share/licenses/$package/LICENSE
    if [[ -f $license ]]; then cp "$license" "$dest/licenses/$package-LICENSE.txt"; fi
done
find "$dest" -name __pycache__ -prune -exec rm -r {} +
mkdir -p dist
zip=dist/LBPPC-$version-windows
rm -f "$zip.zip"
out/pyenv/Scripts/python.exe -c "import shutil; shutil.make_archive('$zip', 'zip', 'out/stage')"
du -sh "$dest" "$zip.zip"
# The launcher's updater (scripts/updater.py) installs a release only when its SHA-256 matches: GitHub's own digest
# of the uploaded zip, or this file, uploaded to the release beside the zip.
sha256sum "$zip.zip" | cut -d' ' -f1 > "$zip.zip.sha256"
echo "Release: upload $zip.zip and $zip.zip.sha256 to a GitHub release (not a pre-release) of emurphy04/LBPPC"
