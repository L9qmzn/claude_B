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
