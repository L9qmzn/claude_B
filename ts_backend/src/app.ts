import crypto from "crypto";
import express, { NextFunction, Request, Response } from "express";
import fs from "fs";
import path from "path";
import {
  query,
  type Options as ClaudeAgentOptions,
  type SDKMessage,
} from "@anthropic-ai/claude-agent-sdk";
import type { ThreadOptions } from "@openai/codex-sdk";

import {
  CLAUDE_ROOT,
  CODEX_API_KEY,
  CODEX_CLI_PATH,
  CODEX_SESSIONS_DIR,
  USER_CREDENTIALS,
} from "./config";
import { initDb } from "./database";
import {
  bootstrapSessionsFromFiles,
  fetchSession,
  listSessionSummaries,
  persistSessionMetadata,
} from "./sessionStore";
import {
  bootstrapCodexSessionsFromFiles,
  fetchCodexSession,
  listCodexSessionSummaries,
  persistCodexSessionMetadata,
} from "./codexSessionStore";
import { AnyRecord, formatSse, logSdkMessage, serializeSdkMessage } from "./streaming";
import {
  ChatRequest,
  CodexChatRequest,
  CodexUserSettings,
  CodexUserSettingsRequest,
  PermissionMode,
  Session,
  UserSettings,
  defaultCodexUserSettings,
  defaultSystemPrompt,
} from "./models";
import { fetchUserSettings, upsertUserSettings } from "./userSettingsStore";
import {
  fetchCodexUserSettings,
  upsertCodexUserSettings,
} from "./codexUserSettingsStore";

declare global {
  namespace Express {
    interface Request {
      userId?: string;
    }
  }
}

const VALID_PERMISSION_MODES: PermissionMode[] = [
  "default",
  "plan",
  "acceptEdits",
  "bypassPermissions",
];

type CodexModule = typeof import("@openai/codex-sdk");
type CodexClientInstance = InstanceType<CodexModule["Codex"]>;
let codexClientPromise: Promise<CodexClientInstance> | null = null;

const dynamicImport = new Function("specifier", "return import(specifier);") as (
  specifier: string,
) => Promise<CodexModule>;

async function getCodexClient(): Promise<CodexClientInstance> {
  if (!codexClientPromise) {
    codexClientPromise = dynamicImport("@openai/codex-sdk").then(({ Codex }) => {
      return new Codex({
        apiKey: CODEX_API_KEY || undefined,
        codexPathOverride: CODEX_CLI_PATH || undefined,
      });
    });
  }
  return codexClientPromise;
}

type SystemInitMessage = Extract<SDKMessage, { type: "system"; subtype: "init" }>;
type AssistantSdkMessage = Extract<SDKMessage, { type: "assistant" }>;
type ResultSdkMessage = Extract<SDKMessage, { type: "result" }>;

function safeCompare(a: string, b: string): boolean {
  const bufferA = Buffer.from(a);
  const bufferB = Buffer.from(b);
  if (bufferA.length !== bufferB.length) {
    return false;
  }
  return crypto.timingSafeEqual(bufferA, bufferB);
}

function decodeBasicAuth(header?: string): { username: string; password: string } | null {
  if (!header || !header.startsWith("Basic ")) {
    return null;
  }
  try {
    const decoded = Buffer.from(header.slice("Basic ".length), "base64").toString("utf-8");
    const index = decoded.indexOf(":");
    if (index === -1) {
      return null;
    }
    return {
      username: decoded.slice(0, index),
      password: decoded.slice(index + 1),
    };
  } catch {
    return null;
  }
}

function basicAuthMiddleware(req: Request, res: Response, next: NextFunction): void {
  const credentials = decodeBasicAuth(req.headers.authorization);
  if (!credentials) {
    res.setHeader("WWW-Authenticate", "Basic");
    res.status(401).json({ detail: "Unauthorized" });
    return;
  }

  const storedPassword = USER_CREDENTIALS[credentials.username];
  if (!storedPassword || !safeCompare(credentials.password, storedPassword)) {
    res.setHeader("WWW-Authenticate", "Basic");
    res.status(401).json({ detail: "Unauthorized" });
    return;
  }

  req.userId = credentials.username;
  next();
}

function ensureSameUser(req: Request, res: Response, userId: string): boolean {
  if (req.userId !== userId) {
    res.status(403).json({ detail: "Forbidden" });
    return false;
  }
  return true;
}

function normalizePermissionMode(value?: string): PermissionMode {
  if (!value) {
    return "default";
  }
  if (VALID_PERMISSION_MODES.includes(value as PermissionMode)) {
    return value as PermissionMode;
  }
  return "default";
}

function deriveSessionTitle(message: string): string {
  const fallback = "新会话";
  const trimmed = message.trim() || fallback;
  if (trimmed.length <= 30) {
    return trimmed;
  }
  return `${trimmed.slice(0, 30)}...`;
}

function isTextBlock(block: unknown): block is { type: string; text: string } {
  return Boolean(
    block &&
      typeof block === "object" &&
      (block as AnyRecord).type === "text" &&
      typeof (block as AnyRecord).text === "string",
  );
}

function buildCodexThreadOptions(body: CodexChatRequest, cwd: string): ThreadOptions {
  return {
    workingDirectory: cwd,
    approvalPolicy: body.approval_policy,
    sandboxMode: body.sandbox_mode,
    skipGitRepoCheck: body.skip_git_repo_check,
    model: body.model,
    modelReasoningEffort: body.model_reasoning_effort,
    networkAccessEnabled: body.network_access_enabled,
    webSearchEnabled: body.web_search_enabled,
  };
}

function emitCodexAgentChunk(
  itemId: string,
  text: string,
  assistantChunks: string[],
  writeEvent: (event: string, payload: AnyRecord) => void,
  sessionId: string | null,
  progressMap: Map<string, string>,
): void {
  const previous = progressMap.get(itemId) ?? "";
  const chunk = text.slice(previous.length);
  if (chunk) {
    assistantChunks.push(chunk);
    writeEvent("token", {
      session_id: sessionId,
      text: chunk,
    });
  }
  progressMap.set(itemId, text);
}

function extractSessionIdFromPayload(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") {
    return null;
  }

  const stack: AnyRecord[] = [payload as AnyRecord];
  const seen = new Set<AnyRecord>();

  while (stack.length > 0) {
    const current = stack.pop();
    if (!current || seen.has(current)) {
      continue;
    }
    seen.add(current);

    const value =
      (typeof current.session_id === "string" && current.session_id) ||
      (typeof current.sessionId === "string" && current.sessionId);
    if (value) {
      return value;
    }

    for (const child of Object.values(current)) {
      if (child && typeof child === "object") {
        if (Array.isArray(child)) {
          for (const item of child) {
            if (item && typeof item === "object") {
              stack.push(item as AnyRecord);
            }
          }
        } else {
          stack.push(child as AnyRecord);
        }
      }
    }
  }

  return null;
}

function isSystemInitMessage(message: SDKMessage): message is SystemInitMessage {
  return message.type === "system" && (message as { subtype?: string }).subtype === "init";
}

function isAssistantMessage(message: SDKMessage): message is AssistantSdkMessage {
  return message.type === "assistant";
}

function isResultMessage(message: SDKMessage): message is ResultSdkMessage {
  return message.type === "result";
}

function ensureDirectory(pathValue: string): void {
  const stats = fs.statSync(pathValue, { throwIfNoEntry: false });
  if (!stats || !stats.isDirectory()) {
    throw new Error(`cwd does not exist or is not a directory: ${pathValue}`);
  }
}

export function createApp(): express.Express {
  initDb();
  try {
    bootstrapSessionsFromFiles();
  } catch (error) {
    // eslint-disable-next-line no-console
    console.warn("Failed to bootstrap sessions:", error);
  }
  try {
    bootstrapCodexSessionsFromFiles();
  } catch (error) {
    // eslint-disable-next-line no-console
    console.warn("Failed to bootstrap Codex sessions:", error);
  }

  const app = express();
  app.use(express.json({ limit: "1mb" }));
  app.use(basicAuthMiddleware);

  app.get("/sessions", (_req, res) => {
    res.json(listSessionSummaries());
  });

  app.get("/sessions/:sessionId", (req, res) => {
    const session = fetchSession(req.params.sessionId, true);
    if (!session) {
      res.status(404).json({ detail: "Session not found" });
      return;
    }
    res.json(session);
  });

  app.post("/sessions/load", (req, res) => {
    const body = req.body as { claude_dir?: string } | undefined;
    try {
      const stats = bootstrapSessionsFromFiles(body?.claude_dir);
      const resolvedRoot = body?.claude_dir ? path.resolve(body.claude_dir) : CLAUDE_ROOT;
      res.json({
        claude_dir: resolvedRoot,
        sessions_loaded: stats.sessions,
        agent_runs_loaded: stats.agent_runs,
      });
    } catch (error) {
      res.status(400).json({ detail: error instanceof Error ? error.message : String(error) });
    }
  });

  app.get("/users/:userId/settings", (req, res) => {
    const { userId } = req.params;
    if (!ensureSameUser(req, res, userId)) {
      return;
    }
    const settings = fetchUserSettings(userId);
    if (!settings) {
      const defaults: UserSettings = {
        user_id: userId,
        permission_mode: "default",
        system_prompt: defaultSystemPrompt(),
      };
      res.json(defaults);
      return;
    }
    res.json(settings);
  });

  app.put("/users/:userId/settings", (req, res) => {
    const { userId } = req.params;
    if (!ensureSameUser(req, res, userId)) {
      return;
    }
    const body = req.body as UserSettings | undefined;
    const permissionMode = normalizePermissionMode(body?.permission_mode);
    const hasSystemPromptField =
      !!body && Object.prototype.hasOwnProperty.call(body, "system_prompt");
    const systemPrompt = hasSystemPromptField ? body?.system_prompt ?? null : defaultSystemPrompt();

    const settings = upsertUserSettings({
      user_id: userId,
      permission_mode: permissionMode,
      system_prompt: systemPrompt,
    });

    res.json(settings);
  });

  app.get("/codex/sessions", (_req, res) => {
    res.json(listCodexSessionSummaries());
  });

  app.get("/codex/sessions/:sessionId", (req, res) => {
    const session = fetchCodexSession(req.params.sessionId, true);
    if (!session) {
      res.status(404).json({ detail: "Session not found" });
      return;
    }
    res.json(session);
  });

  app.post("/codex/sessions/load", (req, res) => {
    const body = req.body as { codex_dir?: string } | undefined;
    try {
      const stats = bootstrapCodexSessionsFromFiles(body?.codex_dir);
      const resolvedRoot = body?.codex_dir ? path.resolve(body.codex_dir) : CODEX_SESSIONS_DIR;
      res.json({
        codex_dir: resolvedRoot,
        sessions_loaded: stats.sessions,
      });
    } catch (error) {
      res.status(400).json({ detail: error instanceof Error ? error.message : String(error) });
    }
  });

  app.get("/codex/users/:userId/settings", (req, res) => {
    const { userId } = req.params;
    if (!ensureSameUser(req, res, userId)) {
      return;
    }
    const settings = fetchCodexUserSettings(userId);
    if (!settings) {
      res.json(defaultCodexUserSettings(userId));
      return;
    }
    res.json(settings);
  });

  app.put("/codex/users/:userId/settings", (req, res) => {
    const { userId } = req.params;
    if (!ensureSameUser(req, res, userId)) {
      return;
    }
    const body = (req.body ?? {}) as CodexUserSettingsRequest;
    const sanitized: CodexUserSettingsRequest = {
      approval_policy: body.approval_policy,
      sandbox_mode: body.sandbox_mode,
      model: body.model,
      model_reasoning_effort: body.model_reasoning_effort,
      network_access_enabled: body.network_access_enabled,
      web_search_enabled: body.web_search_enabled,
      skip_git_repo_check: body.skip_git_repo_check,
    };

    const settings: CodexUserSettings = upsertCodexUserSettings({
      user_id: userId,
      settings: sanitized,
    });

    res.json(settings);
  });

  app.post("/chat", (req, res) => {
    const body = req.body as ChatRequest | undefined;
    if (!body || typeof body.message !== "string" || !body.message.trim()) {
      res.status(400).json({ detail: "message is required" });
      return;
    }

    const permissionMode = normalizePermissionMode(body.permission_mode);
    const hasSystemPrompt =
      body && Object.prototype.hasOwnProperty.call(body, "system_prompt");
    const systemPrompt = hasSystemPrompt ? body.system_prompt ?? null : defaultSystemPrompt();
    const now = new Date();
    const isNewSession = !body.session_id;

    let existingSession: Session | null = null;
    let finalCwd: string;

    if (isNewSession) {
      if (!body.cwd) {
        res.status(400).json({ detail: "cwd is required when starting a new session" });
        return;
      }
      const resolvedCwd = path.resolve(body.cwd);
      try {
        ensureDirectory(resolvedCwd);
      } catch (error) {
        res.status(400).json({ detail: error instanceof Error ? error.message : String(error) });
        return;
      }
      finalCwd = resolvedCwd;
    } else {
      existingSession = fetchSession(body.session_id!, false);
      if (!existingSession) {
        res.status(404).json({ detail: "Session not found" });
        return;
      }
      if (body.cwd && path.resolve(body.cwd) !== path.resolve(existingSession.cwd)) {
        res.status(400).json({ detail: "cwd mismatch for existing session" });
        return;
      }
      finalCwd = existingSession.cwd;
    }

    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    if (typeof res.flushHeaders === "function") {
      res.flushHeaders();
    }

    const userMessageText = body.message;
    const newSessionTitle = deriveSessionTitle(userMessageText);
    const assistantChunks: string[] = [];
    let sessionId: string | null = body.session_id ?? null;
    const isNewSessionFlag = isNewSession;

    const writeEvent = (event: string, payload: AnyRecord) => {
      res.write(formatSse(event, payload));
    };

    const abortController = new AbortController();
    const options: ClaudeAgentOptions = {
      resume: body.session_id ?? undefined,
      cwd: finalCwd,
      includePartialMessages: true,
      settingSources: ["user"],
      permissionMode,
      systemPrompt: (systemPrompt ?? undefined) as any,
      abortController,
    };

    let streamClosed = false;
    const abortStreaming = () => {
      if (streamClosed) {
        return;
      }
      streamClosed = true;
      abortController.abort();
    };
    req.on("aborted", abortStreaming);
    res.on("close", () => {
      if (!res.writableEnded) {
        abortStreaming();
      }
    });

    (async () => {
      try {
        const stream = query({
          prompt: userMessageText,
          options,
        });

        // eslint-disable-next-line no-restricted-syntax
        for await (const message of stream) {
          if (streamClosed) {
            break;
          }

          const rawPayload = serializeSdkMessage(message);
          if (rawPayload) {
            logSdkMessage(message.type, rawPayload);
          }

          if (isSystemInitMessage(message)) {
            if (!sessionId) {
              sessionId = message.session_id;
              if (sessionId && isNewSessionFlag) {
                persistSessionMetadata({
                  session_id: sessionId,
                  title: newSessionTitle,
                  cwd: finalCwd,
                  created_at: now,
                  updated_at: now,
                });
              }
              writeEvent("session", {
                session_id: sessionId,
                cwd: finalCwd,
                is_new: true,
              });
            } else {
              writeEvent("session", {
                session_id: sessionId,
                cwd: finalCwd,
                is_new: false,
              });
            }
          }

          if (isAssistantMessage(message)) {
            const blocks = Array.isArray(message.message?.content)
              ? message.message.content
              : [];
            for (const block of blocks) {
              if (isTextBlock(block)) {
                const chunk = block.text;
                if (chunk) {
                  assistantChunks.push(chunk);
                  writeEvent("token", {
                    session_id: sessionId,
                    text: chunk,
                  });
                }
              }
            }
          }

          if (isResultMessage(message)) {
            if (!sessionId) {
              sessionId = message.session_id;
              writeEvent("session", {
                session_id: sessionId,
                cwd: finalCwd,
                is_new: isNewSessionFlag,
              });
            }
            if (
              "result" in message &&
              typeof message.result === "string" &&
              message.result &&
              assistantChunks.length === 0
            ) {
              assistantChunks.push(message.result);
            }
          }

          if (rawPayload) {
            const payloadSessionId = extractSessionIdFromPayload(rawPayload) ?? sessionId;
            if (!sessionId && payloadSessionId) {
              sessionId = payloadSessionId;
            }
            writeEvent("message", {
              session_id: payloadSessionId,
              payload: rawPayload,
            });
          }
        }

        if (!sessionId) {
          throw new Error("Claude did not return session_id");
        }

        const title = existingSession ? existingSession.title : newSessionTitle;
        persistSessionMetadata({
          session_id: sessionId,
          title,
          cwd: finalCwd,
          created_at: now,
          updated_at: now,
        });

        writeEvent("done", {
          session_id: sessionId,
          cwd: finalCwd,
          length: assistantChunks.join("").length,
        });
      } catch (error) {
        writeEvent("error", {
          message: error instanceof Error ? error.message : String(error),
        });
      } finally {
        res.end();
      }
    })();
  });

  app.post("/codex/chat", (req, res) => {
    const body = req.body as CodexChatRequest | undefined;
    if (!body || typeof body.message !== "string" || !body.message.trim()) {
      res.status(400).json({ detail: "message is required" });
      return;
    }

    const now = new Date();
    const isNewSession = !body.session_id;

    let existingSession: Session | null = null;
    let finalCwd: string;

    if (isNewSession) {
      if (!body.cwd) {
        res.status(400).json({ detail: "cwd is required when starting a new session" });
        return;
      }
      const resolvedCwd = path.resolve(body.cwd);
      try {
        ensureDirectory(resolvedCwd);
      } catch (error) {
        res.status(400).json({ detail: error instanceof Error ? error.message : String(error) });
        return;
      }
      finalCwd = resolvedCwd;
    } else {
      existingSession = fetchCodexSession(body.session_id!, false);
      if (!existingSession) {
        res.status(404).json({ detail: "Session not found" });
        return;
      }
      if (body.cwd && path.resolve(body.cwd) !== path.resolve(existingSession.cwd)) {
        res.status(400).json({ detail: "cwd mismatch for existing session" });
        return;
      }
      finalCwd = existingSession.cwd;
    }

    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    if (typeof res.flushHeaders === "function") {
      res.flushHeaders();
    }

    const userSettings =
      req.userId !== undefined ? fetchCodexUserSettings(req.userId) : null;
    const mergedRequest: CodexChatRequest = {
      ...body,
      approval_policy: body.approval_policy ?? userSettings?.approval_policy,
      sandbox_mode: body.sandbox_mode ?? userSettings?.sandbox_mode,
      skip_git_repo_check: body.skip_git_repo_check ?? userSettings?.skip_git_repo_check,
      model: body.model ?? userSettings?.model,
      model_reasoning_effort: body.model_reasoning_effort ?? userSettings?.model_reasoning_effort,
      network_access_enabled: body.network_access_enabled ?? userSettings?.network_access_enabled,
      web_search_enabled: body.web_search_enabled ?? userSettings?.web_search_enabled,
    };

    const userMessageText = body.message;
    const newSessionTitle = deriveSessionTitle(userMessageText);
    const assistantChunks: string[] = [];
    const agentMessageProgress = new Map<string, string>();
    let sessionId: string | null = body.session_id ?? null;
    const isNewSessionFlag = isNewSession;

    const writeEvent = (event: string, payload: AnyRecord) => {
      res.write(formatSse(event, payload));
    };

    const threadOptions = buildCodexThreadOptions(mergedRequest, finalCwd);

    let streamClosed = false;
    res.on("close", () => {
      streamClosed = true;
    });

    (async () => {
      try {
        const codex = await getCodexClient();
        const thread = isNewSession
          ? codex.startThread(threadOptions)
          : codex.resumeThread(sessionId!, threadOptions);
        const resolveThreadId = () => {
          const id = thread.id;
          return typeof id === "string" && id ? id : null;
        };

        if (!isNewSession && sessionId) {
          writeEvent("session", {
            session_id: sessionId,
            cwd: finalCwd,
            is_new: false,
          });
        }

        const { events } = await thread.runStreamed(userMessageText);

        // eslint-disable-next-line no-restricted-syntax
        for await (const event of events) {
          if (streamClosed) {
            break;
          }

          if (event.type === "thread.started") {
            if (!sessionId) {
              sessionId = event.thread_id;
              if (sessionId && isNewSessionFlag) {
                persistCodexSessionMetadata({
                  session_id: sessionId,
                  title: newSessionTitle,
                  cwd: finalCwd,
                  created_at: now,
                  updated_at: now,
                });
              }
            }
            writeEvent("session", {
              session_id: sessionId,
              cwd: finalCwd,
              is_new: isNewSessionFlag,
            });
          }

          if (event.type === "turn.failed") {
            throw new Error(event.error?.message ?? "Codex turn failed");
          }

          if (event.type === "error") {
            throw new Error(event.message ?? "Codex stream error");
          }

          if (event.type === "item.updated" || event.type === "item.completed") {
            const item = event.item as AnyRecord | undefined;
            if (item && item.type === "agent_message" && typeof item.id === "string") {
              if (typeof item.text === "string") {
                emitCodexAgentChunk(
                  item.id,
                  item.text,
                  assistantChunks,
                  writeEvent,
                  sessionId,
                  agentMessageProgress,
                );
              }
              if (event.type === "item.completed") {
                agentMessageProgress.delete(item.id);
              }
            }
          }

          writeEvent("message", {
            session_id: sessionId,
            payload: event,
          });
        }

        if (!sessionId) {
          sessionId = resolveThreadId();
          if (sessionId) {
            if (isNewSessionFlag) {
              persistCodexSessionMetadata({
                session_id: sessionId,
                title: newSessionTitle,
                cwd: finalCwd,
                created_at: now,
                updated_at: now,
              });
            }
            writeEvent("session", {
              session_id: sessionId,
              cwd: finalCwd,
              is_new: isNewSessionFlag,
            });
          } else {
            throw new Error("Codex did not return session_id");
          }
        }

        const title = existingSession ? existingSession.title : newSessionTitle;
        const createdAt = existingSession ? existingSession.created_at : now;

        persistCodexSessionMetadata({
          session_id: sessionId,
          title,
          cwd: finalCwd,
          created_at: createdAt,
          updated_at: now,
        });

        writeEvent("done", {
          session_id: sessionId,
          cwd: finalCwd,
          length: assistantChunks.join("").length,
        });
      } catch (error) {
        writeEvent("error", {
          message: error instanceof Error ? error.message : String(error),
        });
      } finally {
        res.end();
      }
    })();
  });

  return app;
}
