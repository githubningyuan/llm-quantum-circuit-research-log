#!/usr/bin/env bash
# One root-only HHL recovery/inspection + P7 FREE auto-native gate.
# Does not accept paid flags or read API credentials.
set -Eeuo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/bin
unset DEEPSEEK_API_KEY || true
cd /
[[ $# -eq 0 ]] || { echo 'P7_STOP=NO_ARGUMENTS_ACCEPTED'; exit 2; }
[[ $(id -u) == 0 ]] || { echo 'P7_STOP=ROOT_REQUIRED'; exit 2; }
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
[[ "$SRC" == /run/hhl-task2/* && -d "$SRC" && ! -L "$SRC" ]] || { echo 'P7_STOP=UNTRUSTED_STAGE'; exit 2; }
(cd "$SRC" && sha256sum --status -c SHA256SUMS.txt) || { echo 'P7_STOP=ZIP_FILE_HASH_DRIFT'; exit 2; }
BASE=/srv/hhl-task2/research-records
TARGET=HHL_P5_GEN1_CANARY_jjl9nc5n
RUNNER=(/usr/sbin/runuser -u hhl-runner -- /usr/bin/env -i
  HOME=/srv/hhl-task2/rootless/home
  XDG_RUNTIME_DIR=/run/user/995
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/995/bus
  PATH=/usr/bin:/bin /usr/bin/podman)
[[ -d "$BASE" && ! -L "$BASE" && "$(stat -c '%u:%g:%a' "$BASE")" == 0:0:700 ]] || { echo 'P7_STOP=UNSAFE_RECORD_ROOT'; exit 2; }
[[ -d "$BASE/$TARGET" && ! -L "$BASE/$TARGET" && "$(stat -c '%u:%g:%a' "$BASE/$TARGET")" == 0:0:700 ]] || { echo 'P7_STOP=UNSAFE_P5_RECORD'; exit 2; }
[[ -f "$BASE/$TARGET/result.json" && ! -L "$BASE/$TARGET/result.json" ]] || { echo 'P7_STOP=MISSING_P5_RESULT'; exit 2; }
case "$(findmnt -n -o FSTYPE -T "$BASE")" in tmpfs|ramfs) echo 'P7_STOP=VOLATILE_RECORDS'; exit 2;; esac
# No active HHL OpenCode session, even if it has been renamed by the caller.
/usr/bin/python3 -I -B - <<'PY'
from pathlib import Path
for p in Path('/proc').iterdir():
    if not p.name.isdecimal(): continue
    try:
        if p.stat().st_uid != 997: continue
        parts=(p/'cmdline').read_bytes().split(b'\0')
    except (OSError,PermissionError): continue
    if parts and b'opencode' in parts[0].lower() and any(x in (b'run',b'serve') for x in parts[1:]):
        raise SystemExit('P7_STOP=ACTIVE_AGENT_SESSION')
PY
# Never interfere with C's rootless Broker even if a stale P5 container exists.
if [[ -S /run/hhl-task2/research-v4.sock ]]; then
  echo P7_STOP=C_RESEARCH_SOCKET_ACTIVE
  exit 2
fi
# Verify C's post-session captured files *before* making a durable copy.
/usr/bin/python3 -I -B - "$BASE/$TARGET" <<'PY_VERIFY_SOURCE'
import hashlib,json,re,stat,sys
from pathlib import Path
base=Path(sys.argv[1])/'frozen-after-session'
manifest=base/'manifest.json'
if not manifest.is_file() or manifest.is_symlink():
    raise SystemExit('P7_STOP=MISSING_FROZEN_SOURCE_MANIFEST')
obj=json.loads(manifest.read_bytes())
files=obj.get('files')
if not isinstance(files,list) or not 1<=len(files)<=55:
    raise SystemExit('P7_STOP=INVALID_SOURCE_FILE_LIST')
seen=set()
for entry in files:
    if type(entry) is not dict: raise SystemExit('P7_STOP=MALFORMED_SOURCE_ENTRY')
    name=entry.get('stored_as','')
    if not isinstance(name,str) or not re.fullmatch(r'[0-9a-f]{64}\.source',name) or name in seen:
        raise SystemExit('P7_STOP=INVALID_SOURCE_FILENAME')
    seen.add(name)
    file=base/name
    meta=file.lstat()
    if (not stat.S_ISREG(meta.st_mode) or meta.st_uid!=0 or meta.st_nlink!=1
            or stat.S_IMODE(meta.st_mode)!=0o600 or not 0<=meta.st_size<=2_000_000):
        raise SystemExit('P7_STOP=SOURCE_METADATA_DRIFT')
    if hashlib.sha256(file.read_bytes()).hexdigest()!=entry.get('sha256'):
        raise SystemExit('P7_STOP=SOURCE_HASH_MISMATCH')
print('P7_FROZEN_SOURCE_FILES_HASHED='+str(len(seen)))
PY_VERIFY_SOURCE
# Preserve the complete PRIVATE post-session snapshot and logs before any cleanup.
ARCHIVE="$(mktemp -d "$BASE/HHL_P7_PRESERVE_XXXXXXXX")"
chmod 700 "$ARCHIVE"
tar --one-file-system -C "$BASE" -czf "$ARCHIVE/p5-gen1-root-record.tar.gz" "$TARGET"
chmod 600 "$ARCHIVE/p5-gen1-root-record.tar.gz"
(cd "$ARCHIVE" && sha256sum p5-gen1-root-record.tar.gz > SHA256SUMS.txt && \
  sha256sum --status -c SHA256SUMS.txt && tar -tzf p5-gen1-root-record.tar.gz >/dev/null)
chmod 600 "$ARCHIVE/SHA256SUMS.txt"
echo P7_P5_SOURCE_AND_LOG_ARCHIVE=PASS
echo P7_ARCHIVE_DIR="$ARCHIVE"
# Limit inventory to the exact label used by C's pinned rootless_core.
"${RUNNER[@]}" ps -a --filter label=hhl.research.rootless=v4 --format json > "$ARCHIVE/orphans-inventory.json" || {
  echo P7_STOP=PODMAN_INVENTORY_FAILED; exit 2;
}
chmod 600 "$ARCHIVE/orphans-inventory.json"
# Inventory and metadata are persisted before removal; never remove running,
# unknown, name/label-drifted or concurrently-starting containers.
/usr/bin/python3 -I -B - "$ARCHIVE" <<'PY'
import json,re,subprocess,sys
from pathlib import Path
D=Path(sys.argv[1]); rows=json.loads((D/'orphans-inventory.json').read_bytes())
if not isinstance(rows,list): raise SystemExit('P7_STOP=INVENTORY_NOT_LIST')
R=['/usr/sbin/runuser','-u','hhl-runner','--','/usr/bin/env','-i',
   'HOME=/srv/hhl-task2/rootless/home','XDG_RUNTIME_DIR=/run/user/995',
   'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/995/bus','PATH=/usr/bin:/bin',
   '/usr/bin/podman']
print('P7_HHL_ORPHANS_INITIAL='+str(len(rows)),flush=True)
# Safeguard: no cleaning while root-run P1 socket services are active.
if rows:
    check=subprocess.run(['/usr/bin/systemctl','is-active',
       'hhl-p1-review-research.socket','hhl-p1-review-research.service',
       'hhl-p1-review-evaluator.socket','hhl-p1-review-evaluator.service'],
       capture_output=True,text=True,timeout=10)
    if any(x.strip()=='active' for x in check.stdout.splitlines()):
        raise SystemExit('P7_STOP=P1_SERVICE_ACTIVE_NO_CLEANUP')
# Validate ALL candidate containers before modifying ANY; fail closed.
validated=[]
for i,row in enumerate(rows):
    if type(row) is not dict: raise SystemExit('P7_STOP=MALFORMED_PODMAN_ROW')
    ident=str(row.get('Id',row.get('ID','')))
    if not re.fullmatch('[0-9a-f]{12,64}',ident):
        raise SystemExit('P7_STOP=BAD_CONTAINER_ID')
    run=subprocess.run([*R,'inspect',ident],capture_output=True,timeout=25,check=False)
    if run.returncode or len(run.stdout)>1_000_000:
        raise SystemExit('P7_STOP=CONTAINER_INSPECT_FAILED')
    (D/f'container-{i:02}.inspect.json').write_bytes(run.stdout)
    inspected=json.loads(run.stdout)
    if type(inspected) is not list or len(inspected)!=1:
        raise SystemExit('P7_STOP=CONTAINER_INSPECT_SHAPE')
    obj=inspected[0]
    full_id=obj.get('Id','')
    name=str(obj.get('Name','')).lstrip('/')
    labels=obj.get('Config',{}).get('Labels',{})
    state=obj.get('State',{})
    status=str(state.get('Status','')).lower()
    if (not isinstance(full_id,str) or not full_id.startswith(ident) or
        not re.fullmatch('hhl-rootless-v4-[0-9a-f]{24}',name) or
        type(labels) is not dict or labels.get('hhl.research.rootless')!='v4' or
        type(state) is not dict):
        raise SystemExit('P7_STOP=CONTAINER_IDENTITY_OR_LABEL_DRIFT')
    print('P7_CONTAINER_'+str(i)+'_STATE='+status,flush=True)
    if status!='exited' or state.get('Running') is not False:
        raise SystemExit('P7_STOP=ORPHAN_NOT_PROVEN_EXITED')
    validated.append(full_id)
# Only exact stopped containers; `podman rm` without -f won't kill workloads.
for ident in validated:
    run=subprocess.run([*R,'rm',ident],capture_output=True,text=True,timeout=30)
    if run.returncode:
        raise SystemExit('P7_STOP=TARGETED_STOPPED_RM_FAILED')
verify=subprocess.run([*R,'ps','-aq','--filter','label=hhl.research.rootless=v4'],
       capture_output=True,text=True,timeout=20)
if verify.returncode or verify.stdout.strip():
    raise SystemExit('P7_STOP=HHL_CONTAINERS_REMAIN')
print('P7_STOPPED_ORPHAN_REMOVAL='+('PASS' if validated else 'NOT_NEEDED'),flush=True)
PY
# No private data printed. Archive container metadata and its sha manifest.
(cd "$ARCHIVE" && files=(p5-gen1-root-record.tar.gz orphans-inventory.json); \
  for f in container-*.inspect.json; do [[ -f "$f" ]] && files+=("$f"); done; \
  sha256sum "${files[@]}" > SHA256SUMS.txt && sha256sum --status -c SHA256SUMS.txt)
chmod 600 "$ARCHIVE/SHA256SUMS.txt"
# Original P7 C/P1 services, now with one additional REAL auto-native gen0 canary.
echo P7_STARTING_FREE_AUTO_NATIVE_GATE=YES
bash "$SRC/run_vm_once.sh" --preflight
