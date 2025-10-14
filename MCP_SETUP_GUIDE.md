# MCP Server Setup Guide - CORRECTED

This document provides accurate, tested instructions for setting up the Messages RAG MCP server.

## Quick Start

### 1. Start the MCP Server

```bash
# For remote access (recommended)
uv run python run_mcp_server.py --transport http --host 0.0.0.0 --port 8766

# For local Claude Desktop only
uv run python run_mcp_server.py --transport stdio
```

### 2. Test Server Functionality

```bash
# Test the server (basic functionality)
uv run python test_mcp.py

# Test with real data
uv run python test_mcp_real_data.py
```

## Claude Desktop Integration

### Option 1: Local Integration (Recommended)

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "memories-rag": {
      "command": "uv",
      "args": [
        "run", "python", "$HOME/path/to/memory-database/run_mcp_server.py",
        "--transport", "stdio"
      ],
      "cwd": "$HOME/path/to/memory-database"
    }
  }
}
```

### Option 2: Remote Integration (via mcp-remote)

First, start the HTTP server:
```bash
uv run python run_mcp_server.py --transport http --port 8766
```

Then configure Claude Desktop:
```json
{
  "mcpServers": {
    "memories-rag-remote": {
      "command": "npx",
      "args": [
        "-y", 
        "mcp-remote", 
        "http://localhost:8766/mcp"
      ]
    }
  }
}
```

## Manual HTTP Testing

### Proper MCP Client Flow

1. **Initialize connection:**
```bash
curl -v -H "Accept: application/json, text/event-stream" \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test-client", "version": "1.0.0"}}}' \
     http://localhost:8766/mcp
```

2. **Extract session ID from response headers** (look for `mcp-session-id`)

3. **Send initialized notification:**
```bash
curl -s -H "Accept: application/json, text/event-stream" \
     -H "Content-Type: application/json" \
     -H "mcp-session-id: YOUR_SESSION_ID" \
     -d '{"jsonrpc": "2.0", "method": "notifications/initialized"}' \
     http://localhost:8766/mcp
```

4. **List available tools:**
```bash
curl -s -H "Accept: application/json, text/event-stream" \
     -H "Content-Type: application/json" \
     -H "mcp-session-id: YOUR_SESSION_ID" \
     -d '{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}' \
     http://localhost:8766/mcp
```

5. **Use tools:**
```bash
curl -s -H "Accept: application/json, text/event-stream" \
     -H "Content-Type: application/json" \
     -H "mcp-session-id: YOUR_SESSION_ID" \
     -d '{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "search_person", "arguments": {"email": "test@example.com"}}}' \
     http://localhost:8766/mcp
```

## Available Tools

### search_person
Find people using any combination of identifiers:
- `email`: Email address (lowercase normalized)
- `phone`: Phone number (E.164 format like +14155551234)
- `name`: Display name (supports fuzzy matching)
- `username`: Platform username
- `contact_id`: Platform-specific contact ID
- `fuzzy_match`: Enable fuzzy name matching (boolean)

### search_messages
Search messages for specific people:
- Person identification: `person_id`, `person_email`, `person_phone`, or `person_name`
- Filters: `date_from`, `date_to`, `content_contains`, `platform`
- Options: `include_attachments`, `limit`

## Important Notes

### FastMCP vs Pure MCP
- This server uses **FastMCP**, not pure MCP specification
- FastMCP adds session management and requires specific headers
- It implements "Streamable HTTP" transport, not standard MCP SSE
- Works with MCP clients but requires FastMCP-specific initialization

### Transport Options
- **http/streamable-http**: Same thing in FastMCP, uses `/mcp` endpoint
- **sse**: Legacy transport, uses `/sse` + `/messages/?session_id=X` pattern
- **stdio**: Standard input/output for local integration

### Common Issues
1. **"Missing session ID"**: Send `initialize` first, then `notifications/initialized`
2. **"Not Acceptable"**: Include both `application/json` and `text/event-stream` in Accept header
3. **"Invalid parameters"**: Check tool parameter names and types

## Troubleshooting

### Check Server Status
```bash
# See if server is running
ps aux | grep run_mcp_server

# Check port usage
lsof -i :8766

# Test basic connectivity
curl http://localhost:8765/mcp
```

### Debug Mode
```bash
uv run python run_mcp_server.py --transport http --log-level DEBUG
```

### Restart Claude Desktop
After changing configuration, restart Claude Desktop completely.

## Security Notes

- HTTP server binds to `0.0.0.0` by default (accessible from network)
- No built-in authentication (add reverse proxy if needed)
- For remote access, consider SSH tunneling or VPN
- Database credentials are read from `.env` file