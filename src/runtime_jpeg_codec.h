/* lbppc: JPEG encode and decode for sceJpegEnc and sceJpegDec (runtime_jpeg_codec.c, stb). */
#ifndef RUNTIME_JPEG_CODEC_H
#define RUNTIME_JPEG_CODEC_H
#include <stdint.h>

/* Width, height and components (1 grey, 3 colour) of a JPEG; 0 when it isn't one stb reads. */
int jpeg_codec_info(const void *jpeg, uint32_t size, int *width, int *height, int *components);
/* The JPEG as RGBA (4 bytes a pixel, rows packed), or NULL (jpeg_codec_error says why). Free with jpeg_codec_free. */
uint8_t *jpeg_codec_decode_rgba(const void *jpeg, uint32_t size, int *width, int *height, int *components);
void jpeg_codec_free(uint8_t *pixels);
const char *jpeg_codec_error(void);
/* Packed pixels (components 1 grey or 3 RGB) as a JPEG into out, at `quality` (1-100) or lower until it fits.
   1 with *written set, 0 when it can't fit. */
int jpeg_codec_encode(const uint8_t *pixels, int width, int height, int components, int quality,
                      uint8_t *out, uint32_t capacity, uint32_t *written);
#endif
