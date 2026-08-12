#!/usr/bin/env bash
# Yield false-positive regression (NOT formal 6×20).
# A: 1node×2rank×2iter Megatron + in-pod 0.15s yield poll → CLEAR/STARTUP → done
# B: same-pod no-marker pretrain_gpt.py fixture → OPPONENT; kill exact PID only
# C: PermissionError fixture → CHECK_FAILED
# 落盘仅 yinjinrun.p-huawei；持续让路；失败保留证据不盲重跑。
set -euo pipefail

EXP_LOCAL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JUMP="${JUMP:-afs-cpu}"
KUBE="${KUBE:-/root/.kube/config-vc-a3-241ceshi-songyiyang.yaml}"
KUBECTL="${KUBECTL:-/root/bin/kubectl}"
NS="${NS:-default}"
JOB_NAME="${JOB_NAME:-yjr-mspti-256-r7-20260811}"
MASTER_POD="${MASTER_POD:-${JOB_NAME}-master-0}"

NNODES=1
NPROC=2
TRAIN_ITERS="${TRAIN_ITERS:-2}"
CAPTURE_ITER=1
ARM=normal
TP=1; PP=1; MBS=1; GBS=2; SEQ=1024; LAYERS=2; SEED=1234
RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)-yield-fp-reg}"
RUN_MARKER="MSPTI_YIELD_FP_${RUN_ID}"
AFS_ROOT="/afs-a3-weight-share/yinjinrun.p-huawei"
OUT_DIR="${AFS_ROOT}/results/mspti-sync-skeleton/yield-regression/${RUN_ID}"
CODE_DIR="${AFS_ROOT}/probing-huawei/experiments/mspti_sync_skeleton-yield-fp"
BACKUP_ROOT="/Users/yinjinrun/Codespace/myportal/results/huawei-a3-32/mspti-sync-skeleton/yield-regression/${RUN_ID}"
LOG_DIR="/Users/yinjinrun/Codespace/myportal/logs/mspti-yield-fp-${RUN_ID}"
MASTER_PORT="${MASTER_PORT:-38221}"
# pjlab-new (pvc-5gnm2) 无 /afs-a3-weight-share/enwiki；自有 shard 在 yinjinrun.p-huawei/data。
DATA_PATH="${DATA_PATH:-/afs-a3-weight-share/yinjinrun.p-huawei/data/enwiki20230101/enwiki20230101-00000_text_document}"
CACHE="${CACHE:-/afs-a3-weight-share/yinjinrun.p-huawei/megatron-data-cache}"
YIELD_INTERVAL_S="${YIELD_INTERVAL_S:-0.15}"
WAIT_TIMEOUT_S="${WAIT_TIMEOUT_S:-900}"

mkdir -p "${LOG_DIR}" "${BACKUP_ROOT}"
exec > >(tee -a "${LOG_DIR}/launcher.log") 2>&1

echo "=== YIELD_FP_REGRESSION RUN_ID=${RUN_ID} ==="
echo "JOB_NAME=${JOB_NAME} MASTER_POD=${MASTER_POD}"
echo "DATA_PATH=${DATA_PATH}"
echo "OUT_DIR=${OUT_DIR}"
echo "BACKUP_ROOT=${BACKUP_ROOT}"
echo "DURATION_ESTIMATE: 1n2r×2iter normal ~3-10min + fixture B/C <1min"

jump() {
  ssh -o ConnectTimeout=30 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
    "${JUMP}" "export KUBECONFIG='${KUBE}'; K='${KUBECTL}'; $*"
}

pod_exec() {
  local pod="$1"; shift
  jump "\$K exec -n '${NS}' '${pod}' -- bash --noprofile --norc -lc $(printf '%q' "$*")"
}

yield_check() {
  set +e
  out="$(pod_exec "${MASTER_POD}" \
    "python3 '${CODE_DIR}/opponent_check.py' --mode yield --out-dir '${OUT_DIR}' --run-marker '${RUN_MARKER}' --node-id 0" 2>&1)"
  rc=$?
  set -e
  printf '%s\n' "${out}"
  return "${rc}"
}

idle_check() {
  set +e
  out="$(pod_exec "${MASTER_POD}" "python3 '${CODE_DIR}/opponent_check.py' --mode idle" 2>&1)"
  rc=$?
  set -e
  echo "IDLE rc=${rc} out=${out}"
  if [[ "${rc}" -ne 0 || "${out}" != *CLEAR* ]]; then
    echo "FATAL: not idle; refuse start: rc=${rc} out=${out}" >&2
    exit 11
  fi
}

pull_evidence() {
  mkdir -p "${BACKUP_ROOT}"
  set +e
  jump "\$K exec -n '${NS}' '${MASTER_POD}' -- tar -C '${OUT_DIR}' -cf - ." \
    | tar -C "${BACKUP_ROOT}" -xf -
  local rc=$?
  set -e
  echo "PULL_EVIDENCE rc=${rc} -> ${BACKUP_ROOT}"
  return "${rc}"
}

cleanup_ours() {
  set +e
  pod_exec "${MASTER_POD}" \
    "python3 '${CODE_DIR}/kill_attempt.py' --out-dir '${OUT_DIR}' --run-marker '${RUN_MARKER}' --node-id 0" || true
  set -e
}

echo "[sync] code -> ${CODE_DIR}"
COPYFILE_DISABLE=1 tar -C "${EXP_LOCAL}" -cf - \
  opponent_check.py kill_attempt.py run_megatron_node.sh local_group_guard.py yield_poller.py preflight_dataset_gate.py \
  | ssh -o ConnectTimeout=30 "${JUMP}" \
    "export KUBECONFIG='${KUBE}'; K='${KUBECTL}'; \$K exec -i -n '${NS}' '${MASTER_POD}' -- bash --noprofile --norc -lc 'mkdir -p ${CODE_DIR} && tar -C ${CODE_DIR} -xf - && chmod +x ${CODE_DIR}/*.sh ${CODE_DIR}/*.py'"

idle_check

pod_exec "${MASTER_POD}" "mkdir -p '${OUT_DIR}' '${CACHE}'"

echo "[DATA_GATE] pod=${MASTER_POD} data_path=${DATA_PATH}"
DATA_GATE_OUT="${BACKUP_ROOT}/data_gate.json"
set +e
GATE_LOG="$(pod_exec "${MASTER_POD}" \
  "python3 '${CODE_DIR}/preflight_dataset_gate.py' --data-path '${DATA_PATH}' --out '${OUT_DIR}/data_gate.json'")"
GATE_RC=$?
set -e
echo "${GATE_LOG}" | tee "${LOG_DIR}/data_gate.log"
pod_exec "${MASTER_POD}" "cat '${OUT_DIR}/data_gate.json'" >"${DATA_GATE_OUT}" 2>/dev/null || true
if [[ "${GATE_RC}" -ne 0 ]] || ! grep -q '"DATA_OK": "PASS"' "${DATA_GATE_OUT}" 2>/dev/null; then
  echo "FATAL: DATA_GATE FAIL rc=${GATE_RC} — refuse A arm (see ${DATA_GATE_OUT})" >&2
  exit 12
fi
echo "DATA_OK=PASS receipt=${DATA_GATE_OUT}"

pod_exec "${MASTER_POD}" "python3 - <<'PY'
import json
from pathlib import Path
Path('${OUT_DIR}/config.json').write_text(json.dumps({
  'run_id': '${RUN_ID}',
  'purpose': 'yield_false_positive_regression',
  'arm': 'normal',
  'nnodes': 1,
  'nproc': 2,
  'train_iters': ${TRAIN_ITERS},
  'yield_interval_s': ${YIELD_INTERVAL_S},
}, indent=2), encoding='utf-8')
print('CONFIG_OK')
PY"

MASTER_ADDR="$(jump "\$K get pod -n '${NS}' '${MASTER_POD}' -o jsonpath='{.status.podIP}'")"
[[ -n "${MASTER_ADDR}" ]]

COMMON="RUN_ID='${RUN_ID}' RUN_MARKER='${RUN_MARKER}' ARM='${ARM}' MASTER_ADDR='${MASTER_ADDR}' MASTER_PORT='${MASTER_PORT}' NNODES='${NNODES}' NPROC='${NPROC}' CODE_DIR='${CODE_DIR}' OUT_DIR='${OUT_DIR}' TRAIN_ITERS='${TRAIN_ITERS}' MSPTI_CAPTURE_MEGATRON_ITER='${CAPTURE_ITER}' RUN_TIMEOUT_S='${RUN_TIMEOUT_S:-900}' SEED='${SEED}' TP='${TP}' PP='${PP}' MBS='${MBS}' GBS='${GBS}' SEQ='${SEQ}' LAYERS='${LAYERS}' DATA_PATH='${DATA_PATH}' CACHE='${CACHE}' YIELD_INTERVAL_S='${YIELD_INTERVAL_S}'"

echo "[A] launch 1n2r normal megatron + yield poller"
pod_exec "${MASTER_POD}" \
  "env ${COMMON} NODE_RANK=0 setsid nohup bash '${CODE_DIR}/run_megatron_node.sh' </dev/null >'${OUT_DIR}/launch_0.log' 2>&1 & echo LAUNCH_PID=\$!; \
   env ${COMMON} setsid nohup python3 '${CODE_DIR}/yield_poller.py' </dev/null >'${OUT_DIR}/yield_poller.log' 2>&1 & echo \$! >'${OUT_DIR}/yield_poller.pid'; echo POLLER_PID=\$(cat '${OUT_DIR}/yield_poller.pid')" \
  | tee "${LOG_DIR}/launch_pid.txt"

echo "[A] wait for done/fail"
deadline=$((SECONDS + WAIT_TIMEOUT_S))
A_STATUS="RUNNING"
POLL_N=0
while (( SECONDS < deadline )); do
  status="$(pod_exec "${MASTER_POD}" "python3 - <<'PY'
from pathlib import Path
import json
out=Path('${OUT_DIR}')
done=int((out/'node_0.done').exists())
fail=int((out/'node_0.fail').exists())
n=0
bad=[]
p=out/'yield_poll.jsonl'
if p.exists():
    for ln in p.read_text(encoding='utf-8', errors='replace').splitlines():
        if not ln.strip():
            continue
        n+=1
        try:
            o=json.loads(ln)
        except Exception:
            continue
        line=o.get('line') or ''
        rc=int(o.get('rc') or 0)
        if line.startswith('OPPONENT') or rc==10:
            bad.append(('OPPONENT', line[:240]))
        elif line.startswith('CHECK_FAILED') or rc==20:
            bad.append(('CHECK_FAILED', line[:240]))
        elif line and line not in ('CLEAR','STARTUP','OK') and rc!=0:
            bad.append(('UNEXPECTED', line[:240]))
print(done, fail, n, len(bad))
if bad:
    print('BAD', bad[0][0], bad[0][1])
PY")"
  done_c="$(awk 'NR==1{print $1}' <<<"${status}")"
  fail_c="$(awk 'NR==1{print $2}' <<<"${status}")"
  POLL_N="$(awk 'NR==1{print $3}' <<<"${status}")"
  bad_n="$(awk 'NR==1{print $4}' <<<"${status}")"
  echo "$(date +%H:%M:%S) done=${done_c} fail=${fail_c} polls=${POLL_N} bad=${bad_n}"
  if [[ "${bad_n}" != "0" ]]; then
    echo "${status}" | tee "${LOG_DIR}/A_bad.txt"
    if grep -q 'BAD OPPONENT' <<<"${status}"; then
      A_STATUS="OWN_FALSE_POSITIVE_OR_OPPONENT"
    elif grep -q 'BAD CHECK_FAILED' <<<"${status}"; then
      A_STATUS="CHECK_FAILED"
    else
      A_STATUS="UNEXPECTED"
    fi
    break
  fi
  if (( fail_c > 0 )); then
    A_STATUS="TRAIN_FAIL"
    break
  fi
  if (( done_c == 1 )); then
    sleep 3
    status2="$(pod_exec "${MASTER_POD}" "python3 - <<'PY'
from pathlib import Path
import json
out=Path('${OUT_DIR}')
n=0
bad=[]
p=out/'yield_poll.jsonl'
if p.exists():
    for ln in p.read_text(encoding='utf-8', errors='replace').splitlines():
        if not ln.strip():
            continue
        n+=1
        o=json.loads(ln)
        line=o.get('line') or ''
        rc=int(o.get('rc') or 0)
        if line.startswith('OPPONENT') or rc==10:
            bad.append(line[:240])
        elif line.startswith('CHECK_FAILED') or rc==20:
            bad.append(line[:240])
print(n, len(bad))
if bad:
    print('BAD', bad[0])
PY")"
    POLL_N="$(awk 'NR==1{print $1}' <<<"${status2}")"
    bad_n="$(awk 'NR==1{print $2}' <<<"${status2}")"
    if [[ "${bad_n}" != "0" ]]; then
      echo "${status2}" | tee "${LOG_DIR}/A_bad_exit.txt"
      A_STATUS="OWN_FALSE_POSITIVE_OR_OPPONENT"
    else
      A_STATUS="PASS"
    fi
    break
  fi
  sleep 5
done

if [[ "${A_STATUS}" == "RUNNING" ]]; then
  A_STATUS="TIMEOUT"
fi

OPPONENT_SEEN=0
CHECK_FAILED_SEEN=0
[[ "${A_STATUS}" == "OWN_FALSE_POSITIVE_OR_OPPONENT" ]] && OPPONENT_SEEN=1
[[ "${A_STATUS}" == "CHECK_FAILED" ]] && CHECK_FAILED_SEEN=1

echo "A_RESULT status=${A_STATUS} polls=${POLL_N} opponent_seen=${OPPONENT_SEEN} check_failed_seen=${CHECK_FAILED_SEEN}"
printf '%s\n' "${A_STATUS}" >"${BACKUP_ROOT}/A_STATUS.txt"

pod_exec "${MASTER_POD}" "if [[ -f '${OUT_DIR}/yield_poller.pid' ]]; then kill \$(cat '${OUT_DIR}/yield_poller.pid') 2>/dev/null || true; fi" || true

if [[ "${A_STATUS}" != "PASS" ]]; then
  echo "A failed — pull evidence; still attempt B/C after cleanup"
  cleanup_ours
  pull_evidence || true
fi

cleanup_ours
sleep 2
idle_check

echo "[B] start no-marker pretrain_gpt.py fixture + yield in SAME exec (avoid kubectl cgroup reap)"
B_COMBINED="$(pod_exec "${MASTER_POD}" "python3 - <<'PY'
import json, os, signal, subprocess, time
from pathlib import Path
out = Path('${OUT_DIR}')
code = Path('${CODE_DIR}')
marker = '${RUN_MARKER}'
fxdir = out / 'fixture_b'
fxdir.mkdir(parents=True, exist_ok=True)
script = fxdir / 'pretrain_gpt.py'
script.write_text('import time\ntime.sleep(120)\n', encoding='utf-8')
# Detach from this kubectl exec session.
p2 = subprocess.Popen(
    ['python3', str(script)],
    start_new_session=True,
    env={k: v for k, v in os.environ.items() if k not in ('RUN_MARKER', 'OUT_DIR')},
)
(fxdir / 'fixture.pid').write_text(str(p2.pid), encoding='utf-8')
(fxdir / 'fixture.token').write_text('${RUN_ID}', encoding='utf-8')
time.sleep(0.5)
# Ensure yield mode not STARTUP
pg = out / 'node_0.pgid'
if not pg.exists():
    pg.write_text('999999001\n', encoding='utf-8')
chk = subprocess.run(
    ['python3', str(code / 'opponent_check.py'),
     '--mode', 'yield', '--out-dir', str(out),
     '--run-marker', marker, '--node-id', '0'],
    capture_output=True, text=True,
)
line = (chk.stdout or '').strip().splitlines()
last = line[-1] if line else ''
# Exact-PID cleanup only
try:
    os.kill(p2.pid, signal.SIGKILL)
    killed = 'killed_exact'
except ProcessLookupError:
    killed = 'already_gone'
print(json.dumps({
    'fixture_pid': p2.pid,
    'yield_rc': int(chk.returncode),
    'yield_line': last,
    'cleanup': killed,
}))
PY")"
echo "B_COMBINED=${B_COMBINED}"
B_JSON="$(printf '%s\n' "${B_COMBINED}" | awk '/^{/{print; exit}')"
FIX_PID="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["fixture_pid"])' "${B_JSON}")"
B_RC="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["yield_rc"])' "${B_JSON}")"
B_LAST="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["yield_line"])' "${B_JSON}")"
echo "B fixture_pid=${FIX_PID} yield_rc=${B_RC} line=${B_LAST}"
echo "${FIX_PID}" >"${BACKUP_ROOT}/B_fixture.pid"
if [[ "${B_RC}" -eq 10 && "${B_LAST}" == OPPONENT* ]] && { [[ "${B_LAST}" == *"${FIX_PID}"* ]] || [[ "${B_LAST}" == *"pretrain_gpt.py"* ]]; }; then
  B_STATUS="PASS"
else
  B_STATUS="FAIL"
fi
echo "B_RESULT status=${B_STATUS}"
printf '%s\n' "${B_STATUS}" >"${BACKUP_ROOT}/B_STATUS.txt"
printf '%s\n' "${B_LAST}" >"${BACKUP_ROOT}/B_yield_line.txt"
sleep 1

echo "[C] CHECK_FAILED fixture"
set +e
C_OUT="$(pod_exec "${MASTER_POD}" "python3 '${CODE_DIR}/opponent_check.py' --mode yield --out-dir '${OUT_DIR}' --run-marker '${RUN_MARKER}' --node-id 0 \
  --fixture-ps-rc 0 \
  --fixture-ps-stdout \$'PID PGID STAT ARGS\\n10 10 S torchrun\\n77 77 R pretrain_gpt.py opaque\\n' \
  --fixture-environ-json '{\"10\":{\"status\":\"OK\",\"has_marker\":true,\"marker_value\":\"${RUN_MARKER}\",\"has_out_dir\":true,\"out_dir_value\":\"${OUT_DIR}\",\"starttime\":1},\"77\":{\"status\":\"ERROR\",\"errno\":13,\"errno_name\":\"EACCES\",\"starttime\":9}}'")"
C_RC=$?
set -e
echo "C yield_rc=${C_RC} out=${C_OUT}"
C_LAST="$(printf '%s\n' "${C_OUT}" | awk 'NF{p=$0} END{print p}')"
if [[ "${C_RC}" -eq 20 && "${C_LAST}" == CHECK_FAILED* && "${C_LAST}" == *pid=77* ]]; then
  C_STATUS="PASS"
else
  C_STATUS="FAIL"
fi
echo "C_RESULT status=${C_STATUS}"
printf '%s\n' "${C_STATUS}" >"${BACKUP_ROOT}/C_STATUS.txt"
printf '%s\n' "${C_LAST}" >"${BACKUP_ROOT}/C_yield_line.txt"

set +e
idle_check
set -e

pull_evidence || true

python3 - <<PY
from pathlib import Path
import json
root = Path("${BACKUP_ROOT}")
root.mkdir(parents=True, exist_ok=True)
summary = {
  "run_id": "${RUN_ID}",
  "A_status": "${A_STATUS}",
  "B_status": "${B_STATUS}",
  "C_status": "${C_STATUS}",
  "polls": int("${POLL_N}" or "0"),
  "out_dir": "${OUT_DIR}",
  "backup_root": "${BACKUP_ROOT}",
  "log_dir": "${LOG_DIR}",
  "run_marker": "${RUN_MARKER}",
  "fixture_b_pid": "${FIX_PID}",
}
(root / "SUMMARY.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
(root / "SUMMARY.md").write_text("\n".join([
  "# Yield FP Regression SUMMARY",
  "",
  f"- RUN_ID: \`{summary['run_id']}\`",
  f"- A (1n2r high-freq yield): **{summary['A_status']}** (polls={summary['polls']})",
  f"- B (no-marker fixture OPPONENT): **{summary['B_status']}** (pid={summary['fixture_b_pid']})",
  f"- C (PermissionError CHECK_FAILED): **{summary['C_status']}**",
  f"- remote: \`{summary['out_dir']}\`",
  f"- local: \`{summary['backup_root']}\`",
  "",
]), encoding="utf-8")
print(json.dumps(summary, indent=2))
PY

if [[ "${A_STATUS}" == "PASS" && "${B_STATUS}" == "PASS" && "${C_STATUS}" == "PASS" ]]; then
  echo "YIELD_FP_REGRESSION_OK"
  exit 0
fi
echo "YIELD_FP_REGRESSION_FAIL A=${A_STATUS} B=${B_STATUS} C=${C_STATUS}" >&2
exit 1
