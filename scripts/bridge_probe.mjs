#!/usr/bin/env node

import { readFile } from "node:fs/promises";
import { isAbsolute, join, relative, resolve } from "node:path";
import { pathToFileURL } from "node:url";


const [projectRootArgument, dataDirArgument, scenario] =
  process.argv.slice(2);

if (
  projectRootArgument === undefined
  || dataDirArgument === undefined
  || !["full", "smoke"].includes(scenario)
) {
  process.stderr.write(
    "usage: bridge_probe.mjs <project-root> <data-dir> <full|smoke>\n",
  );
  process.exit(2);
}

const projectRoot = resolve(projectRootArgument);
const dataDir = resolve(dataDirArgument);

try {
  const bridge = await import(
    pathToFileURL(join(projectRoot, "dist", "bridge.js")).href
  );
  const response = await import(
    pathToFileURL(join(projectRoot, "dist", "response.js")).href
  );

  const call = async (domain, action, payload = {}) => {
    const result = await bridge.callPython(
      { domain, action, payload },
      {
        packageRoot: projectRoot,
        dataDir,
        pythonExecutable: process.env.PYTHON,
        timeoutMs: 30_000,
      },
    );
    if (result.diagnostics.stderr.trim() !== "") {
      process.stderr.write(result.diagnostics.stderr);
      if (!result.diagnostics.stderr.endsWith("\n")) {
        process.stderr.write("\n");
      }
    }
    return response.sanitizePublicResponse(result.response);
  };

  let result;
  if (scenario === "smoke") {
    result = {
      scenario,
      initialized: await call("system", "initialize"),
      self_check: await call("system", "self_check"),
    };
  } else {
    const initialized = await call("system", "initialize");
    const progress = await call("report", "progress", {
      report_date: "2026-07-29",
    });
    const goals = await call("system", "update_goals", {
      calories_kcal: 2100,
      protein_g: 90,
      fat_g: 65,
      carbohydrate_g: 260,
      fiber_g: 30,
      sodium_mg: 2000,
      water_ml: 2200,
      timezone_name: "Asia/Shanghai",
      source_text: "确认桥接端到端目标",
    });
    const water = await call("water", "record", {
      amount: 300,
      unit: "ml",
      occurred_at: "2026-07-29T08:00:00+08:00",
      source_text: "桥接端到端记录 300 毫升水",
    });
    const weight = await call("weight", "record", {
      weight: 105,
      unit: "kg",
      status_note: "空腹",
    });
    const insights = await call("report", "insights", {
      report_date: "2026-07-29",
      period: "weekly",
      within_days: 7,
      limit: 5,
    });
    const report = await call("report", "daily", {
      report_date: "2026-07-29",
    });
    const relativePath = report?.data?.report?.relative_path;
    if (typeof relativePath !== "string" || isAbsolute(relativePath)) {
      throw new Error("daily report did not return a safe relative path");
    }
    const reportPath = resolve(dataDir, relativePath);
    const pathFromDataRoot = relative(dataDir, reportPath);
    if (
      pathFromDataRoot.startsWith("..")
      || isAbsolute(pathFromDataRoot)
    ) {
      throw new Error("daily report escaped the isolated data directory");
    }
    const reportText = await readFile(reportPath, "utf8");
    result = {
      scenario,
      initialized,
      progress,
      goals,
      water,
      weight,
      insights,
      report,
      report_contains_chinese: /[\u3400-\u9fff]/u.test(reportText),
    };
  }

  process.stdout.write(`${JSON.stringify(result)}\n`);
} catch (error) {
  const message = error instanceof Error
    ? `${error.name}: ${error.message}`
    : String(error);
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
}
