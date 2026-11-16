"""Make the shader list LBPPC ships (out/lbp3-shaders.bin): the pipelines a recorded play used, so a fresh install
builds them on its first start instead of freezing on them in play.

usage: python tools/shader_list.py <game folder> <cache folder> [<cache folder> ...] [--update <update folder>]
       [-o out/lbp3-shaders.bin]

--update: the 1.28 update in a folder of its own (the game reads its files over the game's, and so does this).

A cache folder is LBPPC's pipeline cache after playing (user/cache/CUSA00473): its .meta (a shader's metadata:
how it was used, its resources; for a vertex shader also its attributes' buffer descriptors, *_vs.meta) and .key
(a pipeline's state) files go in the list as they are. Its .spv files
(translated shader code) do not: for each shader only its place in the game files is recorded (the file, the
SHA-1 of the archive resource, the offset of the shader's footer), and the game translates it from the player's
own copy (vk_pipeline_serialization.cpp, InstallShaderList). The list carries no game code.

Several cache folders are merged only if they don't disagree: a permutation name (program hash + index) is per
cache, so a name with different metadata in two caches keeps the first one's and drops the others' pipelines
that use it.
"""
import argparse
import glob
import os
import re
import struct
import sys
import time
import zlib


def unpack(data):
    """LittleBigPlanet resources: every zlib stream, unpacked and joined (as UnpackResource does in the game)."""
    out, i = [], 0
    while True:
        i = data.find(b'\x78\xda', i)
        if i < 0:
            break
        d = zlib.decompressobj()
        try:
            out.append(d.decompress(data[i:]))
        except zlib.error:
            i += 1
            continue
        if not d.eof:
            break  # the data ends inside the stream: what it gave is kept
        i = len(data) - len(d.unused_data)
    return b''.join(out)


def footers(data, wanted):
    """{program hash: footer offset} for the wanted shaders in data."""
    out = {}
    pos = data.find(b'OrbShdr')
    while pos != -1:
        if pos + 24 <= len(data):
            h = struct.unpack_from('<Q', data, pos + 16)[0]
            if h in wanted and h not in out:
                out[h] = pos
        pos = data.find(b'OrbShdr', pos + 7)
    return out


def game_files(game, update, pattern):
    """The files the game reads, by name: the update's copy over the game's (GameFiles::Path in the game)."""
    files = {}
    for folder in ([game] + ([update] if update else [])):
        for path in glob.glob(os.path.join(folder, pattern)):
            files[os.path.basename(path)] = path
    return files


def locate(game, wanted, update=None):
    """{program hash: (file, sha1 bytes or 20 zeros, footer offset)} for the wanted shaders found in the copy."""
    found = {}
    eboot = game_files(game, update, 'eboot.bin').get('eboot.bin')
    if eboot:
        for h, pos in footers(open(eboot, 'rb').read(), wanted).items():
            found[h] = ('eboot.bin', bytes(20), pos)
    farcs = sorted(game_files(game, update, '*.farc').values(),
                   key=lambda p: (not os.path.basename(p).startswith('patch'), os.path.basename(p)))
    for farc in farcs:
        name = os.path.basename(farc)
        with open(farc, 'rb') as f:
            f.seek(-8, 2)
            n, magic = struct.unpack('>I4s', f.read(8))
            if magic != b'FARC':
                continue
            f.seek(-8 - n * 28, 2)
            table = f.read(n * 28)
            for k in range(n):
                if len(found) == len(wanted):
                    return found
                sha1 = table[k * 28:k * 28 + 20]
                off, size = struct.unpack_from('>II', table, k * 28 + 20)
                f.seek(off)
                head = f.read(4)
                if not (head.endswith(b'b') and head[:3].isalpha()):  # binary LBP resources only
                    continue
                f.seek(off)
                data = unpack(f.read(size))
                for h, pos in footers(data, wanted.keys() - found.keys()).items():
                    found[h] = (name, sha1, pos)
        print(f'  {name}: {len(found)} of {len(wanted)} found', file=sys.stderr)
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('game')
    parser.add_argument('caches', nargs='+')
    parser.add_argument('--update', help='the 1.28 update in a folder of its own')
    parser.add_argument('-o', '--out', default=os.path.join('out', 'lbp3-shaders.bin'))
    args = parser.parse_args()
    start = time.time()

    metas, keys, versions, sharps = {}, {}, set(), {}
    dropped = set()
    for cache in args.caches:
        theirs = {}
        for path in glob.glob(os.path.join(cache, '*.meta')):
            blob = open(path, 'rb').read()
            name = os.path.basename(path)[:-5]
            if name.endswith('_vs'):  # a vertex shader's attribute V#s (RegisterAttribSharps)
                sharps.setdefault(name[:-3], blob)
                continue
            versions.add(('meta',) + struct.unpack_from('<II', blob, 0))
            if name in metas and metas[name] != blob:
                dropped.add((cache, name))
                continue
            theirs[name] = blob
        for path in glob.glob(os.path.join(cache, '*.key')):
            blob = open(path, 'rb').read()
            versions.add(('key', struct.unpack_from('<I', blob, 0)[0]))
            keys.setdefault(os.path.basename(path)[:-4], (cache, blob))
        metas.update(theirs)
    if len({v for v in versions if v[0] == 'meta'}) > 1 or len({v for v in versions if v[0] == 'key'}) > 1:
        sys.exit(f'The caches were written by builds with different cache versions: {sorted(versions)}')
    (_, meta_version, binary_version), = [v for v in versions if v[0] == 'meta']
    (_, key_version), = [v for v in versions if v[0] == 'key']
    # A pipeline whose stage name lost to another cache's metadata is left out (its stages differ).
    keys = {name: blob for name, (cache, blob) in keys.items()
            if not any(c == cache and struct.pack('<Q', int(m, 16)) in blob for c, m in dropped)}

    # The program hash of each metadata's shader: in the .spv names (0x<hash>_<permutation>_v<version>.spv).
    programs = set()
    for cache in args.caches:
        for path in os.listdir(cache):
            m = re.match(r'(0x[0-9a-f]{16})_\d+_v\d+\.spv$', path)
            if m:
                programs.add(int(m.group(1), 16))
    print(f'{len(metas)} shader metadata, {len(keys)} pipelines, {len(programs)} programs; finding them in the game files',
          file=sys.stderr)
    found = locate(args.game, {h: None for h in programs}, args.update)
    print(f'{len(found)} of {len(programs)} programs found in the game files '
          f'({len(programs) - len(found)} are made in play and stay compiled in play)', file=sys.stderr)

    def s(text):
        raw = text.encode()
        return struct.pack('<H', len(raw)) + raw

    def b(blob):
        return struct.pack('<I', len(blob)) + blob

    # Metadata grouped by where its shader is (the game reads each resource once).
    order = {h: (f, sha1) for h, (f, sha1, _pos) in found.items()}

    def meta_program(blob):
        """The program hash in a metadata blob's Info: found by matching the known program hashes."""
        for h in programs:
            if struct.pack('<Q', h) in blob:
                return h
        return None

    meta_items = sorted(metas.items(), key=lambda kv: order.get(meta_program(kv[1]), ('~', b'')))
    out = bytearray(b'LBPSLST2')
    out += struct.pack('<III', meta_version, binary_version, key_version)
    out += struct.pack('<I', len(found))
    for h, (f, sha1, pos) in sorted(found.items(), key=lambda kv: (kv[1][0], kv[1][1], kv[1][2])):
        out += struct.pack('<Q', h) + s(f) + sha1 + struct.pack('<I', pos)
    out += struct.pack('<I', len(meta_items))
    for name, blob in meta_items:
        out += s(name) + b(blob)
    out += struct.pack('<I', len(keys))
    for name, blob in sorted(keys.items()):
        out += s(name) + b(blob)
    sharps = {name: blob for name, blob in sharps.items() if name in metas}
    out += struct.pack('<I', len(sharps))
    for name, blob in sorted(sharps.items()):
        out += struct.pack('<Q', int(name, 16)) + b(blob)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    open(args.out, 'wb').write(out)
    print(f'{args.out}: {len(out) / 1024:.0f} KiB, {time.time() - start:.0f} s', file=sys.stderr)


if __name__ == '__main__':
    main()
