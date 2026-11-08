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
            "pr_number": {
                "type": "integer",
                "description": "Optional GitHub pull request number to check out into an isolated worktree for this worker.",
            },
            "use_worktree": {
                "type": "boolean",
                "description": "Optional override for isolated git worktree creation (defaults to allow_worker_git_worktree setting).",
            },
        },
        "required": ["parent_session_id", "prompt"],
    },
}

TOWN_STATUS = {
    "name": "superagent_town_status",
    "description": (
        "Inspect the router town and all of its worker sessions: status, plot slot, pet avatar, "
        "branch/worktree, linked PR, latest event, latest assistant reply, and working-tree diff summary."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "parent_session_id": {
                "type": "string",
                "description": "The router session id that owns the town, e.g. S-0001.",
            },
            "include_diff": {
                "type": "boolean",
                "description": "Collect git status/diff file counts for each worker (default true).",
                "default": True,
            },
        },
        "required": ["parent_session_id"],
    },
}

MESSAGE_WORKER = {
    "name": "superagent_message_worker",
    "description": (
        "Send a follow-up instruction from the town router to one of its idle/review/failed workers, "
        "resuming the worker for another turn (for example to address review notes, create a PR, or "
        "review/approve a pull request)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "parent_session_id": {
                "type": "string",
                "description": "The router session id that owns the town, e.g. S-0001.",
            },
            "worker_session_id": {
                "type": "string",
                "description": "The worker session id to message, e.g. S-0002.",
            },
            "message": {
                "type": "string",
                "description": "Follow-up instruction or feedback for the worker.",
            },
            "action": {
                "type": "string",
                "enum": ["follow_up", "create_pr", "review_pr", "approve_pr"],
                "description": "Optional structured action to instruct the worker to perform.",
                "default": "follow_up",
            },
            "pr_number": {
                "type": "integer",
                "description": "Optional pull request number when action is review_pr or approve_pr.",
            },
        },
        "required": ["parent_session_id", "worker_session_id", "message"],
    },
}

PR_CREATE = {
    "name": "superagent_pr_create",
    "description": (
        "Stage, commit, and push the current session's branch to GitHub and open a pull request. "
        "Requires allow_worker_pr_create to be enabled in Superagent Settings."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "The worker (or router) session id, e.g. S-0002.",
            },
            "title": {
                "type": "string",
                "description": "Pull request title.",
            },
            "body": {
                "type": "string",
                "description": "Pull request description body.",
            },
            "branch": {
                "type": "string",
                "description": "Optional git branch name (defaults to the session's branch_name or superagent/<id>-<slug>).",
            },
            "base": {
                "type": "string",
                "description": "Optional base branch (defaults to github_pr_base_branch setting or repository default branch).",
            },
            "draft": {
                "type": "boolean",
                "description": "Optional override to create the pull request as a draft.",
            },
            "commit_message": {
                "type": "string",
                "description": "Optional git commit message (defaults to the PR title).",
            },
        },
        "required": ["session_id", "title"],
    },
}

PR_REVIEW = {
    "name": "superagent_pr_review",
    "description": (
        "Submit a GitHub pull request review (APPROVE, REQUEST_CHANGES, or COMMENT) from a router or "
        "worker session. Formal APPROVE / REQUEST_CHANGES verdicts require allow_router_pr_approve "
        "to be enabled in Superagent Settings."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "The router or worker session id submitting the review, e.g. S-0001.",
            },
            "pr_number": {
                "type": "integer",
                "description": "Pull request number to review (defaults to the session's linked pr_number).",
            },
            "event": {
                "type": "string",
                "enum": ["APPROVE", "REQUEST_CHANGES", "COMMENT"],
                "description": "Review verdict: APPROVE, REQUEST_CHANGES, or COMMENT.",
                "default": "COMMENT",
            },
            "body": {
                "type": "string",
                "description": "Review comment body.",
            },
        },
        "required": ["session_id"],
    },
}
