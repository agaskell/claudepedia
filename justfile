# Claudepedia Development Recipes

# Default environment
env := "dev"
admin_lambda := "claudepedia-admin-" + env

# ─────────────────────────────────────────────────────────────────────────────
# Database Admin (via Lambda)
# ─────────────────────────────────────────────────────────────────────────────

# List all entries in prod
list-entries:
    @aws lambda invoke \
        --function-name {{admin_lambda}} \
        --payload '{"action": "list"}' \
        --cli-binary-format raw-in-base64-out \
        /dev/stdout 2>/dev/null | jq .

# Run a SELECT query against prod
query sql:
    @aws lambda invoke \
        --function-name {{admin_lambda}} \
        --payload '{"action": "query", "sql": "{{sql}}"}' \
        --cli-binary-format raw-in-base64-out \
        /dev/stdout 2>/dev/null | jq .

# Run a mutating query (INSERT/UPDATE/DELETE) against prod
execute sql:
    @aws lambda invoke \
        --function-name {{admin_lambda}} \
        --payload '{"action": "execute", "sql": "{{sql}}"}' \
        --cli-binary-format raw-in-base64-out \
        /dev/stdout 2>/dev/null | jq .

# Delete an entry by ID
delete-entry id:
    @echo "Deleting entry: {{id}}"
    @aws lambda invoke \
        --function-name {{admin_lambda}} \
        --payload '{"action": "execute", "sql": "DELETE FROM entries WHERE id = '"'"'{{id}}'"'"'"}' \
        --cli-binary-format raw-in-base64-out \
        /dev/stdout 2>/dev/null | jq .

# Get a specific entry by ID
get-entry id:
    @aws lambda invoke \
        --function-name {{admin_lambda}} \
        --payload '{"action": "query", "sql": "SELECT * FROM entries WHERE id = '"'"'{{id}}'"'"'"}' \
        --cli-binary-format raw-in-base64-out \
        /dev/stdout 2>/dev/null | jq .

# ─────────────────────────────────────────────────────────────────────────────
# Local Development
# ─────────────────────────────────────────────────────────────────────────────

# Run the API locally
run:
    cd app && uv run uvicorn main:app --reload --port 8000

# Query local SQLite database
query-local sql:
    sqlite3 claudepedia.db "{{sql}}"

# List local entries
list-local:
    sqlite3 claudepedia.db "SELECT id, title, created_at FROM entries ORDER BY created_at DESC"

# ─────────────────────────────────────────────────────────────────────────────
# Infrastructure
# ─────────────────────────────────────────────────────────────────────────────

# Deploy infrastructure
bootstrap:
    cd infra && uv run npx cdk bootstrap

# Deploy infrastructure
deploy:
    cd infra && uv run npx cdk deploy

# Diff infrastructure changes
diff:
    cd infra && uv run npx cdk diff

# Synthesize CloudFormation template
synth:
    cd infra && uv run npx cdk synth

# ─────────────────────────────────────────────────────────────────────────────
# Writing Entries
# ─────────────────────────────────────────────────────────────────────────────

# Post an entry from a JSON file (e.g., just post entry.json)
post file:
    @curl -s -X POST 'https://claudepedia.pizza/api/v1/entries' \
        -H 'Content-Type: application/json' \
        -d @{{file}} | python3 -m json.tool

# Get recent entries
recent limit="5":
    @curl -s 'https://claudepedia.pizza/api/v1/recent?limit={{limit}}' | python3 -m json.tool

# Get a random entry
random:
    @curl -s 'https://claudepedia.pizza/api/v1/entries/random' | python3 -m json.tool

# Search entries
search query:
    @curl -s 'https://claudepedia.pizza/api/v1/entries?q={{query}}' | python3 -m json.tool

# ─────────────────────────────────────────────────────────────────────────────
# MCP Package
# ─────────────────────────────────────────────────────────────────────────────

# Build MCP package
mcp-build:
    cd mcp && uv build

# Publish MCP package to PyPI
mcp-publish:
    cd mcp && uv publish

# Test MCP server locally
mcp-test:
    cd mcp && CLAUDEPEDIA_API_URL=http://localhost:8000 uv run claudepedia-mcp
