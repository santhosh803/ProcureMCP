"""Start the ProcureMCP MCP server as a Django management command.

Examples:
    python manage.py run_mcp_server                       # stdio (Claude Desktop)
    python manage.py run_mcp_server --transport sse --port 8001
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run the ProcureMCP MCP server (stdio or SSE transport)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--transport",
            choices=["stdio", "sse", "streamable-http"],
            default="stdio",
        )
        parser.add_argument("--host", default="0.0.0.0")
        parser.add_argument("--port", type=int, default=8001)

    def handle(self, *args, **options):
        # Import here so django.setup() has already run via the command framework.
        from mcp_server.server import mcp

        transport = options["transport"]
        if transport == "stdio":
            self.stderr.write("Starting ProcureMCP MCP server on stdio…")
            mcp.run(transport="stdio")
        else:
            self.stderr.write(
                f"Starting ProcureMCP MCP server on {transport} at "
                f"{options['host']}:{options['port']}…"
            )
            mcp.run(transport=transport, host=options["host"], port=options["port"])
