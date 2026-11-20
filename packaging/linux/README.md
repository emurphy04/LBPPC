# Building LBPPC on Linux

LBPPC builds and packages an AppImage on Ubuntu 24.04. Its glibc (2.39) is the oldest the AppImage runs on. The
[Dockerfile](Dockerfile) gives you that environment on any Linux machine: the packages, GCC 14, the libraries built
from source (Vulkan headers, Vulkan Memory Allocator, glslang, SDL3 and FFmpeg) and appimagetool 1.9.1, all matching
[.github/workflows/linux-appimage.yml](../../.github/workflows/linux-appimage.yml).

The source is not copied into the image. Your checkout is mounted at `/src`, so `out/` and `dist/` stay on your
machine. You need a checkout with its submodules:

```bash
git submodule update --init --recursive
```

No game files are needed to build.

## Quick start: one command for the game and the AppImage

If you have not built the image yet, follow [Build the image](#build-the-image) first. Then, from the repository root:

```sh
docker run --rm -v "$PWD":/src lbppc-linux \
  bash -lc 'bash build.sh && bash packaging/linux/package.sh'
```

The container writes `out/` and `dist/` into your checkout, so the AppImage and its `.sha256` appear in `dist/` when
it finishes. Build output is printed as it happens; see [Build output](#build-output) below. If this checkout was
built on the host before, delete `out/` first (`rm -rf out`): CMake's cache and objects belong to the host compiler.

## Build the image

Match the image's user to yours, so the mounted checkout and the files it writes stay yours:

```bash
docker build --progress=plain -t lbppc-linux \
  --build-arg UID="$(id -u)" --build-arg GID="$(id -g)" \
  -f packaging/linux/Dockerfile packaging/linux
```

`--progress=plain` prints every step and all compiler output instead of a live progress bar. Omit it if you prefer
Docker's compact output.

The first build downloads and compiles the libraries: it takes a while, needs a few GB of disk space, and needs
network access (as does the first packaging run, for PyInstaller). Docker keeps the result in the image; later builds
reuse those layers.

## Build the game and the AppImage

The quick start above runs both steps in one command. To run them in a shell instead, as you would on Ubuntu 24.04:

```bash
docker run --rm -it -v "$PWD":/src lbppc-linux
# inside the container:
bash build.sh
bash packaging/linux/package.sh
```

Packaging makes `dist/LBPPC-<version>-linux-x86_64.AppImage` and its `.sha256` beside it. Mark the AppImage
executable and run it directly. Packaging installs PyInstaller, Pillow and PySide6-Essentials into `out/pyenv-linux` the
first time, and keeps them there for later builds. To cache pip's downloads between fresh checkouts too:

```bash
docker run --rm -it -v "$PWD":/src -v lbppc-pip:/home/builder/.cache/pip lbppc-linux
```

## Build output

Both scripts print their progress as they work, whether run directly on Ubuntu 24.04 or in Docker:

- `build.sh` shows CMake's configure output and pipes Ninja through `tee`, so every compile and link line goes to
  stdout and the same text is saved to `out/gpu-build.log`.
- `packaging/linux/package.sh` shows pip's downloads and PyInstaller's `INFO` messages.

To keep a copy of the whole Docker session on the host as well, pipe `docker run` through `tee`:

```sh
docker run --rm -v "$PWD":/src lbppc-linux \
  bash -lc 'bash build.sh && bash packaging/linux/package.sh' 2>&1 | tee /tmp/lbppc-docker-build.log
```

## Notes

- **Linux containers only.** On Docker Desktop, make sure it is using Linux containers, not Windows ones.
- **A different user ID.** Building the image with your own `UID`/`GID` is the simplest way to keep the checkout
  yours. Without that, run the container as yourself and tell Git the mount is safe:

  ```bash
  docker run --rm -it --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0=/src \
    -v "$PWD":/src lbppc-linux
  ```

- **Tests.** `bash build.sh --test` builds and runs the test programs as well.
- **Podman** works the same way; replace `docker` with `podman`.
- **The AppImage's glibc.** Packages built outside this image (for example on a rolling-release distribution) can
  need a newer glibc than players have. The image keeps the release build at 2.39.
