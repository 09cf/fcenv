/*
 * fccore: headless Fantastic Contraption physics, extracted from fcsim.
 *
 * The XML design is the single source of truth. A simulation is always built
 * by parsing XML, never by editing a live joint graph, because body and joint
 * creation order affects the physics and only the XML path matches what the
 * original game simulates for a saved design.
 *
 * Coordinates: +x right, +y down, +angle clockwise. Units: mm, ticks (30/s).
 */
#ifndef FCCORE_H
#define FCCORE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FCCORE_API_VERSION 1

/* Only the fc_* API is exported from libfccore.so. The vendored fcsim code
 * has generic global names (e.g. step(), which glibc also defines), so
 * everything else is built with -fvisibility=hidden. */
#define FCCORE_API __attribute__((visibility("default")))

/* Block type IDs (same values as fcsim's FCSIM_* constants). */
enum fc_block_type {
  FC_STATIC_RECT = 0,
  FC_STATIC_CIRCLE = 1,
  FC_DYNAMIC_RECT = 2,
  FC_DYNAMIC_CIRCLE = 3,
  FC_GOAL_RECT = 4,
  FC_GOAL_CIRCLE = 5,
  FC_CW_GOAL_CIRCLE = 6,
  FC_CCW_GOAL_CIRCLE = 7,
  FC_WHEEL = 8,
  FC_CW_WHEEL = 9,
  FC_CCW_WHEEL = 10,
  FC_WATER_ROD = 11,
  FC_SOLID_ROD = 12,
};

enum fc_shape_kind {
  FC_SHAPE_RECT = 0,   /* w, h used */
  FC_SHAPE_CIRCLE = 1, /* radius used */
};

/* Static description of a block. Blocks are indexed with all design blocks
 * first (in XML <playerBlocks> order), then all level blocks. */
typedef struct fc_block_info {
  int32_t type_id;   /* enum fc_block_type */
  int32_t shape;     /* enum fc_shape_kind */
  int32_t is_level;  /* 1 for level (environment) blocks */
  int32_t is_goal;   /* 1 for goal pieces */
  int32_t xml_id;    /* id attribute from the XML, -1 if none */
  double w, h;       /* rectangle extents (full width/height) */
  double radius;     /* circle radius */
} fc_block_info;

/* Dynamic state of a block's body. */
typedef struct fc_body_state {
  double x, y, angle;
  double vx, vy, omega;
} fc_body_state;

/* A live revolute joint. Joints break (disappear) when their anchors drift
 * more than 50mm apart. */
typedef struct fc_joint_info {
  int32_t block1, block2; /* block indices */
  double x, y;            /* anchor on block1 */
} fc_joint_info;

typedef struct fc_area {
  double x, y, w, h;
} fc_area;

/* Per-block legality flags, for design (non-level) blocks. */
enum {
  FC_ILLEGAL_OVERLAP = 1 << 0,
  FC_ILLEGAL_OUTSIDE_BUILD_AREA = 1 << 1,
};

/* Win check variants (see fc_sim_set_win_check). */
enum fc_win_check {
  /* Original game (Player.hasWon on Flash display-list values): positions
   * truncated to twips, goal rects measured by their rendered bounds. */
  FC_WIN_FLASH = 0,
  /* fcsim/ftlib: exact positions, rotated-rectangle bounding box. */
  FC_WIN_FCSIM = 1,
};

typedef struct fc_sim fc_sim;

/* Options for fc_sim_new_opts. The defaults follow the original game. */
enum {
  /* fcsim's XML import. By default the original game's is used, which
   * differs in two ways: wheels with radius > 20 get 4 more joints fixed
   * 20mm from the centre, axis-aligned (Wheel.as); and a wheel never snaps
   * to a joint that already has a wheel (BlockCollection.createWheel). */
  FC_OPT_FCSIM_IMPORT = 1 << 0,
  /* Start with fcsim's win check (see fc_sim_set_win_check). */
  FC_OPT_FCSIM_WIN_CHECK = 1 << 1,
  /* Everything fcsim-compatible: bit-exact with fcsim. */
  FC_OPT_FCSIM = FC_OPT_FCSIM_IMPORT | FC_OPT_FCSIM_WIN_CHECK,
};

/* Parse XML (retrieveLevel or saveDesign format) and build a world at tick 0.
 * The XML is simulated exactly as parsed. (The original game re-exports and
 * re-parses the design each time Go is pressed; like fcsim, fccore does not
 * model that. See README "Known gaps".)
 * Returns NULL on parse failure. fc_sim_new(xml, len) is
 * fc_sim_new_opts(xml, len, 0). */
FCCORE_API fc_sim *fc_sim_new(const char *xml, int32_t len);
FCCORE_API fc_sim *fc_sim_new_opts(const char *xml, int32_t len,
                                   uint32_t opts);
FCCORE_API void fc_sim_free(fc_sim *sim);

/* Rebuild the world from the parsed design and reset to tick 0. */
FCCORE_API void fc_sim_reset(fc_sim *sim);

/* Select the win check (default FC_WIN_FLASH). Takes effect from the next
 * step; does not reset the simulation. */
FCCORE_API void fc_sim_set_win_check(fc_sim *sim, int32_t mode);

FCCORE_API int32_t fc_sim_block_count(const fc_sim *sim);
FCCORE_API int32_t fc_sim_design_block_count(const fc_sim *sim);
/* Copy up to `cap` entries; returns the number of blocks. */
FCCORE_API int32_t fc_sim_get_blocks(const fc_sim *sim, fc_block_info *out, int32_t cap);
FCCORE_API int32_t fc_sim_get_state(const fc_sim *sim, fc_body_state *out, int32_t cap);
FCCORE_API int32_t fc_sim_get_joints(const fc_sim *sim, fc_joint_info *out, int32_t cap);

FCCORE_API void fc_sim_get_build_area(const fc_sim *sim, fc_area *out);
FCCORE_API void fc_sim_get_goal_area(const fc_sim *sim, fc_area *out);

/* Advance `n` ticks. The solve tick is recorded the first time all goal
 * pieces are inside the goal area after a step. */
FCCORE_API void fc_sim_step(fc_sim *sim, int64_t n);
/* Step until solved or tick == max_ticks. Returns the solve tick, or -1. */
FCCORE_API int64_t fc_sim_run(fc_sim *sim, int64_t max_ticks);

/* Advance `n` ticks, writing the state of the listed blocks after every
 * `stride`-th step: out[k * n_idx + j] is block idx[j] after step
 * (k + 1) * stride. `out` needs room for (n / stride) * n_idx entries.
 * idx == NULL means all blocks (n_idx is then ignored). Returns the number
 * of frames written. */
FCCORE_API int64_t fc_sim_record(fc_sim *sim, int64_t n, int64_t stride,
                                 const int32_t *idx, int32_t n_idx,
                                 fc_body_state *out);

FCCORE_API int64_t fc_sim_tick(const fc_sim *sim);
FCCORE_API int64_t fc_sim_solve_tick(const fc_sim *sim); /* -1 if not solved yet */
/* 1 if all goal pieces are in the goal area right now (0 if there are none). */
FCCORE_API int32_t fc_sim_goal_reached(const fc_sim *sim);

/* Check the initial (tick 0) design against the editor rules: every design
 * block's flags (FC_ILLEGAL_*) go to `flags` (design_block_count entries, may
 * be NULL). Returns 1 if no non-goal design block has any flag set.
 * Uses a throwaway world, so it does not disturb the simulation. */
FCCORE_API int32_t fc_sim_check_legal(const fc_sim *sim, int32_t *flags);

/* Serialise the parsed design (tick-0 state) as saveDesign XML. The result is
 * malloc'd; release it with fc_free. */
FCCORE_API char *fc_sim_export_xml(fc_sim *sim, const char *user, const char *name,
                        const char *desc);
FCCORE_API void fc_free(void *p);

#ifdef __cplusplus
}
#endif

#endif
