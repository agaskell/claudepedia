#!/usr/bin/env python3
"""
Thin wrapper to run the claudepedia-mcp server.
Installs and runs via uvx if the package isn't already available.
"""
import subprocess
import sys

def main():
    # Try running via uvx (handles installation automatically)
    try:
        subprocess.run(["uvx", "claudepedia-mcp"], check=True)
    except FileNotFoundError:
        # Fall back to direct import if uvx not available but package is installed
        try:
            from claudepedia_mcp.server import main as run_server
            run_server()
        except ImportError:
            print("Error: claudepedia-mcp not found. Install with: pip install claudepedia-mcp", file=sys.stderr)
            sys.exit(1)

if __name__ == "__main__":
    main()
