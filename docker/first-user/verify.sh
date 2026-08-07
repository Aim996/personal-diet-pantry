#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

readonly expected_uid="12001"
readonly plugin_id="personal-diet-pantry"
readonly package_archive="/opt/first-user-verification/plugin.tgz"
readonly summary_path="/home/newcomer/verification-summary.json"
readonly runtime_json="/tmp/personal-diet-pantry-runtime.json"
readonly smoke_json="/tmp/personal-diet-pantry-smoke.json"

if [[ "$(id -u)" != "${expected_uid}" ]]; then
  echo "verification must run as UID ${expected_uid}" >&2
  exit 1
fi

if [[ -e "${OPENCLAW_STATE_DIR}" ]] || [[ -e "${PERSONAL_DIET_PANTRY_DATA_DIR}" ]]; then
  echo "fresh-user state or data directory already exists" >&2
  exit 1
fi

test -f "${package_archive}"
mkdir -p "${OPENCLAW_STATE_DIR}" "${PERSONAL_DIET_PANTRY_DATA_DIR}"

openclaw_version="$(openclaw --version)"
python_version="$(${PYTHON} --version 2>&1)"

openclaw plugins install "npm-pack:${package_archive}" --force
openclaw config set \
  "plugins.entries.${plugin_id}.config.dataDir" \
  "${PERSONAL_DIET_PANTRY_DATA_DIR}"
openclaw plugins enable "${plugin_id}"
openclaw config validate --json
openclaw plugins inspect "${plugin_id}" --runtime --json > "${runtime_json}"

node - "${runtime_json}" <<'NODE'
const fs = require("node:fs");

const runtime = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const expected = [
  "diet_meal",
  "diet_water",
  "diet_weight",
  "diet_pantry",
  "diet_transaction",
  "diet_report",
  "diet_system",
];

const strings = new Set();
const collect = (value) => {
  if (typeof value === "string") {
    strings.add(value);
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) collect(item);
    return;
  }
  if (value && typeof value === "object") {
    for (const item of Object.values(value)) collect(item);
  }
};
collect(runtime);

const missing = expected.filter((name) => !strings.has(name));
if (missing.length > 0) {
  throw new Error(`runtime inspection is missing tools: ${missing.join(", ")}`);
}

const expectedHooks = [
  "before_prompt_build",
  "before_tool_call",
  "after_tool_call",
];
const missingHooks = expectedHooks.filter((name) => !strings.has(name));
if (missingHooks.length > 0) {
  throw new Error(
    `runtime inspection is missing safety hooks: ${missingHooks.join(", ")}`,
  );
}

const serialized = JSON.stringify(runtime);
if (serialized.includes("blocked because non-bundled plugins")) {
  throw new Error("runtime inspection reports a blocked safety hook");
}
NODE

plugin_root="$(
  find "${OPENCLAW_STATE_DIR}" \
    -type d \
    -path '*/node_modules/personal-diet-pantry' \
    -print \
    -quit
)"

if [[ -z "${plugin_root}" ]]; then
  echo "managed plugin package root was not found" >&2
  exit 1
fi

for excluded in src tests src-tests contracts; do
  if [[ -e "${plugin_root}/${excluded}" ]]; then
    echo "installable package unexpectedly contains ${excluded}" >&2
    exit 1
  fi
done

node /opt/first-user-verification/bridge_probe.mjs \
  "${plugin_root}" \
  "${PERSONAL_DIET_PANTRY_DATA_DIR}" \
  smoke > "${smoke_json}"

test -f "${PERSONAL_DIET_PANTRY_DATA_DIR}/diet.sqlite"
test "$(stat -c '%u' "${PERSONAL_DIET_PANTRY_DATA_DIR}/diet.sqlite")" = "${expected_uid}"

node - \
  "${runtime_json}" \
  "${smoke_json}" \
  "${summary_path}" \
  "${openclaw_version}" \
  "${python_version}" <<'NODE'
const fs = require("node:fs");

const runtime = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const smoke = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const outputPath = process.argv[4];
const openclawVersion = process.argv[5];
const pythonVersion = process.argv[6];

if (smoke?.initialized?.ok !== true) {
  throw new Error("diet_system initialize did not succeed");
}
if (smoke?.initialized?.data?.initialized !== true) {
  throw new Error("diet_system initialize did not report initialized=true");
}

const checks = smoke?.self_check?.data?.checks;
if (!Array.isArray(checks)) {
  throw new Error("diet_system self_check did not return checks");
}
const failures = checks.filter((check) => check?.level === "FAIL");
if (failures.length > 0) {
  throw new Error(`diet_system self_check returned ${failures.length} FAIL result(s)`);
}

const expectedTools = [
  "diet_meal",
  "diet_water",
  "diet_weight",
  "diet_pantry",
  "diet_transaction",
  "diet_report",
  "diet_system",
];

const summary = {
  status: "pass",
  product_version: "0.7.4.28",
  openclaw_version: openclawVersion,
  python_version: pythonVersion,
  runtime_inspection_available: runtime !== null,
  runtime_user: { uid: 12001, root: false },
  tools: expectedTools,
  initialize_succeeded: true,
  self_check_failures: 0,
  sqlite_created: true,
};

fs.writeFileSync(outputPath, `${JSON.stringify(summary, null, 2)}\n`, {
  encoding: "utf8",
  mode: 0o600,
});
process.stdout.write(`${JSON.stringify(summary)}\n`);
NODE
