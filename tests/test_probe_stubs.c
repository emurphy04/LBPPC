// SPDX-License-Identifier: GPL-2.0-or-later
// Tests that link the real runtime (Windows) but not probe.c: the two probe.c services the runtime calls (weak: a
// test may define its own).
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
__attribute__((weak)) void runtime_restart(void) { abort(); }
__attribute__((weak)) int runtime_sfo_value(const char *path, const char *key, char *text, size_t text_size, uint32_t *number) {
    (void)path; (void)key; (void)number;
    if (text && text_size) text[0] = 0;
    return 0; // no param.sfo in tests
}
