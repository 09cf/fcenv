/* Drop-in equivalent of fcsim's run_single_design_xml, built on fccore.
 *
 * usage: fc_run [max_ticks] [--trace] [--fcsim] < design.xml
 * prints: solve tick (-1 if unsolved), then final tick
 *
 * --trace writes every design block's state to stderr before each step, in
 * the same format as fcsim's CLI build, so the two can be diffed directly.
 * --fcsim reproduces fcsim exactly (its win check and 5-joint wheels)
 * instead of following the original game.
 */
#include <fccore.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void trace(fc_sim *sim, fc_block_info *info, fc_body_state *st) {
  int32_t n = fc_sim_design_block_count(sim);
  fc_sim_get_state(sim, st, n);
  fprintf(stderr, "Tick %lld\n", (long long)fc_sim_tick(sim));
  for (int32_t i = 0; i < n; i++)
    fprintf(stderr,
            "- ID = %d, Type = %d, Pos = (%.17g, %.17g), Vel = (%.17g, "
            "%.17g), Ang = %.17g, AngVel = %.17g\n",
            info[i].xml_id, info[i].type_id, st[i].x, st[i].y, st[i].vx,
            st[i].vy, st[i].angle, st[i].omega);
}

int main(int argc, char **argv) {
  int64_t max_ticks = 1000;
  int do_trace = 0;
  uint32_t opts = 0;
  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "--trace"))
      do_trace = 1;
    else if (!strcmp(argv[i], "--fcsim"))
      opts = FC_OPT_FCSIM;
    else
      max_ticks = atoll(argv[i]);
  }

  size_t cap = 1 << 16, len = 0, n;
  char *buf = malloc(cap);
  while ((n = fread(buf + len, 1, cap - len, stdin)) > 0) {
    len += n;
    if (len == cap)
      buf = realloc(buf, cap *= 2);
  }

  fc_sim *sim = fc_sim_new_opts(buf, (int32_t)len, opts);
  free(buf);
  if (!sim) {
    fprintf(stderr, "failed to parse XML\n");
    return 1;
  }

  if (do_trace) {
    int32_t nb = fc_sim_block_count(sim);
    fc_block_info *info = malloc((nb + 1) * sizeof(*info));
    fc_body_state *st = malloc((nb + 1) * sizeof(*st));
    fc_sim_get_blocks(sim, info, nb);
    while (fc_sim_tick(sim) != max_ticks && fc_sim_solve_tick(sim) < 0) {
      trace(sim, info, st);
      fc_sim_step(sim, 1);
    }
    free(info);
    free(st);
  } else {
    fc_sim_run(sim, max_ticks);
  }

  printf("%lld\n%lld\n", (long long)fc_sim_solve_tick(sim),
         (long long)fc_sim_tick(sim));
  fc_sim_free(sim);
  return 0;
}
