# TypeScript Backend

This folder contains an Express/TypeScript implementation of the Claude Agent backend.
It mirrors the FastAPI version under `cc_B` while sharing the same configuration file,
database, and session ingestion logic.

## Prerequisites

- Node.js 18+ and npm.
- Access to the Claude Desktop session directory configured in `config.yaml`.
- Your environment must be able to run the Claude Desktop binaries (same requirement
  as the Python backend) and have whatever credentials Claude Desktop needs.

## Setup

```bash
cd ts_backend
npm install
```

The TypeScript sources live in `src/` and compile to `dist/`. At runtime, the server
directly uses the official `@anthropic-ai/claude-agent-sdk` package, so no Python
bridge process is needed.

## Running

- Development (ts-node): `npm run dev`
- Production build: `npm run build && npm start`

Both commands read `../config.yaml`, initialize the same SQLite database, and expose
the identical HTTP API described in `APIdocs.md`. Authentication still uses HTTP Basic
Auth and the stored credentials from the config file.

## Notes

- The service persists sessions and user settings using `better-sqlite3`, sharing
  the existing `sessions.db` file by default.
- Session ingestion scans the Claude Desktop `projects/` directory in the same way
  as the Python version, so you can switch between backends without re-importing.
- The actual Claude Code runtime is handled through `@anthropic-ai/claude-agent-sdk`,
  matching the same behavior and permissions as the Python implementation.
