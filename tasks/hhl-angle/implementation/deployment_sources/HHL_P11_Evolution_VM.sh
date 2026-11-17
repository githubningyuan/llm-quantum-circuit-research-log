#!/usr/bin/env bash
# P11: isolated fork of frozen R3; source-bound native Evaluator unchanged.
# --preflight has zero paid calls and zero formal DB writes.
# Paid runs require a separately forked branch and explicit authorization flags.
set -Eeuo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/bin
[[ "$(id -u)" == 0 ]] || { echo 'P9_STOP=ROOT_REQUIRED'; exit 2; }
MODE="${1:-}"
if [[ "$MODE" == '--run-internal' ]]; then echo 'P11_LAUNCHER_REV=P11_FAMILY_MEMORY_INSPIRATION_R1'; fi
P9_RESUME='-'
case "$MODE" in
  --preflight)
    [[ $# -eq 2 && "$2" =~ ^HHL_P9_EVOLUTION_[a-z0-9]{8}$ ]] || exit 2
    P9_RESUME="$2" ;;
  --inspect-fork|--prepare-fork) [[ $# -eq 1 ]] || exit 2 ;;
  --resume-paid-evolution|--run-internal)
    if [[ "$MODE" == '--resume-paid-evolution' ]]; then
      [[ $# -eq 4 ]] || exit 2
      P9_RESUME="$4"
    else
      [[ $# -eq 4 && "${P9_DETACHED_APPROVED:-}" == '1' ]] || exit 2
      P9_RESUME="$4"
    fi
    [[ "$2" =~ ^[0-9]{1,2}$ && "$3" =~ ^[0-9]{3,4}$ ]] || exit 2
    (( 10#$2>=1 && 10#$2<=12 && 10#$3>=180 && 10#$3<=2400 )) || exit 2
    [[ "$P9_RESUME" == '-' || "$P9_RESUME" =~ ^HHL_P9_EVOLUTION_[a-z0-9]{8}$ ]] || exit 2
    [[ "$P9_RESUME" != '-' ]] || exit 2
    ;;
  *) echo 'Usage: sudo bash HHL_P11_Evolution_VM.sh --inspect-fork | --prepare-fork | --preflight P11_FORK_BRANCH | --resume-paid-evolution N SECS P11_FORK_BRANCH'; exit 2 ;;
esac
BASE=/run/hhl-task2
USERDIR=/home/researcher
P7="$USERDIR/HHL_P7_Recovery_FreeNativeGate_2026-11-08.zip"
P40="$USERDIR/HHL_Gen2_AInspired_40m_20261116.zip"
P9="$USERDIR/HHL_P11_Multifamily_Research_20261116.zip"
for file in "$P7" "$P40" "$P9"; do
  [[ -f "$file" && ! -L "$file" ]] || { echo "P9_STOP=MISSING_PACKAGE:$file"; exit 2; }
done
printf '%s  %s\n' '918ed6230fd74f1879b54f75c24888912c812d7f0e588e8d21c3530c09bc7954' "$P7" | sha256sum --status -c - || { echo 'P9_STOP=P7_ZIP_HASH'; exit 2; }
printf '%s  %s\n' 'a027a7220c743d5f4000ef2e7b5fb9000a51267be6fd8f8fd8c04cc6b94b2841' "$P40" | sha256sum --status -c - || { echo 'P9_STOP=P40_ZIP_HASH'; exit 2; }
# P9_HASH_REPLACED_AT_BUILD
printf '%s  %s\n' 'e79712a94bd85bb02b45c592654c81a5854214b758ffd5ec4b7103b4635e9f2f' "$P9" | sha256sum --status -c - || { echo 'P9_STOP=P9_ZIP_HASH'; exit 2; }
# Offline inspection and creation of a SEPARATE fork. No model/evaluator calls.
if [[ "$MODE" == '--inspect-fork' || "$MODE" == '--prepare-fork' ]]; then
  D="$(mktemp -d /home/researcher/.p11-prepare.XXXXXXXX)"
  chmod 700 "$D"
  trap 'rm -rf -- "$D"' EXIT
  unzip -q "$P9" -d "$D"
  (cd "$D" && sha256sum --status -c SHA256SUMS.txt) || { echo 'P11_STOP=P11_FILES_HASH'; exit 2; }
  if [[ "$MODE" == '--inspect-fork' ]]; then
    /usr/bin/python3 -I -B "$D/p11_fork_branch.py" --inspect
  else
    /usr/bin/python3 -I -B "$D/p11_fork_branch.py" --fork --authorize-fork-write
  fi
  exit $?
fi
if [[ "$MODE" == '--resume-paid-evolution' || "$MODE" == '--run-internal' ]]; then
  P11_BRANCH="/srv/hhl-task2/research-records/$P9_RESUME"
  [[ "$P9_RESUME" != 'HHL_P9_EVOLUTION_78ca6975' && -d "$P11_BRANCH" && ! -L "$P11_BRANCH" && -f "$P11_BRANCH/P11_FORK_FROM_R3.json" ]] || {
    echo 'P11_STOP=PAID_REQUIRES_ISOLATED_FORK'; exit 2; }
fi
[[ -d "$BASE" && ! -L "$BASE" ]] || { echo 'P9_STOP=RUNTIME_ROOT_MISSING'; exit 2; }
# Reject concurrent HHL live sessions and native temporary services.
for unit in hhl-p1-review-research.socket hhl-p1-review-evaluator.socket hhl-p1-review-research.service hhl-p1-review-evaluator.service; do
  if systemctl is-active --quiet "$unit"; then echo "P9_STOP=ALREADY_ACTIVE:$unit"; exit 2; fi
done
for f in "$BASE/p1-review-code" "$BASE/hhl-v06-opencode.json"; do
  [[ ! -e "$f" && ! -L "$f" ]] || { echo "P9_STOP=STAGED_RUNTIME_COLLISION:$f"; exit 2; }
done
if [[ "$MODE" == '--resume-paid-evolution' ]]; then
  # Clean SSH-independent transient systemd unit; no credential exposed in arguments.
  unit="hhl-p9-evolution-$(date -u +%Y%m%d%H%M%S)-$$"
  driver="$(mktemp "$BASE/p9-detached.XXXXXXXX.sh")"
  chmod 700 "$driver"
  cat > "$driver" <<'INNER'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
trap 'rm -f -- "$0"' EXIT
export P9_DETACHED_APPROVED=1
/usr/bin/bash /home/researcher/HHL_P11_Evolution_VM.sh --run-internal "$1" "$2" "$3"
INNER
  outer=$((10#$2*10#$3+1200))
  if ! systemd-run --unit="$unit" --collect \
      --property=KillMode=control-group --property="RuntimeMaxSec=$outer" \
      /usr/bin/bash "$driver" "$2" "$3" "$P9_RESUME"; then
    rm -f -- "$driver";echo 'P9_STOP=SYSTEMD_RUN';exit 2
  fi
  echo "P11_PAID_UNIT=$unit"
  echo "P9_MONITOR=sudo journalctl -u $unit -f --no-pager"
  echo "P11_PROGRESS=/home/researcher/HHL_P11_progress.json"
  exit 0
fi
# Extract exact approved P7 and P40 source, then overlay the new controller.
D="$(mktemp -d "$BASE/p9-stage.XXXXXXXX")"
chmod 700 "$D"
trap 'rm -rf -- "$D"' EXIT
mkdir -m 700 "$D/p7" "$D/a40" "$D/p9"
unzip -q "$P7" -d "$D/p7"
(cd "$D/p7" && sha256sum --status -c SHA256SUMS.txt) || { echo 'P9_STOP=P7_FILES_HASH'; exit 2; }
unzip -q "$P40" -d "$D/a40"
(cd "$D/a40" && sha256sum --status -c SHA256SUMS.txt) || { echo 'P9_STOP=P40_FILES_HASH'; exit 2; }
unzip -q "$P9" -d "$D/p9"
(cd "$D/p9" && sha256sum --status -c SHA256SUMS.txt) || { echo 'P9_STOP=P9_FILES_HASH'; exit 2; }
# P7 native review service, tool policy and C Builder are reused unchanged.
for file in gen2_free_native_gate.py hhl_gen2_parent_adapter.py p6_hhl_nextgen.py gen2_paid.py gen1_canary.py gen2_result_read_policy.py; do
  install -o root -g root -m 644 "$D/a40/$file" "$D/p7/$file"
done
install -o root -g root -m 644 "$D/p9/p9_evolution_controller.py" "$D/p7/p9_evolution_controller.py"
install -o root -g root -m 644 "$D/p9/p11_research_layer.py" "$D/p7/p11_research_layer.py"
install -o root -g root -m 644 "$D/p9/run_vm_once_p9.sh" "$D/p7/run_vm_once.sh"
/usr/bin/python3 -I -B - "$D/p7" <<'PY'
import sys,hashlib
from pathlib import Path
root=Path(sys.argv[1]);manifest=root/'SHA256SUMS.txt'
rows=manifest.read_text().splitlines(); out=[];seen=set()
updates={'gen2_free_native_gate.py','hhl_gen2_parent_adapter.py','p6_hhl_nextgen.py',
         'gen2_paid.py','gen1_canary.py','gen2_result_read_policy.py',
         'p9_evolution_controller.py','p11_research_layer.py','run_vm_once.sh'}
for row in rows:
    _,rel=row.split('  ',1)
    if rel in seen:raise SystemExit('P9_STOP=DUPLICATE_MANIFEST_ROW')
    seen.add(rel)
    out.append(hashlib.sha256((root/rel).read_bytes()).hexdigest()+'  '+rel
               if rel in updates else row)
for rel in sorted(updates-seen):
    out.append(hashlib.sha256((root/rel).read_bytes()).hexdigest()+'  '+rel)
manifest.write_text('\n'.join(out)+'\n')
PY
(cd "$D/p7" && sha256sum --status -c SHA256SUMS.txt) || { echo 'P9_STOP=STAGED_FILES_HASH'; exit 2; }
if [[ "$MODE" == '--preflight' ]]; then
  P11_CHECK_BRANCH="$P9_RESUME" /usr/bin/bash "$D/p7/run_vm_once.sh" --preflight
  echo 'P11_PREFLIGHT=PASS'
else
  /usr/bin/bash "$D/p7/run_vm_once.sh" --paid --authorize-paid-evolution "$2" "$3" "$P9_RESUME"
fi
