#!/usr/bin/env bash
# Isolated P5 gen1 native chain acceptance, ZERO paid model calls. Root only.
# Run only after independently checking outer ZIP SHA256 and file SHA256SUMS.
set -Eeuo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/bin
cd /

MODE="${1:---preflight}"
if [[ "$MODE" == '--paid' ]]; then
  [[ "${2:-}" == '--authorize-paid-evolution' && $# -eq 5 ]] || {
    echo 'P9_STOP=Paid mode needs explicit authorization, iterations, session seconds' >&2; exit 2; }
  P9_ITERATIONS="$3"; P9_SECONDS="$4"; P9_RESUME="$5"
  [[ "$P9_RESUME" == '-' || "$P9_RESUME" =~ ^HHL_P9_EVOLUTION_[a-z0-9]{8}$ ]] || exit 2
  [[ "$P9_ITERATIONS" =~ ^[0-9]{1,2}$ && "$P9_SECONDS" =~ ^[0-9]{3,4}$ ]] || exit 2
  (( P9_ITERATIONS >= 1 && P9_ITERATIONS <= 12 && P9_SECONDS >= 180 && P9_SECONDS <= 2400 )) || exit 2
elif [[ "$MODE" != '--preflight' || $# -ne 1 ]]; then
  echo 'Usage: run_vm_once.sh --preflight OR --paid --authorize-paid-evolution ITERATIONS SESSION_SECONDS BRANCH_OR_DASH' >&2
  exit 2
fi
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
BASE=/run/hhl-task2
STAGE="$BASE/p1-review-code"
U=/run/systemd/system
CFG="$BASE/hhl-v06-opencode.json"
BASE_CFG=/srv/hhl-task2/agent/opencode.json
SCRATCH=/srv/hhl-task2/agent/scratch
B_SPOOL=/srv/hhl-task2/p1-review-broker-spool
E_SPOOL=/srv/hhl-task2/p1-review-evaluator-spool
R_SOCK="$BASE/p1-review-research.sock"
E_SOCK="$BASE/p1-review-evaluator.sock"
UNITS=(hhl-p1-review-research.socket hhl-p1-review-research.service hhl-p1-review-evaluator.socket hhl-p1-review-evaluator.service)
CREATED=0
STARTED=0
CONFIG=0
STAGED=0
WORK=''

stop() { echo "P5_STOP=$*" >&2; exit 2; }
cleanup() {
  local rc=$? okay=1
  trap - EXIT INT TERM
  set +e
  if (( STARTED )); then
    timeout 45s systemctl stop hhl-p1-review-research.socket hhl-p1-review-evaluator.socket \
      hhl-p1-review-research.service hhl-p1-review-evaluator.service >/dev/null 2>&1 || okay=0
  fi
  if (( CREATED )); then
    if [[ -S "$R_SOCK" || -S "$E_SOCK" ]] || \
       systemctl is-active --quiet hhl-p1-review-research.socket || \
       systemctl is-active --quiet hhl-p1-review-evaluator.socket || \
       systemctl is-active --quiet hhl-p1-review-research.service || \
       systemctl is-active --quiet hhl-p1-review-evaluator.service; then
      echo P5_CLEANUP_STOP=active_socket_or_service
      okay=0
    else
      for unit in "${UNITS[@]}"; do
        if [[ -f "$U/$unit" && -f "$STAGE/$unit" ]] && cmp -s "$U/$unit" "$STAGE/$unit"; then
          rm -f -- "$U/$unit" || okay=0
        elif [[ -e "$U/$unit" || -L "$U/$unit" ]]; then
          echo "P5_CLEANUP_STOP=unit_drift:$unit"
          okay=0
        fi
      done
      (( okay )) && systemctl daemon-reload >/dev/null 2>&1 || okay=0
    fi
  fi
  if (( CONFIG )); then
    [[ -f "$CFG" && ! -L "$CFG" ]] && rm -f -- "$CFG" || okay=0
  fi
  if [[ -n "$WORK" && -d "$WORK" ]]; then rm -rf -- "$WORK"; fi
  leftovers="$(/usr/sbin/runuser -u hhl-runner -- /usr/bin/env -i \
    HOME=/srv/hhl-task2/rootless/home XDG_RUNTIME_DIR=/run/user/995 \
    DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/995/bus PATH=/usr/bin:/bin \
    /usr/bin/podman ps -aq --filter label=hhl.research.rootless=v4 2>/dev/null)" || okay=0
  if [[ -n "$leftovers" ]]; then echo P5_CLEANUP_STOP=orphan_container; okay=0; fi
  if (( STAGED && okay )); then
    [[ -d "$STAGE" && ! -L "$STAGE" && "$(stat -c '%u:%g:%a' "$STAGE")" == '0:0:755' ]] || okay=0
    (( okay )) && rm -rf -- "$STAGE" || true
  fi
  if (( okay )); then echo P5_TEMP_SERVICES_CLEANUP=PASS; else echo P5_TEMP_SERVICES_CLEANUP=CHECK_REQUIRED; rc=3; fi
  if [[ "$MODE" == '--preflight' ]]; then echo P5_MODEL_CALLS=0; else echo P5_MODEL_CALLS=SEE_PRIVATE_GEN1_RESULT; fi
  unset DEEPSEEK_API_KEY || true
  echo P5_HISTORICAL_DB_WRITES=0
  echo P5_COULOMB_WRITES=0
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ $(id -u) == 0 ]] || stop 'root only'
[[ "$SRC" == /run/hhl-task2/* ]] || stop 'extract into root-only /run before execution'
(cd "$SRC" && sha256sum --status -c SHA256SUMS.txt) || stop 'source package hash mismatch'
[[ -d "$BASE" && ! -L "$BASE" && "$(stat -c '%u:%g:%a' "$BASE")" == '0:0:755' ]] || stop 'unsafe socket parent'
[[ -d "$SCRATCH" && ! -L "$SCRATCH" ]] || stop 'scratch path missing'
mountpoint -q "$SCRATCH" || stop 'scratch not mounted'
[[ "$(stat -c '%u:%g:%a' "$SCRATCH")" == '997:988:700' ]] || stop 'scratch mode mismatch'
opts=",$(findmnt -n -o OPTIONS -T "$SCRATCH"),"
for option in nosuid nodev noexec; do [[ "$opts" == *",$option,"* ]] || stop "scratch missing $option"; done
[[ "$(id -u hhl-agent):$(id -g hhl-agent)" == '997:988' ]] || stop 'hhl-agent uid drift'
[[ $(id -u hhl-runner) == 995 && -d /run/user/995 ]] || stop 'runner account not ready'
[[ -S "$BASE/quantum-evaluate.sock" ]] || stop 'C native evaluator missing'
[[ "$(stat -c '%u:%g:%a' "$BASE/quantum-evaluate.sock")" == '0:988:660' ]] || stop 'C native evaluator socket drift'
[[ -x /home/researcher/quantum-agent/.venv/bin/python ]] || stop 'A native OpenEvolve venv missing'
[[ ! -e "$STAGE" && ! -L "$STAGE" && ! -e "$CFG" && ! -L "$CFG" ]] || stop 'review stage or runtime config already exists'
for socket in "$R_SOCK" "$E_SOCK" "$BASE/research-v4.sock"; do
  [[ ! -e "$socket" && ! -L "$socket" ]] || stop "unexpected active socket: $socket"
done
for unit in "${UNITS[@]}"; do
  [[ ! -e "$U/$unit" && ! -L "$U/$unit" && ! -e "/etc/systemd/system/$unit" ]] || stop "unit collision: $unit"
done
/usr/bin/python3 -I -B - <<'PY'
from pathlib import Path
for p in Path('/proc').iterdir():
    if not p.name.isdecimal():continue
    try:
        if p.stat().st_uid != 997:continue
        argv=(p/'cmdline').read_bytes().split(b'\0')
    except (OSError,PermissionError):continue
    if argv and b'opencode' in argv[0].lower() and any(x in (b'run',b'serve') for x in argv[1:]):
        raise SystemExit('P5_STOP=active_agent_session')
PY
leftovers="$(/usr/sbin/runuser -u hhl-runner -- /usr/bin/env -i \
  HOME=/srv/hhl-task2/rootless/home XDG_RUNTIME_DIR=/run/user/995 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/995/bus PATH=/usr/bin:/bin \
  /usr/bin/podman ps -aq --filter label=hhl.research.rootless=v4)" || stop 'podman inventory failure'
[[ -z "$leftovers" ]] || stop 'existing rootless HHL workload'
# C original pinned preflight, not a synthetic replacement.
(cd / && bash /run/hhl-task2/hhl-first-one-session/scripts/hhl_first_one_session.sh --preflight --timeout 900) \
  || stop 'C original runtime preflight failed'

WORK="$(mktemp -d "$BASE/.p5-verified-XXXXXXXX")"
chmod 700 "$WORK"
unzip -q "$SRC/p1_verified_r2.zip" -d "$WORK/p1"
# Original P1 accepted source, copied unmodified to its previous isolated path.
install -d -o root -g root -m 755 "$STAGE"
STAGED=1
for dir in broker eval clients support support/ref tools-overlay tools-overlay/tools; do
  install -d -o root -g root -m 755 "$STAGE/$dir"
done
for dir in broker eval; do
  for f in "$WORK/p1/stage/$dir/"*; do install -o root -g root -m 644 "$f" "$STAGE/$dir/$(basename "$f")"; done
done
for f in "$WORK/p1/client.py" "$WORK/p1/acceptance.py" "$WORK/p1/SHA256SUMS.txt"; do
  install -o root -g root -m 644 "$f" "$STAGE/$(basename "$f")"
done
for unit in "${UNITS[@]}"; do
  install -o root -g root -m 644 "$WORK/p1/$unit" "$STAGE/$unit"
done
(cd "$STAGE" && sha256sum --status -c SHA256SUMS.txt) || stop 'P1 proven stage pin failed'
for part in clients support tools; do
  if [[ "$part" == tools ]]; then
    for f in "$SRC/tools/"*; do install -o root -g root -m 644 "$f" "$STAGE/tools-overlay/tools/$(basename "$f")"; done
  else
    for f in "$SRC/$part/"*; do
      [[ -f "$f" ]] || continue
      install -o root -g root -m 644 "$f" "$STAGE/$part/$(basename "$f")"
    done
  fi
done
for f in "$SRC/support/ref/"*; do install -o root -g root -m 644 "$f" "$STAGE/support/ref/$(basename "$f")"; done
install -o root -g root -m 644 "$SRC/gen1_canary.py" "$STAGE/gen1_canary.py"
install -o root -g root -m 644 "$SRC/gen1_paid.py" "$STAGE/gen1_paid.py"
install -o root -g root -m 644 "$SRC/gen2_paid.py" "$STAGE/gen2_paid.py"
install -o root -g root -m 644 "$SRC/p9_evolution_controller.py" "$STAGE/p9_evolution_controller.py"
install -o root -g root -m 644 "$SRC/p11_research_layer.py" "$STAGE/p11_research_layer.py"
install -o root -g root -m 644 "$SRC/gen2_result_read_policy.py" "$STAGE/gen2_result_read_policy.py"
install -o root -g root -m 644 "$SRC/hhl_gen2_parent_adapter.py" "$STAGE/hhl_gen2_parent_adapter.py"
install -o root -g root -m 644 "$SRC/p6_hhl_nextgen.py" "$STAGE/p6_hhl_nextgen.py"
/usr/bin/node --input-type=module --check < "$STAGE/tools-overlay/tools/research_python.js"
/usr/bin/node --input-type=module --check < "$STAGE/tools-overlay/tools/quantum_evaluate.js"
# P5 dependency bridge ONLY: C's original root-owned tool directory already
# uses this exact installed package tree. P5 tools live outside that directory,
# so Node/Bun cannot resolve @opencode-ai/plugin from the isolated overlay.
# Borrow package resolution via a root-created symlink, NOT a symlinked tool.
# Keep the two P5 source files, their permissions, and all P1 clients unchanged.
C_MODULES=/srv/hhl-task2/agent/.opencode/node_modules
/usr/bin/python3 -I -B - "$C_MODULES" <<'PY_CHECK_C_MODULES'
import os, stat, sys
from pathlib import Path
base=Path(sys.argv[1]); package=base/'@opencode-ai'/'plugin'
for p in (base,package):
    # Reject writeable/missing/non-directory package resolution roots.
    s=p.stat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid!=0 or (s.st_mode&0o022):
        raise SystemExit('P5_STOP=untrusted_C_module_dependency')
    # Ensure symbolic links cannot redirect to a writable directory.
    real=p.resolve(strict=True)
    q=real
    while True:
        st=q.stat()
        if st.st_uid != 0 or st.st_mode&0o022:
            raise SystemExit('P5_STOP=untrusted_module_dependency_parent')
        if q==q.parent: break
        q=q.parent
PY_CHECK_C_MODULES
[[ ! -e "$STAGE/tools-overlay/node_modules" && ! -L "$STAGE/tools-overlay/node_modules" ]] || stop 'unexpected overlay modules'
ln -s "$C_MODULES" "$STAGE/tools-overlay/node_modules"
[[ "$(readlink -- "$STAGE/tools-overlay/node_modules")" == "$C_MODULES" ]] || stop 'overlay modules path drift'
# Validate actual ESM import under hhl-agent, before any provider/model request.
# .mjs copies exist only for the Node 18 probe; tool files remain normal .js.
for name in research_python quantum_evaluate; do
  install -o root -g root -m 644 "$STAGE/tools-overlay/tools/$name.js" "$STAGE/tools-overlay/tools/$name.p5_import_probe.mjs"
done
/usr/sbin/runuser -u hhl-agent -g hhl-agent -- /usr/bin/env -i PATH=/usr/bin:/bin \
  /usr/bin/node --input-type=module - \
  "$STAGE/tools-overlay/tools/research_python.p5_import_probe.mjs" \
  "$STAGE/tools-overlay/tools/quantum_evaluate.p5_import_probe.mjs" <<'NODE_IMPORT_PROBE'
import { pathToFileURL } from 'node:url';
try {
  for (const filename of process.argv.slice(2)) {
    const loaded=await import(pathToFileURL(filename).href);
    if (!loaded.default) throw new Error('missing tool default');
  }
  console.log('P5_BOTH_TOOLS_ESM_MODULE_IMPORT=PASS');
} catch (e) {
  const code=typeof e?.code==='string' ? e.code : 'ESM_IMPORT_FAILED';
  console.error('P5_MODULE_IMPORT_STOP='+code);
  process.exitCode=2;
}
NODE_IMPORT_PROBE
for name in research_python quantum_evaluate; do
  rm -f -- "$STAGE/tools-overlay/tools/$name.p5_import_probe.mjs"
done

# C's exact approved root-only configuration adaptation; never rewrite base.
/usr/bin/python3 -I -B - "$BASE_CFG" "$CFG" <<'PY'
import json,os,sys
from pathlib import Path
sys.path.insert(0,'/run/hhl-task2/hhl-first-one-session/lib')
from hhl_live_config_gate import ORIGINAL_READ,ORIGINAL_EDIT,ALLOWED_READ,ALLOWED_EDIT
obj=json.loads(Path(sys.argv[1]).read_bytes()); perm=obj['permission']
if perm.get('read')!=ORIGINAL_READ or perm.get('edit')!=ORIGINAL_EDIT:raise SystemExit('P5_STOP=config drift')
perm['read']=ALLOWED_READ;perm['edit']=ALLOWED_EDIT;perm['quantum_evaluate']='allow'
# Permission maps are ORDERED; never sort their keys (last matching rule wins).
raw=(json.dumps(obj,sort_keys=False,indent=2,ensure_ascii=False)+'\n').encode()
fd=os.open(sys.argv[2],os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW|os.O_CLOEXEC,0o644)
# Match C's verified launch path: umask 077 otherwise yields mode 0600.
os.fchmod(fd,0o644)
with os.fdopen(fd,'wb') as out:out.write(raw);out.flush();os.fsync(out.fileno())
PY
CONFIG=1
[[ -f "$CFG" && ! -L "$CFG" && "$(stat -c '%u:%g:%a' "$CFG")" == '0:0:644' ]] || stop 'P5 runtime config owner/mode drift'

# OpenCode deep-merges permission maps from the original C project config.
# Even when a restrictive overlay has higher priority, an unrelated old ALLOW
# entry survives unless explicitly overridden by DENY. R4 observed exactly
# one broad legacy read ALLOW and four broad legacy edit ALLOW rules surviving.
# Never permit that implicit expansion. Construct a root-pinned fail-closed
# overlay by explicitly revoking those *exact* original C patterns, then
# appending the existing scoped C ALLOWED_READ/EDIT rules.
OVERLAY_CFG="$STAGE/tools-overlay/opencode.json"
[[ ! -e "$OVERLAY_CFG" && ! -L "$OVERLAY_CFG" ]] || stop 'P5 overlay config collision'
/usr/bin/python3 -I -B - "$CFG" "$OVERLAY_CFG" <<'PY_PERMISSIONS'
import json, os, sys
from pathlib import Path
sys.path.insert(0,'/run/hhl-task2/hhl-first-one-session/lib')
from hhl_live_config_gate import ORIGINAL_READ,ORIGINAL_EDIT,ALLOWED_READ,ALLOWED_EDIT

def hardened(legacy, allowed):
    if legacy.get('*') != 'deny' or allowed.get('*') != 'deny':
        raise SystemExit('P5_STOP=policy wildcard drift')
    old_allows={k for k,v in legacy.items() if k!='*' and v=='allow'}
    if any(k in allowed for k in old_allows):
        raise SystemExit('P5_STOP=legacy rule overlaps narrow rule')
    if any(v not in ('allow','deny') for v in allowed.values()):
        raise SystemExit('P5_STOP=unexpected scoped permission')
    result={'*':'deny'}
    result.update({k:'deny' for k in legacy if k in old_allows})
    result.update((k,v) for k,v in allowed.items() if k!='*')
    return result

src=Path(sys.argv[1]); out=Path(sys.argv[2]); obj=json.loads(src.read_bytes())
if obj['permission'].get('read')!=ALLOWED_READ or obj['permission'].get('edit')!=ALLOWED_EDIT:
    raise SystemExit('P5_STOP=approved runtime policy drift')
obj['permission']['read']=hardened(ORIGINAL_READ,ALLOWED_READ)
obj['permission']['edit']=hardened(ORIGINAL_EDIT,ALLOWED_EDIT)
# Project/runtime config merge can place the legacy DENY patterns *after*
# the scoped ALLOW patterns. OpenCode uses last-matching-rule precedence.
# Grant only C's previously approved narrow rule maps at the build-agent
# layer, which OpenCode appends after the global permission rules.
# This neither removes global DENY nor grants a new path.
agent=obj.setdefault('agent',{})
if not isinstance(agent,dict):
    raise SystemExit('P5_STOP=agent config structure drift')
build=agent.setdefault('build',{})
if not isinstance(build,dict):
    raise SystemExit('P5_STOP=build agent structure drift')
overrides=build.setdefault('permission',{})
if not isinstance(overrides,dict) or any(k in overrides for k in ('read','edit')):
    raise SystemExit('P5_STOP=unexpected existing build read/edit override')
# Gen2-only: use a narrower, file-specific read extension for Broker outputs.
# Do not modify global C policy, source zip, or write/edit permissions.
sys.path.insert(0,str(Path(sys.argv[2]).parent.parent))
from gen2_result_read_policy import extended_read
overrides['read']=extended_read(ALLOWED_READ)
overrides['edit']=ALLOWED_EDIT
# Permission maps are ORDERED; never sort their keys (last matching rule wins).
raw=(json.dumps(obj,sort_keys=False,indent=2,ensure_ascii=False)+'\n').encode()
fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o644)
# The overlay must be readable by the unprivileged hhl-agent process.
os.fchmod(fd,0o644)
with os.fdopen(fd,'wb') as f:
    f.write(raw);f.flush();os.fsync(f.fileno())
PY_PERMISSIONS
[[ -f "$OVERLAY_CFG" && ! -L "$OVERLAY_CFG" ]] || stop 'P5 overlay config unsafe'
[[ "$(stat -c '%u:%g:%a' "$OVERLAY_CFG")" == '0:0:644' ]] || stop 'P5 overlay config permission drift'
echo P5_LEGACY_ALLOW_REVOCATION_CONFIG_PREPARED=YES

# Keep old R2 spool records intact; they are separate from live C's original spool.
for spec in "$B_SPOOL:$(id -u hhl-runner):$(id -g hhl-runner)" \
            "$E_SPOOL:$(id -u hhl-eval):$(id -g hhl-eval)"; do
  IFS=: read -r folder owner group <<<"$spec"
  if [[ -e "$folder" || -L "$folder" ]]; then
    [[ -d "$folder" && ! -L "$folder" && "$(stat -c '%u:%g:%a' "$folder")" == "$owner:$group:700" ]] \
      || stop "unsafe source-bound spool: $folder"
  else
    install -d -o "$owner" -g "$group" -m 700 "$folder"
  fi
done
CREATED=1
for unit in "${UNITS[@]}"; do install -o root -g root -m 644 "$STAGE/$unit" "$U/$unit"; done
systemd-analyze verify "${UNITS[@]/#/$U/}"
systemctl daemon-reload
STARTED=1
systemctl start hhl-p1-review-research.socket hhl-p1-review-evaluator.socket
for socket in "$R_SOCK" "$E_SOCK"; do
  [[ -S "$socket" && "$(stat -c '%u:%g:%a' "$socket")" == '0:988:660' ]] || stop "missing P1 native socket: $socket"
done

# This is a new P4 DB-selected gen0 Parent, not another P0 frozen-source test.
RUN_TAG="hhl-$(date -u +%Y%m%d)-p5gen1canary-$(/usr/bin/python3 -I -B - <<'PY'
import secrets
print(secrets.token_hex(5))
PY
)"
/home/researcher/quantum-agent/.venv/bin/python -I -B "$STAGE/gen1_canary.py" --native-canary --run-tag "$RUN_TAG"
echo P5_NATIVE_PARENT_IN_C_AGENT_SCRATCH=PASS
echo P5_NEW_CHILD_CREATED=NO
# Gen2 verification runs only while original P7 sockets and stage are alive.
# The parent is sampled from the independent HHL-only Gen1 branch.
# Same verified Gen2 canary is required for paid or free mode.
[[ "$MODE" == '--preflight' || "$MODE" == '--paid' ]] || stop 'invalid Gen2 mode'
/home/researcher/quantum-agent/.venv/bin/python -I -B \
  "$SRC/gen2_free_native_gate.py" --free-gen2-parent-gate \
  --run-tag "hhl-$(date -u +%Y%m%d)-gen2parent-$(/usr/bin/python3 -I -B - <<'PYTAG'
import secrets
print(secrets.token_hex(5))
PYTAG
)"
# Test the actual staged paid entrypoint import without model calls.
/home/researcher/quantum-agent/.venv/bin/python -I -B - "$STAGE" <<'PY_GEN2_IMPORT'
import sys
sys.path.insert(0,sys.argv[1])
import gen2_paid
if not callable(gen2_paid.execute):
    raise SystemExit('GEN2_STOP=PAID_ENTRY_NOT_CALLABLE')
print('P7_GEN2_PAID_MODULE_IMPORT=PASS')
PY_GEN2_IMPORT

echo P9_AUTONOMOUS_EVOLUTION_CONTROLLER=YES
/home/researcher/quantum-agent/.venv/bin/python -I -B \
  "$STAGE/p9_evolution_controller.py" --plan
if [[ -n "${P11_CHECK_BRANCH:-}" ]]; then
  [[ "$P11_CHECK_BRANCH" =~ ^HHL_P9_EVOLUTION_[a-z0-9]{8}$ ]] || stop 'invalid fork check branch'
  /home/researcher/quantum-agent/.venv/bin/python -I -B \
    "$STAGE/p9_evolution_controller.py" --check-fork --resume-branch "$P11_CHECK_BRANCH"
fi
if [[ "$MODE" == '--paid' ]]; then
  # Paid exploration is a bounded loop; previously proven P7 native services remain alive.
  # Authorization is explicit. Original P6 and Coulomb databases are not written.
  KEY_FILE=/home/researcher/.config/quantum-agent/deepseek.key
  unset DEEPSEEK_API_KEY || true
  DEEPSEEK_API_KEY="$(/usr/bin/python3 -I -B - "$KEY_FILE" <<'PYKEY'
import os, pwd, stat, sys
try:
    fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as f:
        meta = os.fstat(f.fileno())
        if (not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1
            or stat.S_IMODE(meta.st_mode) != 0o600
            or meta.st_uid not in (0, pwd.getpwnam('nylue2014').pw_uid)):
            raise PermissionError('unsafe file metadata')
        raw = f.read(1024)
    key = raw.removesuffix(b'\r\n').removesuffix(b'\n')
    if (not 22 <= len(key) <= 256 or
        any(ch in b' \t\r\n\x00' for ch in key) or
        not key.isascii()):
        raise ValueError('invalid credential format')
    sys.stdout.write(key.decode('ascii'))
except (OSError, ValueError, PermissionError) as exc:
    print('P5_CREDENTIAL_STOP=' + type(exc).__name__, file=sys.stderr)
    raise SystemExit(2)
PYKEY
  )" || stop 'existing VM credential missing or unsafe'
  [[ ${#DEEPSEEK_API_KEY} -ge 22 ]] || stop 'no usable existing VM credential'
  P9ARGS=(--run --authorize-paid-evolution --iterations "$P9_ITERATIONS"
          --session-seconds "$P9_SECONDS")
  [[ "$P9_RESUME" == '-' ]] || P9ARGS+=(--resume-branch "$P9_RESUME")
  DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY" /home/researcher/quantum-agent/.venv/bin/python -I -B \
    "$STAGE/p9_evolution_controller.py" "${P9ARGS[@]}"
  unset DEEPSEEK_API_KEY
else
  echo P9_PAID_MODEL_NOT_STARTED=YES
fi
