"""The one place that talks to the language model.

Every model call in the app goes through call_json() or call_text(), so switching
provider (for example to a Fortrea-approved Azure-hosted model) means editing only
this file: change the client, the model names, the prices and the two functions."""

import base64

import anthropic

MODELS = {"fast": "claude-haiku-5-5",     # cheap passes over a whole document
          "strong": "claude-opus-5-5"}    # careful reading of a few pages

# USD per million tokens (input, output), from Anthropic's published pricing.
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.00, 20.00)}

_clients = {}


def client():
    """Client for this browser session's key. app.py puts the key in
    st.session_state["api_key"] (the owner's key after the demo password, or the
    visitor's own key), so one visitor never spends another's credits."""
    import streamlit as st
    key = st.session_state.get("api_key")
    if not key:
        raise RuntimeError("No API key for this session: enter the demo password or your "
                           "own Anthropic API key in the sidebar. Saved extractions still work.")
    if key not in _clients:
        _clients[key] = anthropic.Anthropic(api_key=key)
    return _clients[key]


def cost(model, usage):
    """Approximate USD cost of one call."""
    price_in, price_out = PRICES[model]
    tokens_in = (usage.input_tokens + (usage.cache_creation_input_tokens or 0) * 1.25
                 + (usage.cache_read_input_tokens or 0) * 0.1)
    return (tokens_in * price_in + usage.output_tokens * price_out) / 1_000_000


def pdf_block(pdf_bytes, title=None):
    """A PDF (or a one-page slice of one) to send as model input."""
    block = {"type": "document",
             "source": {"type": "base64", "media_type": "application/pdf",
                        "data": base64.standard_b64encode(pdf_bytes).decode()}}
    if title:
        block["title"] = title
    return block


def call_json(tier, content, schema, max_tokens=8000, effort="low", system=None):
    """Ask for output that must match a JSON schema. content is a list of blocks
    (text and/or PDF). Returns (text of the JSON, cost)."""
    import json  # noqa: F401  (callers parse the text)
    model = MODELS[tier]
    kwargs = dict(model=model, max_tokens=max_tokens,
                  output_config={"effort": effort,
                                 "format": {"type": "json_schema", "schema": schema}},
                  messages=[{"role": "user", "content": content}])
    if system:
        kwargs["system"] = system
    with client().messages.stream(**kwargs) as stream:
        response = stream.get_final_message()
    text = next(b.text for b in response.content if b.type == "text")
    return text, cost(model, response.usage)


def call_text(tier, system, messages, max_tokens=4000, effort="low"):
    """Plain chat answer. system may be a string or a list of blocks. Returns (text, cost)."""
    model = MODELS[tier]
    response = client().messages.create(model=model, max_tokens=max_tokens,
                                        output_config={"effort": effort},
                                        system=system, messages=messages)
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return text, cost(model, response.usage)
