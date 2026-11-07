"""Tool schemas the model reads."""

ASK = {
    "name": "superagent_ask",
    "description": (
        "Record a coding request as a Superagent ticket. Use when the user asks you to queue work "
        "for the Superagent rather than doing it in this conversation. In observe mode the ticket "
        "is recorded and needs human approval; in auto mode it is queued for a worker subagent."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "request": {
                "type": "string",
                "description": "The work to do, in one or two sentences. Be specific: what to change and where.",
            }
        },
        "required": ["request"],
    },
}

TICK = {
    "name": "superagent_tick",
    "description": (
        "Run one Superagent routing pass: poll configured sources (GitHub), judge new requests, "
        "launch worker subagents for accepted tickets (auto mode only), and collect finished workers. "
        "Call this from a scheduled run to keep the Superagent always-on. Returns counts and "
        "tickets that need a human."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "wait": {
                "type": "boolean",
                "description": "Block until running workers finish (or the runtime cap), instead of returning immediately.",
                "default": False,
            }
        },
    },
}

STATUS = {
    "name": "superagent_status",
    "description": (
        "Show Superagent tickets: what is running, what finished, and — most important — what needs "
        "a human decision. Pass a ticket id for its full history."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ticket_id": {"type": "string", "description": "Optional ticket id, e.g. T-0003."}
        },
    },
}

RUN = {
    "name": "superagent_run",
    "description": (
        "Approve an observed, blocked or failed Superagent ticket and launch a worker for it now. "
        "This is the human-in-the-loop path out of observe mode."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ticket_id": {"type": "string", "description": "Ticket id to approve and run, e.g. T-0003."},
            "wait": {"type": "boolean", "description": "Block until the worker finishes.", "default": False},
        },
        "required": ["ticket_id"],
    },
}

SPAWN = {
    "name": "superagent_spawn",
    "description": (
        "Spawn one real Superagent worker session underneath the current router town. Each call "
        "creates exactly one new chat worker, one new house and one new person. Use one call per "
        "independent job, with a self-contained prompt; workers cannot spawn more workers. Only a "
        "root/router session may call this."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "parent_session_id": {
                "type": "string",
                "description": "The router session id that owns the town, e.g. S-0001.",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "A complete, self-contained task for the worker: what to change, where, and "
                    "which checks to run. The worker does not see this conversation."
                ),
            },
            "title": {
                "type": "string",
                "description": "Optional short label for the house; defaults to the first prompt line.",
            },
        },
        "required": ["parent_session_id", "prompt"],
    },
}
