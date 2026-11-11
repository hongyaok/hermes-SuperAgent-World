"""Tests for the stdlib GitHub client and PR checkout (sa_github.py).

The network is replaced at the `_urlopen` seam and git at the `git_runner`
parameter, so nothing here touches GitHub or a real repository.
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from sa_plugin import sa_github

TOKEN = "ghp_SUPERSECRET123456"


class FakeResponse:
    def __init__(self, payload):
        self._data = b"" if payload is None else json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHTTP:
    """Maps a URL fragment to a payload (or an exception to raise)."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        for fragment, payload in self.responses.items():
            if fragment in request.full_url:
                if isinstance(payload, Exception):
                    raise payload
                return FakeResponse(payload)
        return FakeResponse({})

    def url_for(self, fragment):
        for request in self.requests:
            if fragment in request.full_url:
                return request.full_url
        return ""

    def request_for(self, fragment):
        for request in self.requests:
            if fragment in request.full_url:
                return request
        return None


@pytest.fixture
def http(monkeypatch):
    fake = FakeHTTP()
    monkeypatch.setattr(sa_github, "_urlopen", fake)
    return fake


def http_error(status: int, message: str) -> urllib.error.HTTPError:
    body = json.dumps({"message": message}).encode("utf-8")
    return urllib.error.HTTPError(
        f"https://api.github.com/x", status, message, {}, io.BytesIO(body)
    )


def pr_payload(number=7, **overrides):
    data = {
        "number": number,
        "title": "Add parser tests",
        "body": "Closes #3",
        "user": {"login": "alice"},
        "state": "open",
        "head": {"ref": "feature/parser", "sha": "a" * 40},
        "base": {"ref": "main"},
        "html_url": f"https://github.com/owner/name/pull/{number}",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "draft": False,
    }
    data.update(overrides)
    return data


# -- transport -----------------------------------------------------------


def test_call_sends_auth_and_api_headers(http):
    http.responses["/user"] = {"login": "agent", "id": 5}
    client = sa_github.GitHubClient(TOKEN, "owner/name")
    assert client.me() == {"login": "agent", "id": 5}

    request = http.request_for("/user")
    assert request.get_header("Authorization") == f"Bearer {TOKEN}"
    assert request.get_header("User-agent") == "superagent-plugin"
    assert request.get_header("X-github-api-version") == "2022-11-28"
    assert request.get_header("Accept") == "application/vnd.github+json"


def test_http_error_carries_status_and_message_without_the_token(http):
    http.responses["/user"] = http_error(401, "Bad credentials")
    client = sa_github.GitHubClient(TOKEN, "owner/name")
    with pytest.raises(sa_github.GitHubError) as excinfo:
        client.me()
    assert excinfo.value.status == 401
    assert "Bad credentials" in str(excinfo.value)
    assert TOKEN not in str(excinfo.value)


def test_network_error_is_wrapped(http):
    http.responses["/user"] = urllib.error.URLError("name resolution failed")
    with pytest.raises(sa_github.GitHubError) as excinfo:
        sa_github.GitHubClient(TOKEN, "owner/name").me()
    assert excinfo.value.status == 0
    assert "cannot reach GitHub" in str(excinfo.value)


def test_repo_info_is_normalized(http):
    http.responses["/repos/owner/name"] = {
        "full_name": "owner/name",
        "default_branch": "main",
        "private": True,
        "open_issues": 4,
    }
    info = sa_github.GitHubClient(TOKEN, "owner/name").repo_info()
    assert info == {
        "full_name": "owner/name",
        "default_branch": "main",
        "private": True,
        "open_issues": 4,
    }
    assert http.url_for("/repos/owner/name").endswith("/repos/owner/name")


# -- pull requests -------------------------------------------------------


def test_list_open_prs_normalizes_and_limits(http):
    http.responses["/pulls"] = [pr_payload(), pr_payload(8, draft=True, head={"ref": "x", "sha": "b"})]
    prs = sa_github.GitHubClient(TOKEN, "owner/name").list_open_prs(limit=5)
    assert [pr["number"] for pr in prs] == [7, 8]
    assert prs[0]["author"] == "alice"
    assert prs[0]["head_ref"] == "feature/parser"
    assert prs[0]["base_ref"] == "main"
    assert prs[0]["head_sha"] == "a" * 40
    assert prs[0]["repo"] == "owner/name"
    assert prs[1]["draft"] is True
    url = http.url_for("/pulls")
    assert "state=open" in url and "per_page=5" in url


def test_get_pr_maps_a_404(http):
    http.responses["/pulls/99"] = http_error(404, "Not Found")
    with pytest.raises(sa_github.GitHubError) as excinfo:
        sa_github.GitHubClient(TOKEN, "owner/name").get_pr(99)
    assert excinfo.value.status == 404


def test_list_comments_merges_issue_and_review_comments(http):
    http.responses["/issues/7/comments"] = [
        {
            "id": 22,
            "body": "issue later",
            "user": {"login": "bob"},
            "html_url": "https://github.com/owner/name/pull/7#issuecomment-22",
            "created_at": "2026-01-02T00:00:00Z",
        }
    ]
    http.responses["/pulls/7/comments"] = [
        {
            "id": 11,
            "body": "review earlier",
            "user": {"login": "carol"},
            "html_url": "https://github.com/owner/name/pull/7#discussion_r11",
            "path": "src/parser.py",
            "line": 42,
            "created_at": "2026-01-01T00:00:00Z",
        }
    ]
    comments = sa_github.GitHubClient(TOKEN, "owner/name").list_comments(7)
    assert [comment["id"] for comment in comments] == [11, 22]
    assert [comment["kind"] for comment in comments] == ["review", "issue"]
    assert comments[0]["path"] == "src/parser.py"
    assert comments[0]["line"] == 42
    assert comments[1]["author"] == "bob"


def test_post_comment_sends_the_body(http):
    http.responses["/issues/7/comments"] = {"id": 33, "html_url": "https://x", "created_at": "now"}
    result = sa_github.GitHubClient(TOKEN, "owner/name").post_comment(7, "on it")
    assert result["id"] == 33
    request = http.request_for("/issues/7/comments")
    assert json.loads(request.data.decode("utf-8")) == {"body": "on it"}


# -- triggers ------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "@agent please fix the parser",
        "hey @Agent, look",
        "@superagent run the tests",
        "please /superagent this",
        "Superagent: take a look",
        "  superagent: fix it",
        "SUPERAGENT: uppercase",
        "@sa please fix the parser",
        "@/sa, run the tests",
        "can you /sa look at this?",
        "@SA uppercase",
        "/sa",
    ],
)
def test_is_trigger_accepts_the_documented_forms(body):
    assert sa_github.is_trigger(body, "agent") is True


@pytest.mark.parametrize(
    "body",
    [
        "",
        "   ",
        "thanks, looks good",
        "@someone-else",
        "superagent is a plugin",
        # The short alias must stand alone: a longer word or a path segment
        # is not a request.
        "@sample logs please",
        "see docs/sa.md",
        "/sales report",
        "https://example.com/sa",
        None,
    ],
)
def test_is_trigger_rejects_everything_else(body):
    assert sa_github.is_trigger(body, "agent") is False


def test_is_trigger_needs_a_login_for_a_bare_mention():
    assert sa_github.is_trigger("@agent hello", "") is False


@pytest.mark.parametrize(
    "login,expected",
    [
        ("dependabot[bot]", True),
        ("renovate[bot]", True),
        ("github-actions", True),
        ("dependabot", True),
        ("alice", False),
        ("", False),
    ],
)
def test_is_bot(login, expected):
    assert sa_github.is_bot(login) is expected


# -- checkout ------------------------------------------------------------


class FakeGit:
    def __init__(self, code=0, out="", err=""):
        self.code = code
        self.out = out
        self.err = err
        self.calls = []

    def __call__(self, argv, cwd, timeout):
        self.calls.append({"argv": list(argv), "cwd": cwd, "timeout": timeout})
        return self.code, self.out, self.err


def make_clone(tmp_path) -> Path:
    repo = tmp_path / "clone"
    (repo / ".git").mkdir(parents=True)
    return repo


def test_prepare_pr_checkout_fetches_then_adds_a_worktree(tmp_path):
    repo = make_clone(tmp_path)
    workspace = tmp_path / "workspaces"
    git = FakeGit()

    result = sa_github.prepare_pr_checkout(
        str(repo), "owner/name", 7, str(workspace), TOKEN, git_runner=git
    )

    assert result["reused"] is False
    assert result["branch_ref"] == "refs/superagent/pr-7"
    assert Path(result["path"]) == workspace / "pr-7"

    fetch, worktree = git.calls[0]["argv"], git.calls[1]["argv"]
    assert fetch[:3] == ["git", "-C", str(repo)]
    assert any(part.startswith("http.extraHeader=Authorization: Basic ") for part in fetch)
    assert "fetch" in fetch and "--no-tags" in fetch
    assert "https://github.com/owner/name.git" in fetch
    assert "+refs/pull/7/head:refs/superagent/pr-7" in fetch
    assert worktree == [
        "git",
        "-C",
        str(repo),
        "worktree",
        "add",
        "--detach",
        str(workspace / "pr-7"),
        "refs/superagent/pr-7",
    ]
    assert git.calls[0]["cwd"] == str(repo)


def test_prepare_pr_checkout_base64_encodes_the_token(tmp_path):
    import base64

    repo = make_clone(tmp_path)
    git = FakeGit()
    sa_github.prepare_pr_checkout(str(repo), "o/n", 1, str(tmp_path / "ws"), TOKEN, git_runner=git)
    header = [part for part in git.calls[0]["argv"] if part.startswith("http.extraHeader=")][0]
    encoded = header.split("Basic ", 1)[1]
    assert base64.b64decode(encoded).decode("utf-8") == f"x-access-token:{TOKEN}"


def test_prepare_pr_checkout_reuses_an_existing_checkout(tmp_path):
    repo = make_clone(tmp_path)
    dest = tmp_path / "workspaces" / "pr-7"
    (dest / ".git").mkdir(parents=True)
    git = FakeGit()

    result = sa_github.prepare_pr_checkout(
        str(repo), "owner/name", 7, str(tmp_path / "workspaces"), TOKEN, git_runner=git
    )
    assert result == {"path": str(dest), "reused": True, "branch_ref": "refs/superagent/pr-7"}
    assert git.calls == []


def test_prepare_pr_checkout_requires_a_git_clone(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(sa_github.GitHubError) as excinfo:
        sa_github.prepare_pr_checkout(
            str(plain), "owner/name", 7, str(tmp_path / "ws"), TOKEN, git_runner=FakeGit()
        )
    assert "not a git repository" in str(excinfo.value)


def test_prepare_pr_checkout_rejects_a_foreign_directory(tmp_path):
    repo = make_clone(tmp_path)
    dest = tmp_path / "workspaces" / "pr-7"
    dest.mkdir(parents=True)
    with pytest.raises(sa_github.GitHubError) as excinfo:
        sa_github.prepare_pr_checkout(
            str(repo), "owner/name", 7, str(tmp_path / "workspaces"), TOKEN, git_runner=FakeGit()
        )
    assert "not a git checkout" in str(excinfo.value)


def test_prepare_pr_checkout_redacts_the_token_on_failure(tmp_path):
    repo = make_clone(tmp_path)
    git = FakeGit(code=1, err=f"fatal: authentication failed for {TOKEN}")
    with pytest.raises(sa_github.GitHubError) as excinfo:
        sa_github.prepare_pr_checkout(
            str(repo), "owner/name", 7, str(tmp_path / "ws"), TOKEN, git_runner=git
        )
    message = str(excinfo.value)
    assert "could not fetch pull request #7" in message
    assert TOKEN not in message
    assert "[redacted]" in message


# -- prompt --------------------------------------------------------------


def test_build_pr_prompt_contains_the_context_and_the_rules():
    prompt = sa_github.build_pr_prompt(
        pr_payload(),
        comment={"author": "alice", "body": "@agent please add a regression test"},
        checkout="/data/workspaces/pr-7",
    )
    assert "pull request #7" in prompt
    assert "Add parser tests" in prompt
    assert "https://github.com/owner/name/pull/7" in prompt
    assert "feature/parser -> main" in prompt
    assert "alice" in prompt
    assert "Triggering comment from @alice" in prompt
    assert "@agent please add a regression test" in prompt
    assert "/data/workspaces/pr-7" in prompt
    assert "git diff origin/main...HEAD" in prompt
    assert "Do NOT commit, push" in prompt
    assert "200 words" in prompt


def test_build_pr_prompt_without_a_checkout_says_so():
    prompt = sa_github.build_pr_prompt(pr_payload(8))
    assert "No isolated checkout" in prompt
    assert "git diff origin/main...HEAD" in prompt


def test_build_pr_prompt_clips_long_bodies():
    prompt = sa_github.build_pr_prompt(pr_payload(body="x" * 5000), comment={"body": "y" * 5000})
    assert "…" in prompt
    assert len(prompt) < 6000


def test_build_pr_prompt_can_create_and_approve():
    prompt = sa_github.build_pr_prompt(
        pr_payload(9),
        can_create_pr=True,
        can_approve_pr=True,
    )
    assert "superagent_pr_create" in prompt
    assert "superagent_pr_review" in prompt


# -- create_pr and create_review -----------------------------------------


def test_create_pr_posts_and_normalizes(http):
    http.responses["/repos/owner/name/pulls"] = pr_payload(12, title="New feature", draft=True)
    client = sa_github.GitHubClient(TOKEN, "owner/name")
    pr = client.create_pr("New feature", "superagent/s-0002-new", "main", body="Details", draft=True)
    assert pr["number"] == 12
    assert pr["title"] == "New feature"
    assert pr["draft"] is True
    req = http.request_for("/repos/owner/name/pulls")
    assert req.get_method() == "POST"
    sent = json.loads(req.data.decode("utf-8"))
    assert sent == {
        "title": "New feature",
        "head": "superagent/s-0002-new",
        "base": "main",
        "body": "Details",
        "draft": True,
    }


def test_create_review_approve_and_self_approve_fallback(http):
    http.responses["/pulls/7/reviews"] = {
        "id": 99,
        "state": "APPROVED",
        "html_url": "https://github.com/owner/name/pull/7#pullrequestreview-99",
    }
    client = sa_github.GitHubClient(TOKEN, "owner/name")
    rev = client.create_review(7, event="APPROVE", body="LGTM")
    assert rev == {
        "id": 99,
        "state": "APPROVED",
        "html_url": "https://github.com/owner/name/pull/7#pullrequestreview-99",
        "fallback": False,
    }

    # Simulate GitHub 422 "Can not approve your own pull request"
    http.responses["/pulls/8/reviews"] = http_error(422, "Can not approve your own pull request")
    http.responses["/issues/8/comments"] = {
        "id": 501,
        "html_url": "https://github.com/owner/name/pull/8#issuecomment-501",
    }
    fallback = client.create_review(8, event="APPROVE", body="Looks good!")
    assert fallback["fallback"] is True
    assert fallback["state"] == "APPROVE_COMMENT_FALLBACK"
    assert fallback["id"] == 501


def test_create_review_rejects_invalid_event(http):
    client = sa_github.GitHubClient(TOKEN, "owner/name")
    with pytest.raises(sa_github.GitHubError, match="review event"):
        client.create_review(7, event="MERGE")


# -- worker worktree & branch push ---------------------------------------


def test_sanitize_branch_name():
    assert sa_github.sanitize_branch_name("S-0002", title="Fix parser crash!") == "superagent/s-0002-fix-parser-crash"
    assert sa_github.sanitize_branch_name("S-0002", explicit="feature/clean_branch") == "feature/clean_branch"
    for bad in ("main", "master", "HEAD", "develop", "production", "bad..branch", "bad branch", "/leading"):
        with pytest.raises(sa_github.GitHubError):
            sa_github.sanitize_branch_name("S-0001", explicit=bad)


def test_prepare_worker_worktree_creates_and_reuses(tmp_path):
    repo = make_clone(tmp_path)
    ws = tmp_path / "workspaces"
    git = FakeGit()
    res = sa_github.prepare_worker_worktree(
        str(repo), "S-0003", str(ws), title="Add widgets", git_runner=git
    )
    assert res["reused"] is False
    assert res["branch"] == "superagent/s-0003-add-widgets"
    assert Path(res["path"]) == ws / "worker-s-0003"
    assert git.calls[0]["argv"] == [
        "git",
        "-C",
        str(repo),
        "worktree",
        "add",
        "-B",
        "superagent/s-0003-add-widgets",
        str(ws / "worker-s-0003"),
        "HEAD",
    ]

    # Simulate existing worktree (.git file in worktree)
    (ws / "worker-s-0003").mkdir(parents=True, exist_ok=True)
    (ws / "worker-s-0003" / ".git").write_text("gitdir: ...", encoding="utf-8")
    git2 = FakeGit()
    res2 = sa_github.prepare_worker_worktree(
        str(repo), "S-0003", str(ws), title="Add widgets", git_runner=git2
    )
    assert res2["reused"] is True
    assert git2.calls == []


def test_commit_and_push_branch_stages_commits_and_pushes(tmp_path):
    repo = make_clone(tmp_path)
    calls = []

    def runner(argv, cwd, timeout):
        calls.append(argv)
        if "status" in argv:
            return 0, " M parser.py\n", ""
        if "rev-parse" in argv:
            return 0, "deadbeef1234\n", ""
        return 0, "", ""

    res = sa_github.commit_and_push_branch(
        str(repo),
        "owner/name",
        TOKEN,
        branch="superagent/s-0002-fix",
        commit_message="Fix parser",
        git_runner=runner,
    )
    assert res == {
        "branch": "superagent/s-0002-fix",
        "sha": "deadbeef1234",
        "committed": True,
        "pushed": True,
    }
    assert any("commit" in c for c in calls)
    assert any("push" in c for c in calls)



# -- issues --------------------------------------------------------------


def issue_payload(number=3, **overrides):
    data = {
        "number": number,
        "title": "Crash on an empty payload",
        "body": "POST /parse with {} raises a KeyError.",
        "user": {"login": "alice"},
        "state": "open",
        "labels": [{"name": "bug"}, {"name": ""}],
        "comments": 2,
        "html_url": f"https://github.com/owner/name/issues/{number}",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        # _normalize_issue stamps the repo; the raw payload is what the client
        # sees before that, so the fixture carries it too.
        "repo": "owner/name",
    }
    data.update(overrides)
    return data


def test_list_open_issues_drops_pull_requests(http):
    http.responses["/issues?state=open"] = [
        issue_payload(3),
        # Every PR comes back from /issues with this key; it must not be
        # handled as an issue (that path is list_open_prs + a checkout).
        issue_payload(7, pull_request={"url": "https://api.github.com/repos/owner/name/pulls/7"}),
        issue_payload(4, title="Second"),
    ]

    issues = sa_github.GitHubClient(TOKEN, "owner/name").list_open_issues(limit=2)

    assert [issue["number"] for issue in issues] == [3, 4]
    assert issues[0]["author"] == "alice"
    assert issues[0]["labels"] == ["bug"]
    assert issues[0]["comment_count"] == 2
    assert issues[0]["repo"] == "owner/name"
    # The page is over-fetched so the caller's limit still means "issues".
    url = http.url_for("/issues?state=open")
    assert "per_page=24" in url


def test_get_issue_refuses_a_pull_request(http):
    http.responses["/issues/7"] = issue_payload(7, pull_request={"url": "x"})
    with pytest.raises(sa_github.GitHubError) as excinfo:
        sa_github.GitHubClient(TOKEN, "owner/name").get_issue(7)
    assert excinfo.value.status == 422
    assert "pull request" in str(excinfo.value)


def test_get_issue_maps_a_404(http):
    http.responses["/issues/99"] = http_error(404, "Not Found")
    with pytest.raises(sa_github.GitHubError) as excinfo:
        sa_github.GitHubClient(TOKEN, "owner/name").get_issue(99)
    assert excinfo.value.status == 404


def test_list_issue_comments_never_asks_for_review_comments(http):
    http.responses["/issues/3/comments"] = [
        {
            "id": 11,
            "user": {"login": "alice"},
            "body": "@sa please fix",
            "html_url": "https://github.com/owner/name/issues/3#issuecomment-11",
            "created_at": "2026-01-03T00:00:00Z",
        }
    ]

    comments = sa_github.GitHubClient(TOKEN, "owner/name").list_issue_comments(3)

    assert [comment["id"] for comment in comments] == [11]
    assert comments[0]["kind"] == "issue"
    assert comments[0]["author"] == "alice"
    # An issue has no diff, so /pulls/<n>/comments must not be requested.
    assert http.url_for("/pulls/3/comments") == ""


def test_build_issue_prompt_carries_the_issue_the_ask_and_the_rules():
    prompt = sa_github.build_issue_prompt(
        issue_payload(3, labels=["bug", "parser"]),
        comment={"author": "alice", "body": "@sa the empty dict crashes it"},
        checkout="/tmp/workspaces/worker-s-0007",
        can_create_pr=False,
    )

    assert "Work on GitHub issue #3 in owner/name." in prompt
    assert "Issue #3: Crash on an empty payload" in prompt
    assert "Labels: bug, parser" in prompt
    assert "POST /parse with {} raises a KeyError." in prompt
    assert "Triggering comment from @alice:" in prompt
    assert "the empty dict crashes it" in prompt
    assert "/tmp/workspaces/worker-s-0007" in prompt
    assert "Do NOT commit, push, merge" in prompt
    assert "pull request #3" not in prompt


def test_build_issue_prompt_resolves_the_issue_when_the_setting_is_on():
    prompt = sa_github.build_issue_prompt(
        issue_payload(3), comment=None, can_create_pr=True
    )
    assert "superagent_pr_create" in prompt
    assert "Closes #3" in prompt
    assert "Do NOT commit, push, merge" not in prompt


# -- merging -------------------------------------------------------------


def test_merge_pr_sends_the_method_and_reads_the_body(http):
    http.responses["/pulls/7/merge"] = {
        "merged": True,
        "sha": "abc123",
        "message": "Pull Request successfully merged",
    }

    result = sa_github.GitHubClient(TOKEN, "owner/name").merge_pr(
        7, merge_method="squash", commit_title="Ship it"
    )

    assert result == {
        "merged": True,
        "sha": "abc123",
        "message": "Pull Request successfully merged",
    }
    request = http.request_for("/pulls/7/merge")
    assert request.get_method() == "PUT"
    assert json.loads(request.data.decode("utf-8")) == {
        "merge_method": "squash",
        "commit_title": "Ship it",
    }


def test_merge_pr_rejects_an_unknown_method(http):
    with pytest.raises(sa_github.GitHubError) as excinfo:
        sa_github.GitHubClient(TOKEN, "owner/name").merge_pr(7, merge_method="fast-forward")
    assert "merge_method" in str(excinfo.value)


def test_merge_pr_reports_a_refused_merge(http):
    http.responses["/pulls/7/merge"] = http_error(405, "Pull Request is not mergeable")
    with pytest.raises(sa_github.GitHubError) as excinfo:
        sa_github.GitHubClient(TOKEN, "owner/name").merge_pr(7)
    assert excinfo.value.status == 405
    assert "not mergeable" in str(excinfo.value)


def test_get_pr_exposes_mergeability(http):
    http.responses["/pulls/7"] = pr_payload(7, mergeable=False, mergeable_state="dirty")
    pr = sa_github.GitHubClient(TOKEN, "owner/name").get_pr(7)
    assert pr["mergeable"] is False
    assert pr["mergeable_state"] == "dirty"
    assert pr["merged"] is False


def test_build_pr_prompt_offers_a_merge_only_when_allowed():
    allowed = sa_github.build_pr_prompt(pr_payload(7), can_merge=True)
    blocked = sa_github.build_pr_prompt(pr_payload(7))

    assert "superagent_pr_merge" in allowed
    assert "Never merge a draft" in allowed
    assert "superagent_pr_merge" not in blocked


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://github.com/hongyaok/hermes-SuperAgent-World.git", "hongyaok/hermes-SuperAgent-World"),
        ("https://github.com/hongyaok/hermes-SuperAgent-World", "hongyaok/hermes-SuperAgent-World"),
        ("git@github.com:hongyaok/hermes-SuperAgent-World.git", "hongyaok/hermes-SuperAgent-World"),
        ("ssh://git@github.com/hongyaok/hermes-SuperAgent-World.git", "hongyaok/hermes-SuperAgent-World"),
        ("https://gitlab.com/hongyaok/other.git", ""),
        ("", ""),
    ],
)
def test_github_repo_from_origin_reads_both_remote_spellings(url, expected):
    assert sa_github.github_repo_from_origin(url) == expected
