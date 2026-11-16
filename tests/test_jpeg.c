/* lbppc: the JPEG codec behind sceJpegEnc/sceJpegDec (runtime_jpeg_codec.c): a picture encoded and decoded
 * again comes back close (JPEG is lossy), a buffer too small for it is refused, and data that isn't a JPEG
 * is refused without reading past it. */
#include "runtime_jpeg_codec.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures;
#define CHECK(c, what) do { if (!(c)) { printf("FAIL %s\n", what); ++failures; } else printf("ok   %s\n", what); } while (0)

int main(void) {
    enum { W = 160, H = 90 };
    static uint8_t rgb[W * H * 3], grey[W * H], out[200000];
    for (int y = 0; y < H; ++y)
        for (int x = 0; x < W; ++x) {
            uint8_t *p = rgb + (y * W + x) * 3;
            p[0] = (uint8_t)(x * 255 / W); p[1] = (uint8_t)(y * 255 / H); p[2] = (uint8_t)(x < W / 2 ? 200 : 40);
            grey[y * W + x] = (uint8_t)((x + y) * 255 / (W + H));
        }
    uint32_t written = 0;
    CHECK(jpeg_codec_encode(rgb, W, H, 3, 90, out, sizeof out, &written) && written > 100, "colour picture encodes");
    CHECK(out[0] == 0xFF && out[1] == 0xD8, "it starts as a JPEG");
    int w = 0, h = 0, c = 0;
    CHECK(jpeg_codec_info(out, written, &w, &h, &c) && w == W && h == H && c == 3, "its size and colour read back");
    uint8_t *back = jpeg_codec_decode_rgba(out, written, &w, &h, &c);
    CHECK(back != NULL, "it decodes");
    if (back) {
        long total = 0;
        for (int i = 0; i < W * H; ++i)
            for (int k = 0; k < 3; ++k) total += labs((long)back[i * 4 + k] - rgb[i * 3 + k]);
        const double mean = (double)total / (W * H * 3);
        printf("     mean difference %.2f (of 255)\n", mean);
        CHECK(mean < 4.0, "the decoded picture matches the original closely");
        CHECK(back[3] == 255, "decoded alpha is opaque");
        jpeg_codec_free(back);
    }
    /* stb_image_write stores a grey picture as a colour JPEG with equal channels: it must look the same. */
    CHECK(jpeg_codec_encode(grey, W, H, 1, 90, out, sizeof out, &written), "grey picture encodes");
    uint8_t *g = jpeg_codec_decode_rgba(out, written, &w, &h, &c);
    if (g) {
        long total = 0;
        for (int i = 0; i < W * H; ++i) total += labs((long)g[i * 4] - grey[i]) + labs((long)g[i * 4 + 1] - grey[i]);
        printf("     grey mean difference %.2f\n", (double)total / (W * H * 2));
        CHECK((double)total / (W * H * 2) < 4.0, "the grey picture decodes as the same greys");
        jpeg_codec_free(g);
    } else CHECK(0, "the grey picture decodes");
    uint8_t small[300];
    CHECK(!jpeg_codec_encode(rgb, W, H, 3, 90, small, sizeof small, &written), "a buffer too small is refused");
    uint8_t mid[2500];
    const int fits = jpeg_codec_encode(rgb, W, H, 3, 95, mid, sizeof mid, &written);
    CHECK(fits && written <= sizeof mid, "a tight buffer gets a lower-quality picture that fits");
    static const uint8_t junk[64] = {0xFF, 0xD8, 0xFF, 0xC0, 0x00, 0x11};
    CHECK(jpeg_codec_decode_rgba(junk, sizeof junk, &w, &h, &c) == NULL, "a damaged JPEG is refused");
    CHECK(jpeg_codec_decode_rgba("not a picture", 13, &w, &h, &c) == NULL, "data that isn't a JPEG is refused");
    printf(failures ? "%d FAILED\n" : "JPEG OK\n", failures);
    return failures != 0;
}
