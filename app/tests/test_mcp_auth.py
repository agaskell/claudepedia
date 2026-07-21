"""Tests for auth in the HTTP MCP tools (direct function calls)."""


async def test_mcp_write_without_key_returns_guidance(client):
    import mcp_http

    result = await mcp_http.claudepedia_write(title="T", content="C")
    assert "claudepedia_register" in result


async def test_mcp_write_with_key_param_publishes(client, make_api_key):
    import mcp_http

    key = make_api_key()
    result = await mcp_http.claudepedia_write(
        title="Via param", content="C", api_key=key
    )
    assert "published" in result.lower()


async def test_mcp_write_with_invalid_key_returns_guidance(client):
    import mcp_http

    result = await mcp_http.claudepedia_write(title="T", content="C", api_key="cp_bad")
    assert "invalid" in result.lower()


async def test_mcp_write_with_context_key_publishes(client, make_api_key):
    """The /mcp endpoint feeds the request's Authorization header via contextvar."""
    import auth
    import mcp_http

    key = make_api_key()
    token = auth.request_api_key.set(key)
    try:
        result = await mcp_http.claudepedia_write(title="Via header", content="C")
    finally:
        auth.request_api_key.reset(token)
    assert "published" in result.lower()


async def test_mcp_register_and_verify_tools(client, sent_codes):
    import mcp_http

    out = await mcp_http.claudepedia_register(email="mcp@example.com")
    assert "code" in out.lower()

    code = sent_codes[-1][1]
    out = await mcp_http.claudepedia_verify(email="mcp@example.com", code=code)
    assert "cp_" in out
