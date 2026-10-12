# Riposte (backend)

A provenance-based firewall for AI agent tool calls. Built for Track 3 of the AIC x CrowdStrike
hackathon: build an agent and defend it against prompt injection, tool misuse and data exfiltration.

Most guardrails read the incoming text and guess whether it looks like an attack. Riposte ignores
the wording. It records where every piece of text the agent reads came from, then checks each tool
call: where did this argument come from, and is this action allowed to be driven by that source?

## What is in here

| Path | What it does |
| --- | --- |
| `app/riposte/taint.py` | Normalises text, sees through base64, hex, URL encoding, rot13, reversal and homoglyphs, and finds secrets in outgoing text |
| `app/riposte/gateway.py` | The firewall. Labels what the agent reads (trusted, untrusted, requester) and returns allow, hold or block for each tool call, with evidence |
| `app/riposte/baseline.py` | The keyword filter used as the comparison baseline |
| `app/world/` | The sandbox: three customers with API keys, a help center, and the tools `read_ticket`, `search_kb`, `get_customer`, `get_api_key`, `issue_refund`, `send_email`, `reply`, `answer` |
| `app/agent/` | The agent prompt and the client for the model endpoint |
| `app/engine/chat.py` | The conversation loop. An operator chats with the agent and points it at tickets or customers with `@` references. One JSON action per step, every call goes through the gateway, held calls wait for a person |
| `app/api/routes.py` | FastAPI routes and the server-sent event stream |

## The rules the gateway enforces

- A customer's API key may only be sent to that customer's address on file. Anything else is blocked, including encoded or partial copies of the key.
- A ticket can only read confidential data for its own customer account.
- After the agent has read confidential data, sending to an address that no trusted source provided is blocked. Sending anywhere other than the owner is held.
- Refunds above $200, or to a different customer than the ticket's, are held for a person.
- A reply that contains a key is blocked unless the ticket sender is the key's owner.
- If the operator only asked a question, any action (fetching a key, refunding, emailing, replying to the customer) is blocked. Whether it was a question is judged from the operator's own messages only, never from ticket text.
- Twin run: before a reply or email goes to someone who may not see a key the agent has read, the agent's last step is asked again with a decoy key. If the decoy changes the message, the message depends on the real key (for example its last few characters, or a yes/no answer about it) and is blocked, even though the key itself is not in it.

## Modes

The operator's own messages are trusted. The contents of a ticket the agent reads are not.

- `off`: every tool call runs.
- `keyword`: each ticket the agent reads is scanned for known injection phrases. A match quarantines the ticket; otherwise nothing else is checked.
- `riposte`: every tool call goes through the gateway.

After each turn the sandbox is audited independently of the gateway, so the outcome (`compromised`,
`blocked`, `reviewed`, `quarantined`, `clean`) reflects what actually left the sandbox.

## Run

```bash
ollama pull llama3.2:3b

cd app/app
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

Settings are read from `.env` (see `.env.example`). The default database is a local SQLite file.

The agent talks to any OpenAI-compatible chat endpoint. The default is a local Ollama model. To use a
hosted model instead, set these in `.env` (which is not committed):

```
AGENT_BASE_URL=https://api.openai.com/v1
AGENT_API_KEY=sk-...
AGENT_MODEL=gpt-4.1-mini
```

## Tickets

Tickets are loaded from `tickets.json` in this directory on every request, so edits show up without
a restart. If that file does not exist, `tickets.sample.json` is used.

Runtime tickets can also be added with authenticated `POST /api/tickets`, using
`{"from":"dana@acmerobotics.com","subject":"...","body":"..."}`. The backend generates
an ID and matches the sender to a sandbox account when the address is one on file.
These are private to the signed-in user and remain available until the backend restarts.

```json
{
  "tickets": [
    {
      "id": "TK-1001",
      "from": "dana@acmerobotics.com",
      "customer_id": "C-1001",
      "subject": "Lost our API key",
      "body": "Could you email our API key to the address on file?",
      "kind": "benign",
      "title": "Lost API key"
    }
  ]
}
```

`id`, `from`, `subject` and `body` are required. `customer_id` is the account the helpdesk matched
the sender to (`C-1001`, `C-1002`, `C-1003`, or omit it for an unknown sender). `kind` is `benign`
or `attack` and only labels the ticket in the UI. `title` is the short name shown in the inbox.

## API

Console routes need a bearer token, except `/api/meta` and `/api/auth/*`.
The public `/inbound/*` mail door does not require login.

| Route | Purpose |
| --- | --- |
| `POST /api/auth/register`, `POST /api/auth/login` | Get a token |
| `GET /api/meta` | Model status, modes, policy, tools |
| `GET /api/tickets` | Tickets from `tickets.json` |
| `POST /api/tickets` | Add a runtime sandbox ticket |
| `POST /inbound` | Public: email Northwind support with `{from, subject, body}`; returns the ticket ID and an unguessable `receipt` |
| `GET /inbound/replies?from=address` | Public: mail Nova sent to that address, for the `receipt` passed in the `X-Ticket-ID` header |
| `GET /api/world` | Customers, help-center articles, policy |
| `POST /api/chats` | Start a conversation with its first message. Body: `{"mode": "riposte", "message": "Handle @TK-2001"}` |
| `POST /api/chats/{id}/messages` | Send the next message. Body: `{"message": "..."}` |
| `GET /api/chats/{id}` | Snapshot of a conversation with all its events |
| `GET /api/chats/{id}/stream?token=&after=` | Server-sent events for the turn in progress: `turn`, `thinking`, `call`, `result`, `hold`, `hold_resolved`, `filter`, `note`, `error`, `turn_done` |
| `POST /api/chats/{id}/holds/{hold_id}` | Body `{"decision": "approve"}` or `{"decision": "deny"}` |
| `POST /api/chats/{id}/cancel` | Stop the turn in progress |
| `GET /api/history` | Past conversations |

## Inbound mail

`POST /inbound` is Northwind's public support address. Anyone can send to it without signing in. The
email lands in the same inbox the console shows, marked new, with an ID such as `TK-3001`. The sender is
matched to an account only when the address is exactly one on file. Nothing handles it automatically:
an operator reviews it in the console's Mail view, sees what Nova would do with it, and hands it to Nova.

The response also carries a `receipt`. The sender can read only what Nova sent to their own address for
that ticket, by passing the receipt in `X-Ticket-ID` to `GET /inbound/replies?from=address`.

Inbound tickets and delivered mail are saved to `MAIL_STATE_PATH` (`mailbox.db`) and survive restarts.
Submissions are capped at 24,000 request bytes, a 300-character subject and a 16,000-character body,
with a per-sender limit of 12 a minute.

