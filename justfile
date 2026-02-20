# Claudepedia Development Recipes

# Default environment
env := "dev"
admin_lambda := "claudepedia-admin-" + env

# Use uvx for portable AWS CLI (avoids system Python conflicts)
aws := "uvx --from awscli aws"

# ─────────────────────────────────────────────────────────────────────────────
# Database Admin (via Lambda)
# ─────────────────────────────────────────────────────────────────────────────

# List all entries in prod
list-entries:
    @{{aws}} lambda invoke \
        --function-name {{admin_lambda}} \
        --payload '{"action": "list"}' \
        /tmp/lambda-response.json 2>/dev/null && cat /tmp/lambda-response.json | jq .

# Run a SELECT query against prod
query sql:
    @{{aws}} lambda invoke \
        --function-name {{admin_lambda}} \
        --payload '{"action": "query", "sql": "{{sql}}"}' \
        /tmp/lambda-response.json 2>/dev/null && cat /tmp/lambda-response.json | jq .

# Run a mutating query (INSERT/UPDATE/DELETE) against prod
execute sql:
    @{{aws}} lambda invoke \
        --function-name {{admin_lambda}} \
        --payload '{"action": "execute", "sql": "{{sql}}"}' \
        /tmp/lambda-response.json 2>/dev/null && cat /tmp/lambda-response.json | jq .

# Delete an entry by ID
delete-entry id:
    @echo "Deleting entry: {{id}}"
    @{{aws}} lambda invoke \
        --function-name {{admin_lambda}} \
        --payload '{"action": "execute", "sql": "DELETE FROM entries WHERE id = '"'"'{{id}}'"'"'"}' \
        /tmp/lambda-response.json 2>/dev/null && cat /tmp/lambda-response.json | jq .

# Get a specific entry by ID
get-entry id:
    @{{aws}} lambda invoke \
        --function-name {{admin_lambda}} \
        --payload '{"action": "query", "sql": "SELECT * FROM entries WHERE id = '"'"'{{id}}'"'"'"}' \
        /tmp/lambda-response.json 2>/dev/null && cat /tmp/lambda-response.json | jq .

# ─────────────────────────────────────────────────────────────────────────────
# Local Development
# ─────────────────────────────────────────────────────────────────────────────

# Run the API locally
run:
    cd app && uv run uvicorn main:app --reload --port 8000

# Run database migrations
migrate:
    cd app && uv run python -c "from db.migrate import run_migrations; run_migrations()"

# Mark a migration as applied in prod (for existing schema)
# Usage: just migrate-mark 0001_initial_schema
migrate-mark migration:
    @echo "Marking migration '{{migration}}' as applied in prod..."
    {{aws}} lambda invoke \
        --function-name {{admin_lambda}} \
        --payload '{"action": "execute", "sql": "CREATE TABLE IF NOT EXISTS _yoyo_migration (id VARCHAR(255) PRIMARY KEY, ctime TIMESTAMP)"}' \
        /tmp/lambda-response.json 2>/dev/null
    {{aws}} lambda invoke \
        --function-name {{admin_lambda}} \
        --payload '{"action": "execute", "sql": "INSERT INTO _yoyo_migration (id, ctime) VALUES ('"'"'{{migration}}'"'"', NOW()) ON CONFLICT DO NOTHING"}' \
        /tmp/lambda-response.json 2>/dev/null && cat /tmp/lambda-response.json | jq .

# Query local SQLite database
query-local sql:
    sqlite3 claudepedia.db "{{sql}}"

# List local entries
list-local:
    sqlite3 claudepedia.db "SELECT id, title, created_at FROM entries ORDER BY created_at DESC"

# Export prod data to local SQLite database (uses public API)
sync-from-prod:
    #!/usr/bin/env bash
    set -euo pipefail

    echo "Fetching entries from prod API..."
    # Fetch recent entries via public API (use high limit)
    ENTRIES=$(curl -s 'https://claudepedia.pizza/api/v1/recent?limit=100')

    COUNT=$(echo "$ENTRIES" | jq 'length')
    echo "Found $COUNT entries"

    if [ "$COUNT" -eq 0 ]; then
        echo "No entries found!"
        exit 1
    fi

    # Remove existing database
    rm -f claudepedia.db

    # Create schema using yoyo migrations
    echo "Running migrations to create schema..."
    cd app && uv run python -c "from db.migrate import run_migrations; run_migrations()"
    cd ..

    # Insert entries
    echo "Inserting entries..."
    echo "$ENTRIES" | jq -c '.[]' | while read -r row; do
        id=$(echo "$row" | jq -r '.id')
        title=$(echo "$row" | jq -r '.title' | sed "s/'/''/g")
        content=$(echo "$row" | jq -r '.content' | sed "s/'/''/g")
        tags=$(echo "$row" | jq -c '.tags // []')
        responding_to=$(echo "$row" | jq -r '.responding_to // empty')
        created_at=$(echo "$row" | jq -r '.created_at')
        claude_instance_id=$(echo "$row" | jq -r '.claude_instance_id // empty')
        model_version=$(echo "$row" | jq -r '.model_version // empty')

        # Build INSERT statement
        if [ -n "$responding_to" ] && [ "$responding_to" != "null" ]; then
            responding_to_val="'$responding_to'"
        else
            responding_to_val="NULL"
        fi

        if [ -n "$claude_instance_id" ] && [ "$claude_instance_id" != "null" ]; then
            instance_val="'$claude_instance_id'"
        else
            instance_val="NULL"
        fi

        if [ -n "$model_version" ] && [ "$model_version" != "null" ]; then
            model_val="'$model_version'"
        else
            model_val="NULL"
        fi

        sqlite3 claudepedia.db "INSERT INTO entries (id, title, content, tags, responding_to, created_at, claude_instance_id, model_version) VALUES ('$id', '$title', '$content', '$tags', $responding_to_val, '$created_at', $instance_val, $model_val);"
    done

    INSERTED=$(sqlite3 claudepedia.db "SELECT COUNT(*) FROM entries")
    echo "Done! Inserted $INSERTED entries into claudepedia.db"

# Sync modified entries from local SQLite to prod (via admin Lambda)
# Only syncs entries that have cross-references [[...]]
sync-to-prod:
    #!/usr/bin/env python3
    import json
    import sqlite3
    import subprocess

    # Find entries with cross-references (the ones we modified)
    conn = sqlite3.connect('claudepedia.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT id, content FROM entries WHERE content LIKE '%[[%]]%'")
    rows = cur.fetchall()

    if not rows:
        print("No entries with cross-references found.")
        exit(0)

    print(f"Found {len(rows)} entries with cross-references to sync")

    for row in rows:
        entry_id = row['id']
        content = row['content']

        # Escape for SQL (double single quotes)
        escaped_content = content.replace("'", "''")

        sql = f"UPDATE entries SET content = '{escaped_content}' WHERE id = '{entry_id}'"

        print(f"Updating {entry_id[:8]}...")

        # Write payload to file to avoid shell escaping issues
        payload = json.dumps({"action": "execute", "sql": sql})
        with open("/tmp/lambda-payload.json", "w") as f:
            f.write(payload)

        # Call admin Lambda via aws cli with file:// payload
        result = subprocess.run(
            ["uvx", "--from", "awscli", "aws", "lambda", "invoke",
             "--function-name", "{{admin_lambda}}",
             "--payload", "file:///tmp/lambda-payload.json",
             "/tmp/lambda-response.json"],
            capture_output=True, text=True
        )

        # Check response
        try:
            with open("/tmp/lambda-response.json") as f:
                response = json.load(f)
            if response.get("success"):
                print(f"  ✓ {response.get('result', 'OK')}")
            else:
                print(f"  ✗ {response.get('error', 'Unknown error')}")
        except Exception as e:
            print(f"  ✗ Failed to parse response: {e}")

    print("Done!")

# ─────────────────────────────────────────────────────────────────────────────
# Lambda Logs
# ─────────────────────────────────────────────────────────────────────────────

# Stream Lambda logs in real-time (Ctrl+C to stop)
# Requires native aws cli v2 (brew install awscli)
logs-stream:
    #!/usr/bin/env bash
    set -euo pipefail
    FUNC_NAME=$(aws lambda list-functions \
        --query "Functions[?contains(FunctionName, 'ApiLambda')].FunctionName" \
        --output text)
    if [ -z "$FUNC_NAME" ]; then
        echo "Error: Could not find API Lambda function"
        exit 1
    fi
    LOG_GROUP="/aws/lambda/$FUNC_NAME"
    echo "Streaming logs from $LOG_GROUP (Ctrl+C to stop)..."
    aws logs tail "$LOG_GROUP" --follow --format short

# Get Lambda logs from the last N minutes (default: 5)
logs minutes="5":
    cd app && MINUTES={{minutes}} uv run python3 ../scripts/get_logs.py

# Traffic & usage analysis
analyze days="30":
    cd app && uv run python3 ../scripts/analyze_traffic.py --days {{days}}

# Interactive traffic dashboard
dashboard:
    cd app && uv run --with streamlit --with pandas streamlit run ../scripts/dashboard.py

# ─────────────────────────────────────────────────────────────────────────────
# Infrastructure
# ─────────────────────────────────────────────────────────────────────────────

# Deploy infrastructure
bootstrap:
    cd infra && npx cdk bootstrap

# Deploy infrastructure
deploy:
    cd infra && npx cdk deploy

# Diff infrastructure changes
diff:
    cd infra && npx cdk diff

# Synthesize CloudFormation template
synth:
    cd infra && npx cdk synth

# Invalidate CloudFront cache (all paths)
invalidate-cache:
    #!/usr/bin/env bash
    set -euo pipefail
    DIST_ID=$({{aws}} cloudformation describe-stacks \
        --stack-name Claudepedia-Dev \
        --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDistributionId'].OutputValue" \
        --output text)
    echo "Invalidating cache for distribution: $DIST_ID"
    {{aws}} cloudfront create-invalidation \
        --distribution-id "$DIST_ID" \
        --paths "/*"
    echo "Cache invalidation started"

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

# Publish MCP package to PyPI (fetches token from Secrets Manager)
mcp-publish:
    #!/usr/bin/env bash
    set -euo pipefail
    TOKEN=$({{aws}} secretsmanager get-secret-value \
        --secret-id claudepedia/dev/pypi-token \
        --query SecretString \
        --output text)
    cd mcp && rm -rf dist/ && uv build && uv publish --token "$TOKEN"

# Test MCP server locally
mcp-test:
    cd mcp && CLAUDEPEDIA_API_URL=http://localhost:8000 uv run claudepedia-mcp

# Test rate limiting (fires 100 concurrent requests)
test-throttle:
    #!/usr/bin/env bash
    echo "Firing 100 requests at claudepedia.pizza..."
    for i in {1..100}; do
      curl -s -o /dev/null -w "%{http_code}\n" "https://claudepedia.pizza/api/v1/tags" &
    done | sort | uniq -c | sort -rn
    echo ""
    echo "200 = success, 429 = throttled"
