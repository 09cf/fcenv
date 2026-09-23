#include <fccore.h>

#include <box2d/b2Body.h>
#include <box2d/b2Contact.h>
#include <box2d/b2ContactManager.h>
#include <box2d/b2Joint.h>
#include <box2d/b2World.h>
#include <fpmath/fpmath.h>
#include <math.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#include "graph.h"
#include "xml.h"

extern char *export_design(struct design *design, char *user, char *name,
                           char *desc);

struct fc_sim {
  struct design *design;
  b2World *world;
  struct block **blocks; /* design blocks, then level blocks */
  int32_t n_blocks;
  int32_t n_design_blocks;
  int64_t tick;
  int64_t solve_tick;
  int32_t win_check;
};

/* ------------------------------------------------------------------------
 * Area containment and fcsim's win check (FC_WIN_FCSIM; also used for the
 * build-area legality check).
 * Copied from fcsim src/arena.cpp (get_rect_bb, get_circ_bb, get_block_bb,
 * block_inside_area, goal_blocks_inside_goal_area). Types and operations are
 * kept identical, including float intermediates, since the results decide
 * the solve tick.
 * ---------------------------------------------------------------------- */

static void get_rect_bb(struct shell *shell, struct area *area) {
  // replicate truncation weirdness
  float angle_degrees = shell->angle * 57.295779513082320876763;
  if (fabs(angle_degrees) >= 32768) {
    angle_degrees = -32768;
  }
  float angle_radians = angle_degrees * 0.017453292519943295769245;

  float sina = fp_sin(angle_radians);
  float cosa = fp_cos(angle_radians);
  float wc = shell->rect.w * cosa;
  float ws = shell->rect.w * sina;
  float hc = shell->rect.h * cosa;
  float hs = shell->rect.h * sina;

  area->x = shell->x;
  area->y = shell->y;
  area->w = fabs(wc) + fabs(hs);
  area->h = fabs(ws) + fabs(hc);
  area->expand = 0;
}

static void get_circ_bb(struct shell *shell, struct area *area) {
  area->x = shell->x;
  area->y = shell->y;
  area->w = shell->circ.radius * 2;
  area->h = shell->circ.radius * 2;
  area->expand = 0;
}

static void get_block_bb(struct block *block, struct area *area) {
  struct shell shell;

  get_shell(&shell, &block->shape);
  if (block->body) {
    shell.x = block->body->m_position.x;
    shell.y = block->body->m_position.y;
    shell.angle = block->body->m_rotation;
  }

  if (shell.type == SHELL_CIRC)
    get_circ_bb(&shell, area);
  else
    get_rect_bb(&shell, area);
}

static int block_inside_area(struct block *block, struct area *area) {
  struct area bb;

  get_block_bb(block, &bb);

  const double modified_w = area->w + area->expand;
  const double modified_h = area->h + area->expand;

  return bb.x - bb.w / 2 >= area->x - modified_w / 2 &&
         bb.x + bb.w / 2 <= area->x + modified_w / 2 &&
         bb.y - bb.h / 2 >= area->y - modified_h / 2 &&
         bb.y + bb.h / 2 <= area->y + modified_h / 2;
}

static bool goal_blocks_inside_goal_area(struct design *design) {
  struct block *block;
  bool any = false;

  for (block = design->design_blocks.head; block; block = block->next) {
    if (block->goal) {
      any = true;
      if (!block_inside_area(block, &design->goal_area))
        return false;
    }
  }

  return any;
}

/* ------------------------------------------------------------------------
 * Win check as the original game does it: Player.hasWon() in the FFDec
 * decompile (decomp/scripts/contraption/Player.as), which works on Flash
 * display-list values rather than physics values:
 *
 * - a block's x/y are DisplayObject.x/y, set from the body each tick
 *   (Block.updateFromPhysicsBody) and stored as integer twips (1/20 px,
 *   truncated toward zero);
 * - the goal area's centre is likewise its DisplayObject.x/y
 *   (StartZone.getPosition), while its width/height are plain Numbers;
 * - goal circles (Wheel) use x +- radius;
 * - goal rects use DisplayObject.width/height minus 4. That is the axis-
 *   aligned bounds of the block's foreground graphic after rotation. The
 *   foreground holds the fill and radius-3 anchor circles with a 2px stroke
 *   at the centre and corners (JointedDynamicRectangle.drawBlock,
 *   Block.drawTypedAnchor), so its local bounds are +-(w/2 + 4) x +-(h/2 + 4).
 *   The rotated bounds are the extent of the four transformed corners, each
 *   truncated to twips.
 *
 * Twips truncation, the 4px padding and the rotation clamp were checked
 * against ftlib's dataset of real FC results (tests/wincheck_fit.py).
 * ---------------------------------------------------------------------- */

static double flash_twips(double v) {
  double t = v * 20.0;
  /* out of int32 range (or NaN): Flash stores 0x80000000 twips */
  if (!(t > -2147483648.0 && t < 2147483648.0))
    return -2147483648.0 / 20.0;
  return trunc(t) / 20.0;
}

static bool flash_block_inside(struct block *block, double x0, double x1,
                               double y0, double y1) {
  struct shell shell;
  b2Body *body = block->body;
  double x = flash_twips(body->m_position.x);
  double y = flash_twips(body->m_position.y);
  double ex, ey;

  get_shell(&shell, &block->shape);
  if (shell.type == SHELL_CIRC) {
    ex = ey = shell.circ.radius * 2 / 2;
  } else {
    /* Block.updateFromPhysicsBody: rotation = m_rotation * (180 / pi) */
    double deg = body->m_rotation * (180 / 3.141592653589793);
    double rad, c, s, a, b;
    double xmin = INFINITY, xmax = -INFINITY, ymin = INFINITY, ymax = -INFINITY;
    int sx, sy;

    /* matches FC on the "huge rotation" test levels */
    if (fabs(deg) >= 32768)
      deg = -32768;
    /* DisplayObject.rotation keeps the angle in [-180, 180) */
    deg = fmod(deg + 180, 360);
    if (deg < 0)
      deg += 360;
    deg -= 180;
    rad = deg * (3.141592653589793 / 180);
    c = fp_cos(rad);
    s = fp_sin(rad);
    a = shell.rect.w / 2 + 4;
    b = shell.rect.h / 2 + 4;
    for (sx = -1; sx <= 1; sx += 2) {
      for (sy = -1; sy <= 1; sy += 2) {
        double px = flash_twips(c * sx * a - s * sy * b);
        double py = flash_twips(s * sx * a + c * sy * b);
        xmin = fmin(xmin, px);
        xmax = fmax(xmax, px);
        ymin = fmin(ymin, py);
        ymax = fmax(ymax, py);
      }
    }
    ex = ((xmax - xmin) - 4) / 2;
    ey = ((ymax - ymin) - 4) / 2;
  }

  return !(x - ex < x0 || x + ex > x1 || y - ey < y0 || y + ey > y1);
}

static bool flash_goal_blocks_inside_goal_area(struct design *design) {
  struct area *g = &design->goal_area;
  double gx = flash_twips(g->x), gy = flash_twips(g->y);
  double x0 = gx - g->w / 2, x1 = gx + g->w / 2;
  double y0 = gy - g->h / 2, y1 = gy + g->h / 2;
  struct block *block;
  bool any = false;

  for (block = design->design_blocks.head; block; block = block->next) {
    if (block->goal) {
      any = true;
      if (!flash_block_inside(block, x0, x1, y0, y1))
        return false;
    }
  }

  return any;
}

static bool goal_reached(const fc_sim *sim) {
  if (sim->win_check == FC_WIN_FCSIM)
    return goal_blocks_inside_goal_area(sim->design);
  return flash_goal_blocks_inside_goal_area(sim->design);
}

/* ------------------------------------------------------------------------ */

static int32_t count_blocks(struct block_list *list) {
  int32_t n = 0;
  struct block *b;
  for (b = list->head; b; b = b->next)
    n++;
  return n;
}

static int32_t block_index(const fc_sim *sim, const struct block *block) {
  int32_t i;
  for (i = 0; i < sim->n_blocks; i++)
    if (sim->blocks[i] == block)
      return i;
  return -1;
}

static void body_state(const b2Body *body, fc_body_state *out);

static struct block *body_block(b2Body *body) {
  return (struct block *)body->m_shapeList->m_userData;
}

static struct design *parse_design(char *buf, int32_t len, bool fc_rules) {
  struct xml_level level;
  struct design *design;

  memset(&level, 0, sizeof(level));
  if (xml_parse(buf, len, &level) != 0)
    return NULL;
  design = calloc(1, sizeof(*design));
  design->fc_import_rules = fc_rules;
  convert_xml(&level, design);
  xml_free(&level);
  return design;
}

/* copy of xml with an <?xml ...?> declaration, which xml_parse requires and
 * export_design (and so fcsim's saveDesign output) omits */
static char *with_xml_decl(const char *xml, int32_t *len) {
  static const char decl[] = "<?xml version=\"1.0\"?>";
  int32_t skip = 0, pre;
  char *buf;
  while (skip < *len && (xml[skip] == ' ' || xml[skip] == '\t' ||
                         xml[skip] == '\n' || xml[skip] == '\r'))
    skip++;
  pre = (*len - skip >= 2 && xml[skip] == '<' && xml[skip + 1] == '?')
            ? 0
            : (int32_t)sizeof(decl) - 1;
  buf = malloc(pre + *len + 1);
  memcpy(buf, decl, pre);
  memcpy(buf + pre, xml, *len);
  *len += pre;
  buf[*len] = 0;
  return buf;
}

fc_sim *fc_sim_new(const char *xml, int32_t len) {
  return fc_sim_new_opts(xml, len, 0);
}

fc_sim *fc_sim_new_opts(const char *xml, int32_t len, uint32_t opts) {
  fc_sim *sim;
  struct block *b;
  char *buf;
  int32_t i;

  buf = with_xml_decl(xml, &len);
  struct design *design =
      parse_design(buf, len, !(opts & FC_OPT_FCSIM_IMPORT));
  free(buf);
  if (!design)
    return NULL;

  sim = calloc(1, sizeof(*sim));
  sim->design = design;
  sim->win_check = (opts & FC_OPT_FCSIM_WIN_CHECK) ? FC_WIN_FCSIM : FC_WIN_FLASH;

  sim->n_design_blocks = count_blocks(&sim->design->design_blocks);
  sim->n_blocks =
      sim->n_design_blocks + count_blocks(&sim->design->level_blocks);
  sim->blocks = calloc(sim->n_blocks > 0 ? sim->n_blocks : 1,
                       sizeof(*sim->blocks));
  i = 0;
  for (b = sim->design->design_blocks.head; b; b = b->next)
    sim->blocks[i++] = b;
  for (b = sim->design->level_blocks.head; b; b = b->next)
    sim->blocks[i++] = b;

  sim->world = gen_world(sim->design);
  sim->tick = 0;
  sim->solve_tick = -1;
  return sim;
}

void fc_sim_free(fc_sim *sim) {
  if (!sim)
    return;
  free_world(sim->world, sim->design);
  free_design(sim->design);
  free(sim->blocks);
  free(sim);
}

void fc_sim_reset(fc_sim *sim) {
  free_world(sim->world, sim->design);
  sim->world = gen_world(sim->design);
  sim->tick = 0;
  sim->solve_tick = -1;
}

int32_t fc_sim_block_count(const fc_sim *sim) { return sim->n_blocks; }

int32_t fc_sim_design_block_count(const fc_sim *sim) {
  return sim->n_design_blocks;
}

int32_t fc_sim_get_blocks(const fc_sim *sim, fc_block_info *out, int32_t cap) {
  int32_t i;
  for (i = 0; i < sim->n_blocks && i < cap; i++) {
    struct block *b = sim->blocks[i];
    struct shell shell;
    fc_block_info *info = &out[i];

    get_shell(&shell, &b->shape);
    memset(info, 0, sizeof(*info));
    info->type_id = b->type_id;
    info->is_level = i >= sim->n_design_blocks;
    info->is_goal = b->goal;
    info->xml_id = b->uid;
    if (shell.type == SHELL_CIRC) {
      info->shape = FC_SHAPE_CIRCLE;
      info->radius = shell.circ.radius;
    } else {
      info->shape = FC_SHAPE_RECT;
      info->w = shell.rect.w;
      info->h = shell.rect.h;
    }
  }
  return sim->n_blocks;
}

int32_t fc_sim_get_state(const fc_sim *sim, fc_body_state *out, int32_t cap) {
  int32_t i;
  for (i = 0; i < sim->n_blocks && i < cap; i++)
    body_state(sim->blocks[i]->body, &out[i]);
  return sim->n_blocks;
}

int32_t fc_sim_get_joints(const fc_sim *sim, fc_joint_info *out, int32_t cap) {
  int32_t n = 0;
  b2Joint *joint;
  for (joint = b2World_GetJointList(sim->world); joint;
       joint = joint->m_next) {
    if (n < cap) {
      b2Vec2 a = joint->GetAnchor1(joint);
      out[n].block1 = block_index(sim, body_block(joint->m_body1));
      out[n].block2 = block_index(sim, body_block(joint->m_body2));
      out[n].x = a.x;
      out[n].y = a.y;
    }
    n++;
  }
  return n;
}

static struct joint *design_joint_at(const fc_sim *sim, int32_t id) {
  struct joint *j = sim->design->joints.head;
  if (id < 0)
    return NULL;
  while (j && id--)
    j = j->next;
  return j;
}

static int32_t design_joint_id(const fc_sim *sim, const struct joint *joint) {
  int32_t id = 0;
  struct joint *j;
  for (j = sim->design->joints.head; j; j = j->next, id++)
    if (j == joint)
      return id;
  return -1;
}

int32_t fc_sim_design_joint_count(const fc_sim *sim) {
  int32_t n = 0;
  struct joint *j;
  for (j = sim->design->joints.head; j; j = j->next)
    n++;
  return n;
}

int32_t fc_sim_get_design_joint(const fc_sim *sim, int32_t id,
                                fc_design_joint *out, int32_t *att,
                                int32_t cap) {
  struct joint *j = design_joint_at(sim, id);
  struct attach_node *node;
  int32_t n = 0;
  if (!j)
    return -1;
  for (node = j->att.head; node; node = node->next) {
    if (att && n < cap)
      att[n] = block_index(sim, node->block);
    n++;
  }
  out->x = j->x;
  out->y = j->y;
  out->gen = j->gen ? block_index(sim, j->gen) : -1;
  out->n_att = n;
  return n;
}

int32_t fc_sim_get_block_joint_ids(const fc_sim *sim, int32_t block,
                                   int32_t *ids, int32_t cap) {
  struct joint *joints[MAX_BLOCK_JOINTS];
  int32_t n, i;
  if (block < 0 || block >= sim->n_blocks)
    return -1;
  n = get_block_joints(sim->blocks[block], joints);
  for (i = 0; i < n && i < cap; i++)
    ids[i] = design_joint_id(sim, joints[i]);
  return n;
}

static void area_out(const struct area *a, fc_area *out) {
  out->x = a->x;
  out->y = a->y;
  out->w = a->w;
  out->h = a->h;
}

void fc_sim_get_build_area(const fc_sim *sim, fc_area *out) {
  area_out(&sim->design->build_area, out);
}

void fc_sim_get_goal_area(const fc_sim *sim, fc_area *out) {
  area_out(&sim->design->goal_area, out);
}

/* Same per-tick logic as fcsim's tick_func (src/arena_algorithm.cpp). */
static void tick_once(fc_sim *sim) {
  step(sim->world);
  sim->tick++;
  if (sim->solve_tick < 0 && goal_reached(sim))
    sim->solve_tick = sim->tick;
}

void fc_sim_step(fc_sim *sim, int64_t n) {
  int64_t i;
  for (i = 0; i < n; i++)
    tick_once(sim);
}

int64_t fc_sim_run(fc_sim *sim, int64_t max_ticks) {
  while (sim->tick != max_ticks && sim->solve_tick < 0)
    tick_once(sim);
  return sim->solve_tick;
}

static void body_state(const b2Body *body, fc_body_state *out) {
  out->x = body->m_position.x;
  out->y = body->m_position.y;
  out->angle = body->m_rotation;
  out->vx = body->m_linearVelocity.x;
  out->vy = body->m_linearVelocity.y;
  out->omega = body->m_angularVelocity;
}

int64_t fc_sim_record(fc_sim *sim, int64_t n, int64_t stride,
                      const int32_t *idx, int32_t n_idx, fc_body_state *out) {
  int64_t i, frames = 0;
  int32_t j;
  if (stride < 1)
    stride = 1;
  if (!idx)
    n_idx = sim->n_blocks;
  for (i = 1; i <= n; i++) {
    tick_once(sim);
    if (i % stride)
      continue;
    for (j = 0; j < n_idx; j++)
      body_state(sim->blocks[idx ? idx[j] : j]->body, out++);
    frames++;
  }
  return frames;
}

int64_t fc_sim_tick(const fc_sim *sim) { return sim->tick; }

int64_t fc_sim_solve_tick(const fc_sim *sim) { return sim->solve_tick; }

int32_t fc_sim_goal_reached(const fc_sim *sim) { return goal_reached(sim); }

void fc_sim_set_win_check(fc_sim *sim, int32_t mode) { sim->win_check = mode; }

/* Based on fcsim's mark_overlaps (src/arena.cpp), applied to every design
 * block rather than only the ones being dragged. */
int32_t fc_sim_check_legal(const fc_sim *sim, int32_t *flags) {
  struct design *design = sim->design;
  b2Body **saved;
  b2World *world;
  b2Contact *contact;
  int32_t *f;
  int32_t i;
  int32_t legal = 1;

  /* gen_world overwrites block->body, so stash the live bodies */
  saved = malloc((sim->n_blocks + 1) * sizeof(*saved));
  for (i = 0; i < sim->n_blocks; i++)
    saved[i] = sim->blocks[i]->body;

  f = calloc(sim->n_design_blocks + 1, sizeof(*f));
  world = gen_world(design);

  b2ContactManager_CleanContactList(&world->m_contactManager);
  b2World_CleanBodyList(world);
  b2ContactManager_Collide(&world->m_contactManager);

  for (i = 0; i < sim->n_design_blocks; i++)
    if (!block_inside_area(sim->blocks[i], &design->build_area))
      f[i] |= FC_ILLEGAL_OUTSIDE_BUILD_AREA;

  for (contact = world->m_contactList; contact; contact = contact->m_next) {
    if (contact->m_manifoldCount > 0) {
      int32_t i1 = block_index(sim, contact->m_shape1->m_userData);
      int32_t i2 = block_index(sim, contact->m_shape2->m_userData);
      if (i1 >= 0 && i1 < sim->n_design_blocks)
        f[i1] |= FC_ILLEGAL_OVERLAP;
      if (i2 >= 0 && i2 < sim->n_design_blocks)
        f[i2] |= FC_ILLEGAL_OVERLAP;
    }
  }

  free_world(world, design);
  for (i = 0; i < sim->n_blocks; i++)
    sim->blocks[i]->body = saved[i];
  free(saved);

  for (i = 0; i < sim->n_design_blocks; i++) {
    if (f[i] && !sim->blocks[i]->goal)
      legal = 0;
    if (flags)
      flags[i] = f[i];
  }
  free(f);
  return legal;
}

char *fc_sim_export_xml(fc_sim *sim, const char *user, const char *name,
                        const char *desc) {
  return export_design(sim->design, (char *)(user ? user : ""),
                       (char *)(name ? name : ""), (char *)(desc ? desc : ""));
}

void fc_free(void *p) { free(p); }
