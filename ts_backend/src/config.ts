import fs from "fs";
import os from "os";
import path from "path";
import YAML from "yaml";

export type UserCredentials = Record<string, string>;

export interface AppConfig {
  claude_dir: string;
  sessions_db: string;
  port?: number | string;
  users: UserCredentials;
}

const PROJECT_ROOT = path.resolve(__dirname, "..", "..");
const CONFIG_PATH = path.join(PROJECT_ROOT, "config.yaml");
const DEFAULT_USER_CREDENTIALS: UserCredentials = { admin: "642531" };

function iterCandidateClaudeDirs(): string[] {
  const home = os.homedir();
  const candidates: string[] = [];

  const envValues = [process.env.CLAUDE_DIR, process.env.CLAUDE_HOME];
  for (const value of envValues) {
    if (value) {
      candidates.push(path.resolve(value));
    }
  }

  candidates.push(path.join(home, ".claude"));

  const platform = process.platform;
  if (platform === "darwin") {
    candidates.push(path.join(home, "Library", "Application Support", "Claude"));
  } else if (platform === "win32") {
    const appdata = process.env.APPDATA;
    if (appdata) {
      candidates.push(path.join(appdata, "Claude"));
    }
    const localappdata = process.env.LOCALAPPDATA;
    if (localappdata) {
      candidates.push(path.join(localappdata, "Claude"));
    }
    candidates.push(path.join(home, "AppData", "Roaming", "Claude"));
    candidates.push(path.join(home, "AppData", "Local", "Claude"));
  } else {
    const xdgDataHome = process.env.XDG_DATA_HOME;
    if (xdgDataHome) {
      candidates.push(path.join(xdgDataHome, "claude"));
    }
  }

  return candidates;
}

function detectClaudeDir(): string {
  const fallback = path.join(os.homedir(), ".claude");
  const seen = new Set<string>();

  for (const candidate of iterCandidateClaudeDirs()) {
    const resolved = path.resolve(candidate);
    if (seen.has(resolved)) {
      continue;
    }
    seen.add(resolved);

    if (fs.existsSync(resolved) || fs.existsSync(path.join(resolved, "projects"))) {
      return resolved;
    }
  }

  return fallback;
}

export function loadAppConfig(): AppConfig {
  const defaults: AppConfig = {
    claude_dir: "",
    sessions_db: path.join(PROJECT_ROOT, "sessions.db"),
    users: { ...DEFAULT_USER_CREDENTIALS },
  };

  let loaded: unknown = {};
  try {
    const content = fs.readFileSync(CONFIG_PATH, "utf-8");
    loaded = YAML.parse(content) ?? {};
  } catch (error) {
    loaded = {};
  }

  const config: AppConfig = { ...defaults };
  if (typeof loaded === "object" && loaded !== null) {
    for (const [key, value] of Object.entries(loaded)) {
      if (key === "users" && value && typeof value === "object") {
        const sanitized: UserCredentials = {};
        for (const [username, password] of Object.entries(value as Record<string, unknown>)) {
          if (typeof username === "string" && typeof password === "string") {
            const normalized = username.trim();
            if (normalized) {
              sanitized[normalized] = password;
            }
          }
        }
        if (Object.keys(sanitized).length > 0) {
          config.users = sanitized;
        }
        continue;
      }

      if (typeof key === "string" && typeof value === "string" && value.trim()) {
        const trimmed = value.trim();
        if (key === "claude_dir") {
          config.claude_dir = trimmed;
        } else if (key === "sessions_db") {
          config.sessions_db = trimmed;
        } else if (key === "port") {
          config.port = trimmed;
        }
      } else if (typeof key === "string" && typeof value === "number" && key === "port") {
        config.port = value;
      }
    }
  }

  if (!config.claude_dir) {
    config.claude_dir = detectClaudeDir();
  }

  return config;
}

export const CONFIG = loadAppConfig();
export const CLAUDE_ROOT = path.resolve(CONFIG.claude_dir);
export const CLAUDE_PROJECTS_DIR = path.join(CLAUDE_ROOT, "projects");

const sessionsDb = CONFIG.sessions_db;
const dbPath = path.isAbsolute(sessionsDb)
  ? sessionsDb
  : path.resolve(path.dirname(CONFIG_PATH), sessionsDb);

export const DB_PATH = dbPath;
export const USER_CREDENTIALS = CONFIG.users;
