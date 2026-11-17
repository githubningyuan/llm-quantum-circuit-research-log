#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/bin
unset DEEPSEEK_API_KEY || true
cd /
[[ $(id -u) -eq 0 && ( $# -eq 0 || ( $# -eq 1 && "$1" == "--authorize-paid-gen2" ) ) ]] || { echo 'GEN2_STOP=ROOT_ONLY_NO_ARGS'; exit 2; }
ROOT=/run/hhl-task2
P7=/home/researcher/HHL_P7_Recovery_FreeNativeGate_2026-11-08.zip
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
[[ "$SRC" == "$ROOT"/* && -d "$SRC" && ! -L "$SRC" ]] || { echo 'GEN2_STOP=UNTRUSTED_PACKAGE'; exit 2; }
(cd "$SRC" && sha256sum --status -c SHA256SUMS.txt) || { echo 'GEN2_STOP=PACKAGE_HASH'; exit 2; }
printf '%s  %s\n' '918ed6230fd74f1879b54f75c24888912c812d7f0e588e8d21c3530c09bc7954' "$P7" | sha256sum -c -
D="$(mktemp -d "$ROOT/.gen2-p7-XXXXXXXX")"
chmod 700 "$D"
trap 'rm -rf -- "$D"' EXIT
unzip -q "$P7" -d "$D"
(cd "$D" && sha256sum --status -c SHA256SUMS.txt) || { echo 'GEN2_STOP=P7_ORIGINAL_HASH'; exit 2; }
# Only the transient copy of P7 is adapted. P7 source ZIP and VM deployments untouched.
for f in gen2_free_native_gate.py hhl_gen2_parent_adapter.py p6_hhl_nextgen.py gen2_paid.py gen1_canary.py gen2_result_read_policy.py; do
  install -o root -g root -m 644 "$SRC/$f" "$D/$f"
done
install -o root -g root -m 644 "$SRC/run_vm_once_gen2.sh" "$D/run_vm_once.sh"
# Update hashes for replaced files, append only genuinely new files.
# A duplicate gen1_canary.py row in the original P7 manifest caused STAGED_HASH.
/usr/bin/python3 -I -B - "$D" <<'PYMANIFEST'
import hashlib,sys
from pathlib import Path
root=Path(sys.argv[1]); manifest=root/'SHA256SUMS.txt'
injected=('gen2_free_native_gate.py','hhl_gen2_parent_adapter.py',
          'p6_hhl_nextgen.py','gen2_paid.py','gen1_canary.py',
          'gen2_result_read_policy.py','run_vm_once.sh')
rows=manifest.read_text().splitlines()
seen=set(); updated=[]
for row in rows:
    _,rel=row.split('  ',1)
    if rel in seen:raise SystemExit('GEN2_STOP=DUPLICATE_P7_MANIFEST')
    seen.add(rel)
    if rel in injected:
        h=hashlib.sha256((root/rel).read_bytes()).hexdigest()
        updated.append(f'{h}  {rel}')
    else:
        updated.append(row)
for rel in injected:
    if rel not in seen:
        updated.append(hashlib.sha256((root/rel).read_bytes()).hexdigest()+'  '+rel)
manifest.write_text('\n'.join(updated)+'\n')
PYMANIFEST
(cd "$D" && sha256sum --status -c SHA256SUMS.txt) || { echo 'GEN2_STOP=STAGED_HASH'; exit 2; }
# Ensure VM OpenCode exposes the same-session continuation flag.
# OpenCode may print --help to stderr. Capture both streams first, without
# pipefail/grep -q SIGPIPE false negatives. Never display credentials.
HELP_TEXT="$(/usr/sbin/runuser -u hhl-agent -g hhl-agent -- /srv/hhl-task2/bin/opencode run --help 2>&1)" || {
  echo 'GEN2_STOP=OPENCODE_HELP_FAILED'; exit 2;
}
case "$HELP_TEXT" in
  *'--session'*) echo 'GEN2_SESSION_FLAG=PASS' ;;
  *) echo 'GEN2_STOP=OPENCODE_SESSION_FLAG_UNAVAILABLE'; exit 2 ;;
esac
if [[ $# -eq 0 ]]; then
  /usr/bin/bash "$D/run_vm_once.sh" --preflight
else
  /usr/bin/bash "$D/run_vm_once.sh" --paid --authorize-paid-gen2
fi
