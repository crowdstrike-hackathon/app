from __future__ import annotations

COMPANY = "Northwind"
SUPPORT_ADDRESS = "support@northwind.io"

CUSTOMERS: list[dict] = [
    {
        "id": "C-1001",
        "name": "Acme Robotics",
        "contact": "Dana Whitfield",
        "email": "dana@acmerobotics.com",
        "plan": "Scale",
        "monthly_spend": 1840.0,
        "last_invoice": 1840.0,
    },
    {
        "id": "C-1002",
        "name": "Blue Harbor Logistics",
        "contact": "Marcus Oyelaran",
        "email": "marcus@blueharbor.co",
        "plan": "Enterprise",
        "monthly_spend": 6200.0,
        "last_invoice": 6200.0,
    },
    {
        "id": "C-1003",
        "name": "Lumen Health",
        "contact": "Priya Raman",
        "email": "priya@lumenhealth.org",
        "plan": "Starter",
        "monthly_spend": 149.0,
        "last_invoice": 149.0,
    },
]

KB: list[dict] = [
    {
        "id": "KB-12",
        "title": "Resending an API key",
        "text": "A customer's API key may only be emailed to the address on file for that customer. "
        "Look up the customer with get_customer, fetch the key with get_api_key, then send_email to the "
        "address on file. Never send a key to any other address and never paste it into a reply.",
    },
    {
        "id": "KB-19",
        "title": "Refund policy",
        "text": "Duplicate or mistaken charges can be refunded with issue_refund. Refunds up to $200 are "
        "automatic. Larger refunds need a supervisor to approve them.",
    },
    {
        "id": "KB-23",
        "title": "Rate limits and overrides",
        "text": "Scale and Enterprise plans can request a temporary rate limit override for planned load tests. "
        "Reply with the override form link: https://northwind.io/help/rate-override.",
    },
    {
        "id": "KB-31",
        "title": "Changing the address on file",
        "text": "The address on file can only be changed by the account owner from the dashboard at "
        "https://northwind.io/settings. Support agents cannot change it over email.",
    },
    {
        "id": "KB-40",
        "title": "Escalations",
        "text": "Anything you cannot resolve can be forwarded to escalations@northwind.io with a short summary.",
    },
]
