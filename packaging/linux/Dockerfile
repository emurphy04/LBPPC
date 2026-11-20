# Ubuntu 24.04 build environment for LBPPC on Linux: the game, run.py and the AppImage packaging.
#
# It follows .github/workflows/linux-appimage.yml: the same packages, the same libraries built from source (the
# Vulkan headers, Vulkan Memory Allocator, glslang, SDL3 and FFmpeg), GCC 14, and appimagetool 1.9.1. Ubuntu 24.04's
# glibc (2.39) is the oldest the AppImage runs on.
#
# The source is not copied into the image: it is mounted at /src, so the checkout and the build's output stay on the
# host. See packaging/linux/README.md for the commands and the one-shot build.
#
# Build (match your user's IDs so the mounted checkout and its output stay yours):
#   docker build -t lbppc-linux \
#     --build-arg UID="$(id -u)" --build-arg GID="$(id -g)" \
#     -f packaging/linux/Dockerfile packaging/linux
# Run:
#   docker run --rm -it -v "$PWD":/src lbppc-linux
#   bash build.sh && bash packaging/linux/package.sh
FROM ubuntu:24.04

ARG UID=1000
ARG GID=1000
ARG VULKAN_HEADERS=v1.4.363
ARG VMA=v3.4.0
ARG GLSLANG=16.6.0
ARG SDL3=release-3.4.18
ARG FFMPEG=n8.1.3
ARG APPIMAGETOOL_VERSION=1.9.1
ARG APPIMAGETOOL_SHA256=ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0

ENV DEPS=/opt/deps \
    PKG_CONFIG_PATH=/opt/deps/lib/pkgconfig \
    CMAKE_PREFIX_PATH=/opt/deps \
    LD_LIBRARY_PATH=/opt/deps/lib \
    CC=gcc-14 \
    CXX=g++-14 \
    BB_PGO=off \
    APPIMAGETOOL=/opt/appimagetool \
    APPIMAGE_EXTRACT_AND_RUN=1 \
    HOME=/home/builder

# Packages from the workflow. Not libvulkan-dev: its headers are older than the GPU code needs (the newer ones are
# built below); the loader itself (libvulkan1) is linked.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gcc-14 g++-14 cmake ninja-build make pkg-config nasm patchelf file git curl ca-certificates binutils \
        libvulkan1 libboost-dev libfmt-dev libxxhash-dev libzydis-dev libprotobuf-dev protobuf-compiler \
        nlohmann-json3-dev robin-map-dev libssl-dev zlib1g-dev libminiupnpc-dev \
        libx11-dev libxext-dev libxrandr-dev libxcursor-dev libxfixes-dev libxi-dev libxss-dev libxtst-dev \
        libxkbcommon-dev libwayland-dev wayland-protocols libdecor-0-dev libegl-dev libgl-dev libgles-dev \
        libdrm-dev libgbm-dev libasound2-dev libpulse-dev libpipewire-0.3-dev libdbus-1-dev libudev-dev \
        libusb-1.0-0-dev libibus-1.0-dev \
        python3 python3-venv python3-dev \
    && rm -rf /var/lib/apt/lists/*

# The libraries Ubuntu's versions are too old for, or that would bring many libraries with them. Kept in one layer:
# they only change when one of the versions above does.
RUN set -eux; \
    mkdir -p "$DEPS"; \
    cd /tmp; \
    fetch() { git -c advice.detachedHead=false clone -q --depth 1 --branch "$2" "https://github.com/$1.git" "$3"; }; \
    fetch KhronosGroup/Vulkan-Headers "$VULKAN_HEADERS" vulkan-headers; \
    cmake -S vulkan-headers -B vulkan-headers/build -G Ninja -DCMAKE_INSTALL_PREFIX="$DEPS" \
        -DVULKAN_HEADERS_ENABLE_MODULE=OFF; \
    cmake --install vulkan-headers/build; \
    fetch GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator "$VMA" vma; \
    cmake -S vma -B vma/build -G Ninja -DCMAKE_INSTALL_PREFIX="$DEPS" -DCMAKE_PREFIX_PATH="$DEPS"; \
    cmake --install vma/build; \
    fetch KhronosGroup/glslang "$GLSLANG" glslang; \
    cmake -S glslang -B glslang/build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$DEPS" \
        -DENABLE_OPT=OFF -DGLSLANG_TESTS=OFF -DGLSLANG_ENABLE_INSTALL=ON -DBUILD_SHARED_LIBS=OFF; \
    cmake --build glslang/build --parallel "$(nproc)"; \
    cmake --install glslang/build; \
    fetch libsdl-org/SDL "$SDL3" sdl3; \
    cmake -S sdl3 -B sdl3/build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$DEPS" \
        -DSDL_SHARED=ON -DSDL_STATIC=OFF -DSDL_TESTS=OFF -DSDL_EXAMPLES=OFF; \
    cmake --build sdl3/build --parallel "$(nproc)"; \
    cmake --install sdl3/build; \
    fetch FFmpeg/FFmpeg "$FFMPEG" ffmpeg; \
    cd ffmpeg; \
    # Only what the game's video player needs (MP4/MPEG-TS/Matroska with H.264, HEVC, AAC and friends), and no
    # system libraries: nothing for the AppImage to bring along beyond FFmpeg's own.
    ./configure --prefix="$DEPS" --cc=gcc-14 --enable-shared --disable-static --enable-pic \
        --disable-programs --disable-doc --disable-autodetect --disable-network --disable-avdevice \
        --disable-avfilter --disable-everything \
        --enable-decoder=h264,hevc,mpeg4,mpeg2video,mpeg1video,vp8,vp9,aac,aac_latm,mp3,mp2,ac3,eac3,opus,vorbis,flac,pcm_s16le,pcm_s16be,pcm_s24le,pcm_f32le \
        --enable-demuxer=mov,mpegts,mpegps,matroska,mp3,aac,h264,hevc,ogg,wav,avi,m4v \
        --enable-parser=h264,hevc,aac,aac_latm,mpeg4video,mpegvideo,mpegaudio,ac3,vp8,vp9,opus,vorbis,flac \
        --enable-protocol=file,pipe --enable-bsf=h264_mp4toannexb,hevc_mp4toannexb,aac_adtstoasc; \
    make -j"$(nproc)"; \
    make install; \
    mkdir -p "$DEPS/share/licenses"; \
    cp LICENSE.md "$DEPS/share/licenses/FFmpeg-LICENSE.md"; \
    cp COPYING.LGPLv2.1 "$DEPS/share/licenses/FFmpeg-COPYING.LGPLv2.1"; \
    cp ../sdl3/LICENSE.txt "$DEPS/share/licenses/SDL3-LICENSE.txt"; \
    cd /; rm -rf /tmp/vulkan-headers /tmp/vma /tmp/glslang /tmp/sdl3 /tmp/ffmpeg

# The loader from libvulkan1, under the name the build links (libvulkan-dev would bring old headers), and gcc/g++ for
# package.sh's C++ library lookup. appimagetool runs without FUSE (APPIMAGE_EXTRACT_AND_RUN=1 above).
RUN set -eux; \
    mkdir -p "$DEPS/lib/pkgconfig"; \
    ln -sf /usr/lib/x86_64-linux-gnu/libvulkan.so.1 "$DEPS/lib/libvulkan.so"; \
    printf '%s\n' "prefix=$DEPS" 'libdir=${prefix}/lib' 'includedir=${prefix}/include' '' \
        'Name: Vulkan-Loader' 'Description: Vulkan Loader' "Version: ${VULKAN_HEADERS#v}" \
        'Libs: -L${libdir} -lvulkan' 'Cflags: -I${includedir}' > "$DEPS/lib/pkgconfig/vulkan.pc"; \
    ln -sf /usr/bin/gcc-14 /usr/local/bin/gcc; \
    ln -sf /usr/bin/g++-14 /usr/local/bin/g++; \
    curl -fsSL -o /opt/appimagetool \
        "https://github.com/AppImage/appimagetool/releases/download/$APPIMAGETOOL_VERSION/appimagetool-x86_64.AppImage"; \
    echo "$APPIMAGETOOL_SHA256  /opt/appimagetool" | sha256sum -c -; \
    chmod +x /opt/appimagetool

# Builds run as an ordinary user: the mounted checkout, out/ and dist/ stay theirs when UID/GID match the host's.
# Ubuntu's image already has a user with ID 1000 (ubuntu), so only make one when the ID is free, and enter the
# container by ID: that needs no passwd entry and works with either user.
RUN if ! getent group "$GID" >/dev/null; then groupadd --gid "$GID" builder; fi \
    && if ! getent passwd "$UID" >/dev/null; then useradd --uid "$UID" --gid "$GID" --shell /bin/bash builder; fi \
    && mkdir -p /home/builder && chown "$UID:$GID" /home/builder \
    && git config --system --add safe.directory /src
USER $UID:$GID
WORKDIR /src
CMD ["bash"]
