from collections import defaultdict, deque
from time import monotonic

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..config import settings
from ..riposte.taint import destination_key
from ..world import tickets

router = APIRouter()
MAX_BYTES = 24000
_senders = defaultdict(deque)


def open_mailbox() -> None:
    tickets.open_store(settings.resolve(settings.mail_state_path))


class InboundTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sender: str = Field(alias="from", min_length=3, max_length=200)
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=16000)


def rate_limit(sender: str):
    now = monotonic()
    entries = _senders[sender]
    while entries and entries[0] <= now - 60:
        entries.popleft()
    if len(entries) >= settings.inbound_per_sender_per_minute:
        raise HTTPException(429, "Sender rate limit exceeded", headers={"Retry-After": "60"})
    entries.append(now)


@router.post("/inbound", status_code=202)
async def receive_ticket(request: Request):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_BYTES:
            raise HTTPException(413, "Ticket exceeds size limit")
        data.extend(chunk)
    try:
        body = InboundTicket.model_validate_json(data)
    except ValidationError:
        raise HTTPException(422, "Expected from, subject and body within ticket limits")
    sender = destination_key(body.sender)
    if "@" not in sender:
        raise HTTPException(422, "from must be an email address")
    rate_limit(sender)
    ticket, receipt = tickets.receive(body.model_dump(by_alias=True))
    return {"ticket_id": ticket["id"], "receipt": receipt}


@router.get("/inbound/replies")
async def sender_replies(sender: str = Query(alias="from", max_length=200),
                         receipt: str = Header(default="", alias="X-Ticket-ID", max_length=128)):
    address = destination_key(sender)
    ticket = tickets.ticket_for_receipt(receipt)
    if ticket is None or destination_key(ticket["from"]) != address:
        raise HTTPException(404, "Mail receipt not found")
    return {"replies": tickets.mail_for(ticket["id"], address)}
