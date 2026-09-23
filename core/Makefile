# fccore: headless Fantastic Contraption physics extracted from fcsim.
#
# Determinism notes:
# - never use -ffast-math
# - -ffp-contract=off stops the compiler fusing a*b+c into FMA instructions,
#   which GCC does by default on targets that have them (aarch64, -march=native)
# - trig goes through src/fpmath, never libm

CC ?= gcc
CXX ?= g++
BUILD ?= build

FPFLAGS = -ffp-contract=off -fno-fast-math
# -MMD -MP: track header dependencies (see -include below)
CPPFLAGS += -Iinclude -Isrc -MMD -MP
CFLAGS ?= -O2
CXXFLAGS ?= -O2
# vendored fcsim code is compiled without -Wall to keep upstream diffs minimal
CFLAGS += -fPIC -fvisibility=hidden $(FPFLAGS)
CXXFLAGS += -fPIC -fvisibility=hidden $(FPFLAGS)

C_SRCS = $(wildcard src/*.c src/box2d/*.c src/fpmath/*.c)
CXX_SRCS = $(wildcard src/*.cpp src/box2d/*.cpp)
OBJS = $(C_SRCS:%.c=$(BUILD)/%.o) $(CXX_SRCS:%.cpp=$(BUILD)/%.o)

# FPATAN=1: atan2 via the x87 fpatan instruction (x86 only), like fcsim's
# fcsim-fpatan build, instead of the portable software atan2 (794-stable)
ifeq ($(FPATAN),1)
CPPFLAGS += -DUSE_FPATAN
OBJS += $(BUILD)/src/fpmath/fpatan.o
endif

all: $(BUILD)/libfccore.a $(BUILD)/libfccore.so $(BUILD)/fc_run

$(BUILD)/%.o: %.c
	@mkdir -p $(dir $@)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

$(BUILD)/%.o: %.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c $< -o $@

$(BUILD)/%.o: %.s
	@mkdir -p $(dir $@)
	$(CC) -c $< -o $@

$(BUILD)/libfccore.a: $(OBJS)
	ar rcs $@ $^

$(BUILD)/libfccore.so: $(OBJS)
	$(CXX) -shared -Wl,-Bsymbolic -o $@ $^

$(BUILD)/fc_run: tools/fc_run.c $(BUILD)/libfccore.a
	$(CC) $(CPPFLAGS) $(CFLAGS) $< $(BUILD)/libfccore.a -lstdc++ -lm -o $@

-include $(OBJS:.o=.d)

# fcsim's own run_single_design_xml, used as the bit-exact reference
reference:
	tests/build_reference.sh

fetch:
	python3 tests/fetch_designs.py

test: all
	cd tests && python3 -m unittest -v test_api test_reference

agreement: all
	python3 tests/fc_agreement.py

# keeps build/reference
clean:
	rm -rf $(BUILD)/src $(BUILD)/libfccore.* $(BUILD)/fc_run

.PHONY: all clean reference fetch test agreement
