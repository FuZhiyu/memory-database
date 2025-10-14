#!/usr/bin/env python3
"""
Run the MCP server for the Messages RAG system.

This script runs the FastMCP server with Streamable HTTP transport for remote access.
FastMCP implements its own session-based protocol layer on top of the MCP specification.

Transport options:
- stdio: For local Claude Desktop integration
- http/streamable-http: For remote access via mcp-remote or direct HTTP clients
- sse: Legacy SSE transport (deprecated, use http instead)
"""

import os
import sys
import argparse
import structlog

# Ensure project root is on sys.path so `src` is importable
PROJECT_ROOT = os.path.dirname(__file__)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

def setup_logging(level: str = "INFO"):
    """Set up structured logging."""
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer()
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(structlog, level.upper(), 20)
        ),
        logger_factory=structlog.WriteLoggerFactory(),
        cache_logger_on_first_use=True,
    )

def main():
    parser = argparse.ArgumentParser(description="Run MCP server for Messages RAG")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8766, help="Port to bind to")
    parser.add_argument("--transport", default="http", choices=["stdio", "sse", "http", "streamable-http"], 
                       help="Transport type: http (recommended), stdio (Claude Desktop local), sse (legacy)")
    parser.add_argument("--log-level", default="INFO", 
                       choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                       help="Log level")
    
    args = parser.parse_args()
    
    setup_logging(args.log_level)
    logger = structlog.get_logger()
    
    logger.info("Starting MCP server",
                host=args.host,
                port=args.port,
                transport=args.transport)

    # Authentication is optional - runs without token for personal use
    # if args.transport in {"http", "streamable-http"} and not os.getenv("MEMORY_DB_HTTP_TOKEN"):
    #     logger.error(
    #         "HTTP transports require MEMORY_DB_HTTP_TOKEN to be set."
    #         " Refusing to start without authentication."
    #     )
    #     sys.exit(1)

    try:
        from memory_database.mcp_server.server import mcp
        # Log loaded tools before starting server to aid troubleshooting
        try:
            import asyncio
            tools = asyncio.run(mcp.get_tools())
            # FastMCP returns a dict name->tool
            tool_names = sorted(list(tools.keys())) if isinstance(tools, dict) else []
            logger.info("MCP tools loaded", tools=tool_names)
        except Exception as _e:  # non-fatal
            logger.debug("Could not enumerate tools before start", error=str(_e))
        
        if args.transport == "stdio":
            mcp.run(transport="stdio")
        elif args.transport == "http":
            mcp.run(transport="http", host=args.host, port=args.port)
        elif args.transport == "streamable-http":
            mcp.run(transport="streamable-http", host=args.host, port=args.port)
        else:
            mcp.run(transport="sse", host=args.host, port=args.port)
            
    except Exception as e:
        logger.error("Failed to start MCP server", error=str(e))
        sys.exit(1)

if __name__ == "__main__":
    main()
