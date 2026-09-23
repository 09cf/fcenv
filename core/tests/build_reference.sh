#!/usr/bin/env bash
# Build fcsim's own run_single_design_xml (the reference implementation) from
# the upstream checkout, without scons. Output: build/reference/run_single_design_xml
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
fcsim="${FCSIM_DIR:-$here/../fcsim}"
out="$here/build/reference"
mkdir -p "$out"

srcs=$(python3 - "$fcsim/SConstruct" <<'PY'
import re, sys
t = open(sys.argv[1]).read()
m = re.search(r"common_sources = \[(.*?)\]", t, re.S)
print(" ".join(re.findall(r'"([^"]+)"', m.group(1))))
PY
)

objs=()
for s in $srcs src/run_single_design_xml.cpp; do
  o="$out/$(echo "$s" | tr / _).o"
  case "$s" in
    *.c) gcc -O3 -flto -DCLI -I"$fcsim/include" -c "$fcsim/$s" -o "$o" ;;
    *) g++ -O3 -flto -DCLI -I"$fcsim/include" -c "$fcsim/$s" -o "$o" -w ;;
  esac
  objs+=("$o")
done
g++ -O3 -flto "${objs[@]}" -o "$out/run_single_design_xml" -lpthread
echo "built $out/run_single_design_xml (fcsim $(git -C "$fcsim" rev-parse --short HEAD))"
