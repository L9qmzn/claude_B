import type {
  ApprovalMode,
  ModelReasoningEffort,
  SandboxMode,
} from "@openai/codex-sdk";

export type PermissionMode = "default" | "plan" | "acceptEdits" | "bypassPermissions";

export type SystemPrompt = string | Record<string, unknown> | null;

export interface Session {
  session_id: string;
  title: string;
  cwd: string;
  created_at: Date;
  updated_at: Date;
  messages: Record<string, unknown>[];
}

export interface SessionSummary {
  session_id: string;
  title: string;
  cwd: string;
  created_at: Date;
  updated_at: Date;
  message_count: number;
}

export interface ChatRequest {
  session_id?: string;
  cwd?: string;
  message: string;
  permission_mode?: PermissionMode;
  system_prompt?: SystemPrompt;
}

export interface CodexChatRequest {
  session_id?: string;
  cwd?: string;
  message: string;
  approval_policy?: ApprovalMode;
  sandbox_mode?: SandboxMode;
  skip_git_repo_check?: boolean;
  model?: string;
  model_reasoning_effort?: ModelReasoningEffort;
  network_access_enabled?: boolean;
  web_search_enabled?: boolean;
}

export interface LoadSessionsRequest {
  claude_dir?: string;
}

export interface UserSettingsRequest {
  permission_mode: PermissionMode;
  system_prompt?: SystemPrompt;
}

export interface UserSettings extends UserSettingsRequest {
  user_id: string;
}

export interface CodexUserSettingsRequest {
  approval_policy?: ApprovalMode;
  sandbox_mode?: SandboxMode;
  model?: string;
  model_reasoning_effort?: ModelReasoningEffort;
  network_access_enabled?: boolean;
  web_search_enabled?: boolean;
  skip_git_repo_check?: boolean;
}

export interface CodexUserSettings extends CodexUserSettingsRequest {
  user_id: string;
}

export interface SessionFileMetadata {
  session_id: string;
  title: string;
  cwd: string;
  created_at: Date;
  updated_at: Date;
  parent_session_id?: string | null;
  is_agent_run: boolean;
}

export function defaultSystemPrompt(): Record<string, string> {
  return { type: "preset", preset: "claude_code" };
}

export function defaultCodexUserSettings(userId: string): CodexUserSettings {
  return {
    user_id: userId,
    approval_policy: "on-request",
    sandbox_mode: "read-only",
    model_reasoning_effort: "medium",
    network_access_enabled: false,
    web_search_enabled: false,
    skip_git_repo_check: false,
  };
}
