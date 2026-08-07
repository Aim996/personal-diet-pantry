import { spawn } from "node:child_process";
import { delimiter, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export type JsonObject = Record<string, unknown>;

export type BridgeErrorCode =
  | "ABORTED"
  | "INVALID_REQUEST"
  | "INVALID_RESPONSE"
  | "OUTPUT_LIMIT"
  | "PROCESS_ERROR"
  | "TIMEOUT";

export interface BridgeDiagnostics {
  exitCode: number | null;
  stderr: string;
}

export interface PythonCallResult<T extends JsonObject = JsonObject> {
  response: T;
  diagnostics: BridgeDiagnostics;
}

export interface CallPythonOptions {
  dataDir?: string;
  env?: NodeJS.ProcessEnv;
  maxOutputBytes?: number;
  packageRoot?: string;
  pythonExecutable?: string;
  signal?: AbortSignal;
  timeoutMs?: number;
}

export interface BridgeErrorOptions extends ErrorOptions {
  terminationConfirmed?: boolean;
}

export class BridgeError extends Error {
  readonly code: BridgeErrorCode;
  readonly diagnostics: BridgeDiagnostics;
  readonly terminationConfirmed: boolean;

  constructor(
    code: BridgeErrorCode,
    message: string,
    diagnostics: BridgeDiagnostics = { exitCode: null, stderr: "" },
    options?: BridgeErrorOptions,
  ) {
    super(message, options);
    this.name = "BridgeError";
    this.code = code;
    this.diagnostics = diagnostics;
    Object.defineProperty(this, "diagnostics", { enumerable: false });
    this.terminationConfirmed = options?.terminationConfirmed ?? false;
  }
}

const DEFAULT_TIMEOUT_MS = 15_000;
const DEFAULT_MAX_OUTPUT_BYTES = 1024 * 1024;
const DEFAULT_PACKAGE_ROOT = resolve(
  fileURLToPath(new URL("..", import.meta.url)),
);

export function callPython<T extends JsonObject = JsonObject>(
  request: JsonObject,
  options: CallPythonOptions = {},
): Promise<PythonCallResult<T>> {
  if (options.signal?.aborted) {
    return Promise.reject(
      new BridgeError("ABORTED", "Python request was aborted"),
    );
  }

  let requestLine: string;
  try {
    requestLine = `${JSON.stringify(request)}\n`;
  } catch (error) {
    return Promise.reject(
      new BridgeError(
        "INVALID_REQUEST",
        "Python request is not JSON serializable",
        undefined,
        { cause: error },
      ),
    );
  }

  const timeoutMs = positiveLimit(
    options.timeoutMs,
    DEFAULT_TIMEOUT_MS,
    "timeoutMs",
  );
  const maxOutputBytes = positiveLimit(
    options.maxOutputBytes,
    DEFAULT_MAX_OUTPUT_BYTES,
    "maxOutputBytes",
  );
  const packageRoot = resolve(options.packageRoot ?? DEFAULT_PACKAGE_ROOT);
  const effectiveEnv = { ...process.env, ...options.env };
  const pythonRoot = join(packageRoot, "python");
  const inheritedPythonPath = effectiveEnv.PYTHONPATH;
  const env: NodeJS.ProcessEnv = {
    ...effectiveEnv,
    PYTHONDONTWRITEBYTECODE: effectiveEnv.PYTHONDONTWRITEBYTECODE ?? "1",
    PYTHONPATH: inheritedPythonPath
      ? `${pythonRoot}${delimiter}${inheritedPythonPath}`
      : pythonRoot,
  };
  if (options.dataDir !== undefined) {
    env.PERSONAL_DIET_PANTRY_DATA_DIR = options.dataDir;
  }

  return new Promise((resolvePromise, rejectPromise) => {
    const child = spawn(
      options.pythonExecutable ?? effectiveEnv.PYTHON ?? "python",
      ["-m", "personal_diet_pantry.cli"],
      {
        cwd: packageRoot,
        env,
        shell: false,
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
      },
    );
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];
    let stdoutBytes = 0;
    let stderrBytes = 0;
    let terminalError: BridgeError | undefined;
    let settled = false;

    const diagnostics = (exitCode: number | null): BridgeDiagnostics => ({
      exitCode,
      stderr: Buffer.concat(stderr).toString("utf8"),
    });
    const terminateWith = (error: BridgeError) => {
      if (terminalError) {
        return;
      }
      terminalError = error;
      child.kill("SIGKILL");
    };
    const onAbort = () => {
      terminateWith(
        new BridgeError(
          "ABORTED",
          "Python request was aborted",
          diagnostics(null),
        ),
      );
    };
    options.signal?.addEventListener("abort", onAbort, { once: true });

    const timeout = setTimeout(() => {
      terminateWith(
        new BridgeError(
          "TIMEOUT",
          `Python request exceeded ${timeoutMs}ms`,
          diagnostics(null),
        ),
      );
    }, timeoutMs);
    timeout.unref();

    child.stdout.on("data", (chunk: Buffer) => {
      stdoutBytes += chunk.byteLength;
      if (stdoutBytes > maxOutputBytes) {
        terminateWith(
          new BridgeError(
            "OUTPUT_LIMIT",
            `Python standard output exceeded ${maxOutputBytes} bytes`,
            diagnostics(null),
          ),
        );
        return;
      }
      stdout.push(chunk);
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderrBytes += chunk.byteLength;
      if (stderrBytes > maxOutputBytes) {
        terminateWith(
          new BridgeError(
            "OUTPUT_LIMIT",
            `Python standard error exceeded ${maxOutputBytes} bytes`,
            diagnostics(null),
          ),
        );
        return;
      }
      stderr.push(chunk);
    });
    child.on("error", (error) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timeout);
      options.signal?.removeEventListener("abort", onAbort);
      const terminationConfirmed = child.pid === undefined;
      rejectPromise(
        terminalError
          ? new BridgeError(
              terminalError.code,
              terminalError.message,
              diagnostics(null),
              {
                cause: terminalError.cause,
                terminationConfirmed,
              },
            )
          : new BridgeError(
            "PROCESS_ERROR",
            "Unable to start the Python service",
            diagnostics(null),
            { cause: error, terminationConfirmed },
          ),
      );
    });
    child.on("close", (exitCode) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timeout);
      options.signal?.removeEventListener("abort", onAbort);
      if (terminalError) {
        rejectPromise(
          new BridgeError(
            terminalError.code,
            terminalError.message,
            diagnostics(exitCode),
            {
              cause: terminalError.cause,
              terminationConfirmed: true,
            },
          ),
        );
        return;
      }

      const childDiagnostics = diagnostics(exitCode);
      try {
        const response = parseSingleResponse(
          Buffer.concat(stdout).toString("utf8"),
          childDiagnostics,
        );
        resolvePromise({
          response: response as T,
          diagnostics: childDiagnostics,
        });
      } catch (error) {
        rejectPromise(error);
      }
    });

    child.stdin.on("error", () => {
      // A concurrent exit is reported by the process error/close handlers.
    });
    child.stdin.end(requestLine, "utf8");
  });
}

function positiveLimit(
  value: number | undefined,
  fallback: number,
  name: string,
): number {
  if (value === undefined) {
    return fallback;
  }
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new BridgeError(
      "INVALID_REQUEST",
      `${name} must be a positive integer`,
    );
  }
  return value;
}

function parseSingleResponse(
  stdout: string,
  diagnostics: BridgeDiagnostics,
): JsonObject {
  const lineEndingLength = stdout.endsWith("\r\n")
    ? 2
    : stdout.endsWith("\n")
      ? 1
      : 0;
  const responseText =
    lineEndingLength === 0 ? stdout : stdout.slice(0, -lineEndingLength);
  if (
    lineEndingLength === 0 ||
    responseText.length === 0 ||
    responseText.includes("\n") ||
    responseText.includes("\r")
  ) {
    throw new BridgeError(
      "INVALID_RESPONSE",
      "Python service must emit exactly one JSON line",
      diagnostics,
      { terminationConfirmed: true },
    );
  }
  try {
    const value: unknown = JSON.parse(responseText);
    if (
      value === null ||
      typeof value !== "object" ||
      Array.isArray(value)
    ) {
      throw new TypeError("response is not a JSON object");
    }
    return value as JsonObject;
  } catch (error) {
    throw new BridgeError(
      "INVALID_RESPONSE",
      "Python service emitted malformed JSON",
      diagnostics,
      { cause: error, terminationConfirmed: true },
    );
  }
}
