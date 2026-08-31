# Landono assistant scaffold

This package splits the assistant into three pieces:

- `db.py`: read-only SQLite queries for deterministic listing filters.
- `vectorstore.py`: Chroma indexing and semantic search over listing text.
- `tools.py` and `agent.py`: LangChain tools and an agent factory.

Build the Chroma index:

```bash
pdm run update-chroma
```

Add free geocoding support and cache listing coordinates:

```bash
pdm run geocode
```

This uses the public Nominatim API, throttled and cached locally. For production, set a specific
`LANDONO_NOMINATIM_USER_AGENT` and keep attribution/usage-policy requirements in mind.

Create an agent:

```python
from realtor_assistant.agent import create_landono_agent

agent = create_landono_agent()
response = agent.invoke({
    "messages": [{"role": "user", "content": "Do you have rentals near McGill under $2,000?"}]
})
print(response["messages"][-1].content)
```

Run the sandbox CLI:

```bash
pdm run assistant
```

The CLI loads `OPENAI_API_KEY` from `.env` at the project root. The default model is `openai:gpt-4o-mini`.
You can override it in `.env`:

```bash
LANDONO_OPENAI_MODEL=openai:gpt-4.1-mini
```

Use `/handoff` in the CLI to test deterministic contact capture without the model generating the user's email
or phone number.

The booking/contact tool writes a pending handoff record to `db/lead_handoffs.db`. Broker contact details are
selected from the listings database only; if no broker can be selected deterministically, the handoff is marked
`needs_manual_routing` instead of guessing a phone number or email.

User contact details are also guarded: the handoff writer requires a validated `contact_token` created by
application code. Website, SMS, and social integrations should capture name/email/phone in their adapter layer,
create a contact token, then call the handoff writer.
