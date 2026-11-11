
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define MAP_SIZE 65536u
static uint8_t cov_map[MAP_SIZE];
static uint32_t num_guards;

void __sanitizer_cov_trace_pc_guard_init(uint32_t *start, uint32_t *stop) {
    if (start == stop || *start) return;
    for (uint32_t *x = start; x < stop; x++)
        *x = ++num_guards;
}

void __sanitizer_cov_trace_pc_guard(uint32_t *guard) {
    if (!*guard) return;
    cov_map[*guard % MAP_SIZE] = 1;
}

__attribute__((destructor))
static void cov_dump(void) {
    const char *out = getenv("COV_OUT");
    if (!out || !*out) return;
    FILE *f = fopen(out, "wb");
    if (!f) return;
    fwrite(cov_map, 1, MAP_SIZE, f);
    fclose(f);
}
