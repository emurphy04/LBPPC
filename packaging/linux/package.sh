#!/usr/bin/env bash
# Builds dist/LBPPC-<version>-linux-x86_64.AppImage (and its .sha256) from a Linux build (bash build.sh first):
# the launcher frozen with PyInstaller (no Python needed on the player's PC; it runs run.py and the preparation
# scripts with --script), the game (bin/LBPPC-Game, bin/gpu/libbbgpu.so), the libraries it needs that a desktop or
# SteamOS may not have (lib/), the scripts, tools and documents. No game files: players point the launcher at their
# own copy. The player's files live in ~/.local/share/LBPPC (packaging/linux/AppRun).
# Made on Ubuntu 24.04 (.github/workflows/linux-appimage.yml): the oldest system it runs on has that glibc (2.39).
# Needs: python3 with venv and tkinter (python3-venv python3-tk), patchelf, file, and appimagetool (APPIMAGETOOL, or
# on PATH).
set -euo pipefail
cd -- "$(dirname -- "$0")/../.."
[[ -f out/LBPPC-Game && -f out/gpu/libbbgpu.so ]] || { echo 'Build first: bash build.sh' >&2; exit 1; }
version=$(sed -n "s/^VERSION = '\(.*\)'$/\1/p" launcher/lbppc_launcher.py)
appimagetool=${APPIMAGETOOL:-$(command -v appimagetool || true)}
[[ -n $appimagetool ]] || { echo 'Need appimagetool (APPIMAGETOOL=path)' >&2; exit 1; }

# The launcher, frozen.
python=${PYTHON:-python3}
[[ -x out/pyenv-linux/bin/python ]] || "$python" -m venv out/pyenv-linux
out/pyenv-linux/bin/python -m pip install -q --disable-pip-version-check pyinstaller pillow
hidden=()
for module in argparse base64 bisect collections contextlib ctypes hashlib importlib.util json platform re runpy \
              secrets shutil signal socket struct subprocess tempfile time urllib.request urllib.error zlib; do
    hidden+=(--hidden-import "$module")
done
out/pyenv-linux/bin/python -m PyInstaller --noconfirm --clean --log-level WARN --windowed --name LBPPC \
    --distpath out/pyi-dist-linux --workpath out/pyi-work-linux --specpath out/pyi-work-linux \
    --paths "$PWD/scripts" "${hidden[@]}" \
    --add-data "$PWD/launcher/fonts:fonts" --add-data "$PWD/launcher/assets:assets" \
    --add-data "$PWD/launcher/lang:lang" "$PWD/launcher/lbppc_launcher.py"

appdir=out/AppDir
rm -rf -- "$appdir"
dest=$appdir/opt/lbppc
mkdir -p "$dest/bin/gpu" "$dest/lib" "$dest/docs" "$dest/licenses" "$dest/tools" "$dest/assets"
cp -r out/pyi-dist-linux/LBPPC/. "$dest/"
# The game without its debug information (out/ keeps the symbols for crash reports).
strip --strip-debug -o "$dest/bin/LBPPC-Game" out/LBPPC-Game
strip --strip-debug -o "$dest/bin/gpu/libbbgpu.so" out/gpu/libbbgpu.so
cp out/bb-gpu-capabilities "$dest/bin/"
chmod 755 "$dest/bin/LBPPC-Game" "$dest/bin/gpu/libbbgpu.so" "$dest/bin/bb-gpu-capabilities"

# Libraries: everything the game loads except what every Linux desktop has and must come from the system (the C
# library, the graphics driver and Vulkan loader, X11/Wayland, sound, OpenSSL, zlib). SDL3 loads the display and
# sound libraries itself when it needs them.
# Whole names up to .so: a bare prefix took libminiupnpc for libm (and libcrypto, libcap for libc), leaving it out.
system='^(ld-linux[-a-z0-9_]*|libc|libm|libdl|libpthread|librt|libresolv|libutil|libgcc_s|libstdc\+\+|libvulkan|'
system+='libX[-A-Za-z0-9]*|libxcb[-a-z0-9]*|libwayland-[a-z]+|libxkbcommon[-a-z0-9]*|libdecor-[0-9]+|libdrm[_a-z]*|'
system+='libgbm|libGL|libGLX|libEGL|libOpenGL|libGLdispatch|libasound|libpulse[-a-z]*|libpipewire-[0-9.]+|libjack|'
system+='libdbus-[0-9]+|libudev|libsystemd|libz|libssl|libcrypto|libexpat|libffi|libuuid|libcap|liblzma|libzstd|'
system+='liblz4|libgcrypt|libgpg-error|libbsd|libmd)\.so'
bundled=()
for elf in out/LBPPC-Game out/gpu/libbbgpu.so; do
    while read -r name path; do
        [[ -n $path && -f $path ]] || continue
        [[ $name =~ $system ]] && continue
        [[ $name == libbbgpu.so ]] && continue  # bin/gpu/libbbgpu.so, found through the game's $ORIGIN/gpu
        [[ -f $dest/lib/$name ]] && continue
        cp -L "$path" "$dest/lib/$name"
        bundled+=("$name")
    done < <(ldd "$elf" | awk '$2 == "=>" {print $1, $3}')
done
# Their own dependencies (FFmpeg's, SDL3's) are found next to them.
for library in "$dest"/lib/*.so*; do
    [[ -f $library ]] && patchelf --set-rpath '$ORIGIN' "$library"
done
echo "Bundled: ${bundled[*]:-nothing}"
# The C++ library the game was built with, for systems whose own is older (run.py uses it only then).
needs=$(objdump -T out/LBPPC-Game out/gpu/libbbgpu.so "$dest"/lib/*.so* 2>/dev/null |
        grep -o 'GLIBCXX_3\.4\.[0-9]*' | sort -t. -k3 -n -u | tail -1)
mkdir -p "$dest/lib/compat"
cp -L "$(g++ -print-file-name=libstdc++.so.6)" "$dest/lib/compat/libstdc++.so.6"
cp -L "$(gcc -print-file-name=libgcc_s.so.1)" "$dest/lib/compat/libgcc_s.so.1"
echo "$needs" > "$dest/lib/compat/NEEDS"
echo "C++ library needed: ${needs:-none}"
glibc=$(objdump -T out/LBPPC-Game out/gpu/libbbgpu.so "$dest"/lib/*.so* 2>/dev/null |
        grep -o 'GLIBC_2\.[0-9]*' | sort -t. -k2 -n -u | tail -1)
echo "Needs glibc: $glibc"

# The shader list (tools/shader_list.py): recorded pipelines and where their shaders are, no game code.
if [[ -f out/lbp3-shaders.bin ]]; then
    cp out/lbp3-shaders.bin "$dest/bin/"
else
    echo 'No shader list in this package (tools/shader_list.py)' >&2
fi
cp -r scripts "$dest/"
cp tools/shadnet_account.py tools/refresh_account.py "$dest/tools/"
# The default controls: packaging/input.ini (the working copy, input.ini, is the developer's and is not in git).
cp packaging/input.ini "$dest/input.ini"
cp run.py LICENSE LICENSE-GPL-2.0 README.md "$dest/"
cp launcher/assets/logo-128.rgba "$dest/assets/"  # the game window's icon (run.py)
cp docs/online.md docs/troubleshooting.md "$dest/docs/"
cp gpu/third_party/httplib/LICENSE "$dest/licenses/cpp-httplib-LICENSE.txt"
cp launcher/fonts/Fredoka-OFL.txt "$dest/licenses/"
cp gpu/bbnet/SHADNET-PORT.md "$dest/licenses/online-code-NOTICE.md"
for package in libsdl3-0 libprotobuf32t64 libzydis4 libfmt9 libxxhash0; do
    [[ -f /usr/share/doc/$package/copyright ]] && cp "/usr/share/doc/$package/copyright" "$dest/licenses/$package-copyright.txt"
done
for license in "${DEPS_PREFIX:-/opt/deps}"/share/licenses/*; do
    [[ -f $license ]] && cp "$license" "$dest/licenses/"
done
find "$dest" -name __pycache__ -prune -exec rm -r {} +
printf 'LBPPC_VERSION=%s\nBUILD=Linux AppImage\nSOURCE=%s\n' "$version" \
    "$(git rev-parse --short HEAD 2>/dev/null || echo unknown)$(git diff --quiet HEAD 2>/dev/null || echo ' (changed)')" \
    > "$dest/VERSION"

install -m755 packaging/linux/AppRun "$appdir/AppRun"
cp packaging/linux/lbppc.desktop "$appdir/lbppc.desktop"
out/pyenv-linux/bin/python -c 'import sys; from PIL import Image
Image.open(sys.argv[1]).convert("RGBA").resize((256, 256), Image.LANCZOS).save(sys.argv[2])' \
    launcher/assets/logo.png "$appdir/lbppc.png"
ln -s lbppc.png "$appdir/.DirIcon"
mkdir -p "$appdir/usr/share/applications" "$appdir/usr/share/icons/hicolor/256x256/apps"
cp packaging/linux/lbppc.desktop "$appdir/usr/share/applications/"
cp "$appdir/lbppc.png" "$appdir/usr/share/icons/hicolor/256x256/apps/lbppc.png"

mkdir -p dist
image=dist/LBPPC-$version-linux-x86_64.AppImage
rm -f "$image" "$image.sha256"
ARCH=x86_64 "$appimagetool" --no-appstream "$appdir" "$image"
(cd dist && sha256sum "$(basename "$image")" > "$(basename "$image").sha256")
ls -l "$image"
cat "$image.sha256"
