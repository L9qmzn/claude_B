import fs from "fs";
import path from "path";

import { CLAUDE_PROJECTS_DIR, CLAUDE_ROOT } from "./config";
import { getDb } from "./database";
import {
  Session,
  SessionFileMetadata,
  SessionSummary,
} from "./models";

function dtToStr(value: Date): string {
  return value.toISOString();
}

function strToDate(value: string): Date {
  return new Date(value);
}

function parseIsoTimestamp(raw?: string | null): Date {
  if (!raw) {
    return new Date();
  }
  try {
    if (raw.endsWith("Z")) {
      return new Date(raw);
    }
    return new Date(raw);
  } catch {
    return new Date();
  }
}

function cwdToProjectSlug(cwd: string): string {
  const normalized = path.resolve(cwd);
  return normalized.replace(/[^0-9A-Za-z]/g, "-");
}

function sessionFilePath(cwd: string, sessionId: string): string {
  const slug = cwdToProjectSlug(cwd);
  return path.join(CLAUDE_PROJECTS_DIR, slug, `${sessionId}.jsonl`);
}

export function loadSessionMessagesFromJsonl(
  cwd: string,
  sessionId: string,
): Record<string, unknown>[] {
  const filePath = sessionFilePath(cwd, sessionId);
  if (!fs.existsSync(filePath)) {
    return [];
  }

  const messages: Record<string, unknown>[] = [];
  try {
    const content = fs.readFileSync(filePath, "utf-8");
    for (const rawLine of content.split(/\r?\n/)) {
      const line = rawLine.trim();
      if (!line) {
        continue;
      }
      try {
        messages.push(JSON.parse(line));
      } catch {
        // Skip malformed lines
      }
    }
  } catch {
    return [];
  }

  return messages;
}

export function countSessionMessages(cwd: string, sessionId: string): number {
  const filePath = sessionFilePath(cwd, sessionId);
  if (!fs.existsSync(filePath)) {
    return 0;
  }

  try {
    const content = fs.readFileSync(filePath, "utf-8");
    return content
      .split(/\r?\n/)
      .filter((line) => line.trim().length > 0).length;
  } catch {
    return 0;
  }
}

function isAgentSessionFile(filePath: string): boolean {
  const fileName = path.basename(filePath);
  return fileName.startsWith("agent-") && fileName.endsWith(".jsonl");
}

function* iterSessionFilesFromClaude(root: string): Generator<string> {
  const projectDir = path.join(root, "projects");
  if (!fs.existsSync(projectDir)) {
    return;
  }

  const projects = fs.readdirSync(projectDir, { withFileTypes: true });
  for (const project of projects) {
    if (!project.isDirectory()) {
      continue;
    }
    const projectPath = path.join(projectDir, project.name);
    const files = fs.readdirSync(projectPath, { withFileTypes: true });
    for (const file of files) {
      if (file.isFile() && file.name.endsWith(".jsonl")) {
        yield path.join(projectPath, file.name);
      }
    }
  }
}

function extractSessionMetadataFromFile(filePath: string): SessionFileMetadata | null {
  try {
    const handler = fs.openSync(filePath, "r");
    try {
      const buffer = Buffer.alloc(65536);
      const bytesRead = fs.readSync(handler, buffer, 0, buffer.length, 0);
      if (bytesRead <= 0) {
        return null;
      }
      const content = buffer.subarray(0, bytesRead).toString("utf-8");
      const firstLine = content.split(/\r?\n/).find((line) => line.trim().length > 0);
      if (!firstLine) {
        return null;
      }
      const data = JSON.parse(firstLine);

      const sessionId = (typeof data.session_id === "string" && data.session_id) || path.parse(filePath).name;
      const cwd =
        (typeof data.cwd === "string" && data.cwd) ||
        (typeof data.project_path === "string" && data.project_path);
      if (!cwd) {
        return null;
      }

      const createdAt = parseIsoTimestamp(typeof data.created_at === "string" ? data.created_at : undefined);
      const updatedAt = parseIsoTimestamp(typeof data.updated_at === "string" ? data.updated_at : undefined);

      let title: string =
        (typeof data.title === "string" && data.title) ||
        (data.message && typeof data.message === "object" && typeof data.message.text === "string" && data.message.text) ||
        sessionId;

      if (!title) {
        title = sessionId;
      }

      const parentSessionId =
        typeof data.parent_session_id === "string" ? data.parent_session_id : undefined;

      return {
        session_id: sessionId,
        title,
        cwd,
        created_at: createdAt,
        updated_at: updatedAt,
        parent_session_id: parentSessionId,
        is_agent_run: isAgentSessionFile(filePath),
      };
    } finally {
      fs.closeSync(handler);
    }
  } catch {
    return null;
  }
}

function* discoverSessionMetadataFromFiles(root: string): Generator<SessionFileMetadata> {
  for (const filePath of iterSessionFilesFromClaude(root)) {
    const metadata = extractSessionMetadataFromFile(filePath);
    if (metadata) {
      yield metadata;
    }
  }
}

export function fetchSession(sessionId: string, includeMessages = true): Session | null {
  const row = getDb()
    .prepare(
      "SELECT session_id, title, cwd, created_at, updated_at FROM sessions WHERE session_id = ?",
    )
    .get(sessionId) as Record<string, string> | undefined;

  if (!row) {
    return null;
  }

  let messages: Record<string, unknown>[] = [];
  if (includeMessages) {
    messages = loadSessionMessagesFromJsonl(row.cwd, row.session_id);
  }

  return {
    session_id: row.session_id,
    title: row.title,
    cwd: row.cwd,
    created_at: strToDate(row.created_at),
    updated_at: strToDate(row.updated_at),
    messages,
  };
}

export function listSessionSummaries(): SessionSummary[] {
  const rows = getDb()
    .prepare(
      "SELECT session_id, title, cwd, created_at, updated_at FROM sessions ORDER BY updated_at DESC",
    )
    .all() as Record<string, string>[];

  return rows.map((row) => ({
    session_id: row.session_id,
    title: row.title,
    cwd: row.cwd,
    created_at: strToDate(row.created_at),
    updated_at: strToDate(row.updated_at),
    message_count: countSessionMessages(row.cwd, row.session_id),
  }));
}

export function persistSessionMetadata(params: {
  session_id: string;
  title: string;
  cwd: string;
  created_at: Date;
  updated_at: Date;
}): void {
  getDb()
    .prepare(
      `
      INSERT INTO sessions (session_id, title, cwd, created_at, updated_at)
      VALUES (@session_id, @title, @cwd, @created_at, @updated_at)
      ON CONFLICT(session_id) DO UPDATE SET
        title = excluded.title,
        cwd = excluded.cwd,
        created_at = CASE
          WHEN excluded.created_at < sessions.created_at THEN excluded.created_at
          ELSE sessions.created_at
        END,
        updated_at = CASE
          WHEN excluded.updated_at > sessions.updated_at THEN excluded.updated_at
          ELSE sessions.updated_at
        END
      `,
    )
    .run({
      session_id: params.session_id,
      title: params.title,
      cwd: params.cwd,
      created_at: dtToStr(params.created_at),
      updated_at: dtToStr(params.updated_at),
    });
}

export function persistAgentSessionMetadata(params: {
  agent_id: string;
  parent_session_id?: string | null;
  title: string;
  cwd: string;
  created_at: Date;
  updated_at: Date;
}): void {
  getDb()
    .prepare(
      `
      INSERT INTO agent_sessions (agent_id, parent_session_id, title, cwd, created_at, updated_at)
      VALUES (@agent_id, @parent_session_id, @title, @cwd, @created_at, @updated_at)
      ON CONFLICT(agent_id) DO UPDATE SET
        parent_session_id = COALESCE(excluded.parent_session_id, agent_sessions.parent_session_id),
        title = excluded.title,
        cwd = excluded.cwd,
        created_at = CASE
          WHEN agent_sessions.created_at IS NULL THEN excluded.created_at
          WHEN excluded.created_at < agent_sessions.created_at THEN excluded.created_at
          ELSE agent_sessions.created_at
        END,
        updated_at = CASE
          WHEN agent_sessions.updated_at IS NULL THEN excluded.updated_at
          WHEN excluded.updated_at > agent_sessions.updated_at THEN excluded.updated_at
          ELSE agent_sessions.updated_at
        END
      `,
    )
    .run({
      agent_id: params.agent_id,
      parent_session_id: params.parent_session_id ?? null,
      title: params.title,
      cwd: params.cwd,
      created_at: dtToStr(params.created_at),
      updated_at: dtToStr(params.updated_at),
    });
}

export function bootstrapSessionsFromFiles(claudeDir?: string): { sessions: number; agent_runs: number } {
  const root = claudeDir ? path.resolve(claudeDir) : CLAUDE_ROOT;
  if (!fs.existsSync(root)) {
    throw new Error(`Claude directory does not exist: ${root}`);
  }

  const stats = { sessions: 0, agent_runs: 0 };

  const primarySessions: SessionFileMetadata[] = [];
  const agentSessions: SessionFileMetadata[] = [];

  for (const metadata of discoverSessionMetadataFromFiles(root)) {
    if (metadata.is_agent_run) {
      agentSessions.push(metadata);
    } else {
      primarySessions.push(metadata);
    }
  }

  const existingSessionIds = new Set<string>();
  const rows = getDb().prepare("SELECT session_id FROM sessions").all() as Record<string, string>[];
  for (const row of rows) {
    existingSessionIds.add(row.session_id);
  }

  for (const metadata of primarySessions) {
    persistSessionMetadata({
      session_id: metadata.session_id,
      title: metadata.title,
      cwd: metadata.cwd,
      created_at: metadata.created_at,
      updated_at: metadata.updated_at,
    });
    stats.sessions += 1;
    existingSessionIds.add(metadata.session_id);
  }

  for (const metadata of agentSessions) {
    const parentSessionId =
      metadata.parent_session_id && existingSessionIds.has(metadata.parent_session_id)
        ? metadata.parent_session_id
        : null;
    persistAgentSessionMetadata({
      agent_id: metadata.session_id,
      parent_session_id: parentSessionId,
      title: metadata.title,
      cwd: metadata.cwd,
      created_at: metadata.created_at,
      updated_at: metadata.updated_at,
    });
    stats.agent_runs += 1;
  }

  return stats;
}
