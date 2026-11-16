/* lbppc: the JPEG codec behind sceJpegEnc and sceJpegDec (runtime_extra.c): stb_image and stb_image_write
 * (third_party/stb, public domain, pinned in third_party/stb/COMMIT), compiled here alone so their own
 * warnings stay out of the runtime's -Werror. LBP3 encodes its photos, and costume and level icons, with
 * sceJpegEnc; without an encoder every photo came out black. Decoding reads JPEGs that came from other
 * players through the server, so only baseline and progressive JPEG are built (STBI_ONLY_JPEG), from memory
 * only, at most 8192 pixels a side. */
#include "runtime_jpeg_codec.h"
#include <stdlib.h>
#include <string.h>

#if defined(__clang__)
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Weverything"
#elif defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wall"
#pragma GCC diagnostic ignored "-Wextra"
/* GCC takes no warning groups here: the unused parts of the headers are named. */
#pragma GCC diagnostic ignored "-Wunused-function"
#endif
#define STBI_ONLY_JPEG
#define STBI_NO_STDIO
/* Not STBI_NO_LINEAR: with it and STBI_NO_HDR, stb_image's (unused) 16-bit conversion calls stbi__mad4sizes_valid
   without its definition, an error in GCC 14. */
#define STBI_NO_HDR
#define STBI_MAX_DIMENSIONS 8192
#define STB_IMAGE_STATIC
#define STB_IMAGE_IMPLEMENTATION
#include "../third_party/stb/stb_image.h"
#define STBI_WRITE_NO_STDIO
#define STB_IMAGE_WRITE_STATIC
#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "../third_party/stb/stb_image_write.h"
#if defined(__clang__)
#pragma clang diagnostic pop
#elif defined(__GNUC__)
#pragma GCC diagnostic pop
/* GCC checks "declared static but never defined" at the end of the file, outside the push/pop: this file is only
   the codec, so that warning stays off to its end. */
#pragma GCC diagnostic ignored "-Wunused-function"
#endif

int jpeg_codec_info(const void *jpeg, uint32_t size, int *width, int *height, int *components) {
    if (!jpeg || !size || size > 0x7fffffff) return 0;
    return stbi_info_from_memory((const stbi_uc *)jpeg, (int)size, width, height, components);
}

uint8_t *jpeg_codec_decode_rgba(const void *jpeg, uint32_t size, int *width, int *height, int *components) {
    if (!jpeg || !size || size > 0x7fffffff) return NULL;
    return stbi_load_from_memory((const stbi_uc *)jpeg, (int)size, width, height, components, 4);
}

void jpeg_codec_free(uint8_t *pixels) { stbi_image_free(pixels); }

const char *jpeg_codec_error(void) { return stbi_failure_reason(); }

typedef struct { uint8_t *out; uint32_t capacity, written; int overflow; } Sink;
static void sink_write(void *context, void *data, int size) {
    Sink *s = context;
    if (size <= 0 || s->overflow) return;
    if ((uint64_t)s->written + (uint64_t)size > s->capacity) { s->overflow = 1; return; }
    memcpy(s->out + s->written, data, (size_t)size);
    s->written += (uint32_t)size;
}

int jpeg_codec_encode(const uint8_t *pixels, int width, int height, int components, int quality,
                      uint8_t *out, uint32_t capacity, uint32_t *written) {
    if (!pixels || !out || width <= 0 || height <= 0 || (components != 1 && components != 3)) return 0;
    /* A smaller file when it doesn't fit the game's buffer: the quality asked for, then lower. */
    for (int q = quality; q >= 10; q = q > 40 ? q - 15 : q - 10) {
        Sink s = {out, capacity, 0, 0};
        if (stbi_write_jpg_to_func(sink_write, &s, width, height, components, pixels, q) && !s.overflow) {
            *written = s.written;
            return 1;
        }
    }
    return 0;
}
