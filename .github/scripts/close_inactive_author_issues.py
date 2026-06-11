#!/usr/bin/env python3
"""
在 GitHub Actions 中运行：按 Issue **评论时间线**上**最后一条评论**的发起人判断。

规则：
  · 若最后一条评论来自配置的维护者账号，且该评论距今 ≥ INACTIVE_DAYS（默认 30）天 →
    先发关闭说明评论，再关闭 Issue。
  · 若最后一条评论来自维护者，且距今 ≥ STALE_AFTER_DAYS（默认 21）天且 < 关闭阈值 →
    打上 STALE_LABEL（默认 stale），便于筛选「待跟进」。
  · 若最后一条评论**不是**维护者（社区/他人已回复），则移除 STALE_LABEL（若存在），避免误标。

环境变量：
  GITHUB_TOKEN / GH_TOKEN — 需 issues:write（Actions 自动注入；本地可省略）
  GITHUB_REPOSITORY — owner/repo（Actions 自动注入；本地未设时默认本仓库）
  MAINTAINERS — 逗号分隔的维护者登录名，默认 Imou-OpenPlatform
  STALE_AFTER_DAYS — 打 stale 标签的天数阈值，默认 21
  INACTIVE_DAYS — 关闭的天数阈值，默认 30（应 ≥ STALE_AFTER_DAYS）
  STALE_LABEL — 标签名，默认 stale（请在本仓库预先创建该 label，或首次在网页上建同名标签）
  INACTIVE_EXCLUDE_LABELS — 逗号分隔，命中则跳过（默认含 no-auto-close, enhancement）
  DRY_RUN — 设为 1 时只打印，不修改 Issue

本地未设置 GITHUB_TOKEN 时，会读取
  .cursor/skills/github-open-items-report/github_report.secrets.json
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

CLOSE_COMMENT = (
    "Closing issue due to inactivity. "
    "Feel free to reopen if you have any updates or further questions."
)

DEFAULT_OWNER = "Imou-OpenPlatform"
DEFAULT_REPO_NAME = "Imou-Home-Assistant"

_secrets_loaded = False
_secrets_token: str | None = None


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in (here.parent.parent, *here.parent.parents):
        if (p / "hacs.json").is_file() and (p / ".github").is_dir():
            return p
    return here.parent.parent


def _read_token_from_secrets_file() -> str | None:
    global _secrets_loaded, _secrets_token
    if _secrets_loaded:
        return _secrets_token
    _secrets_loaded = True
    p = (
        _repo_root()
        / ".cursor"
        / "skills"
        / "github-open-items-report"
        / "github_report.secrets.json"
    )
    if not p.is_file():
        _secrets_token = None
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _secrets_token = None
        return None
    for key in ("github_token", "GITHUB_TOKEN", "gh_token", "token"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            _secrets_token = v.strip()
            return _secrets_token
    _secrets_token = None
    return None


def _token() -> str:
    t = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if t:
        return t
    t = _read_token_from_secrets_file() or ""
    if t:
        return t
    print(
        "未找到 GitHub token：请设置环境变量 GITHUB_TOKEN / GH_TOKEN，或在仓库根下\n"
        "  .cursor/skills/github-open-items-report/github_report.secrets.json\n"
        "中写入 github_token（与开放项汇报脚本共用）。",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _github_token_is_example_placeholder(t: str) -> bool:
    return bool(re.fullmatch(r"ghp_[xX]{36}", t.strip()))


def _repo() -> tuple[str, str]:
    r = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if "/" in r:
        a, b = r.split("/", 1)
        return a, b
    return DEFAULT_OWNER, DEFAULT_REPO_NAME


def _maintainers() -> frozenset[str]:
    raw = (os.environ.get("MAINTAINERS") or "Imou-OpenPlatform").strip()
    return frozenset(x.strip().lower() for x in raw.split(",") if x.strip())


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name) or str(default)))
    except ValueError:
        return default


def _close_days() -> int:
    return _int_env("INACTIVE_DAYS", 30, minimum=1)


def _stale_days() -> int:
    return _int_env("STALE_AFTER_DAYS", 21, minimum=1)


def _stale_label_name() -> str:
    return (os.environ.get("STALE_LABEL") or "stale").strip() or "stale"


def _exclude_labels() -> frozenset[str]:
    raw = (os.environ.get("INACTIVE_EXCLUDE_LABELS") or "no-auto-close,enhancement").strip()
    return frozenset(x.strip().lower() for x in raw.split(",") if x.strip())


def _dry_run() -> bool:
    return os.environ.get("DRY_RUN", "").strip() in ("1", "true", "yes")


def _issue_label_names_lower(it: dict) -> set[str]:
    return {
        (lb.get("name") or "").lower()
        for lb in (it.get("labels") or [])
        if isinstance(lb, dict)
    }


def gh(method: str, url: str, *, data: dict | list | None = None) -> object:
    token = _token()
    body_bytes = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body_bytes,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "close-inactive-author-issues",
            "X-GitHub-Api-Version": "2022-11-28",
            **({} if body_bytes is None else {"Content-Type": "application/json"}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print(
                "GitHub 返回 401：token 无效、已撤销，或仍是示例占位符（ghp_+36 个 x）。\n"
                "请换用 Settings → Developer settings → Personal access tokens 里生成的真实 PAT，\n"
                "写入 github_report.secrets.json 的 github_token，或导出 GITHUB_TOKEN 后再运行。",
                file=sys.stderr,
            )
        raise


def gh_delete(url: str) -> None:
    token = _token()
    req = urllib.request.Request(
        url,
        method="DELETE",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "close-inactive-author-issues",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print(
                "GitHub 返回 401：请检查 token 与仓库权限（issues: write）。",
                file=sys.stderr,
            )
        raise


def list_open_issues(owner: str, repo: str) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        q = urllib.parse.urlencode(
            {"state": "open", "per_page": "100", "page": str(page)}
        )
        url = f"https://api.github.com/repos/{owner}/{repo}/issues?{q}"
        batch = gh("GET", url)
        if not isinstance(batch, list) or not batch:
            break
        for it in batch:
            if "pull_request" in it:
                continue
            out.append(it)
        if len(batch) < 100:
            break
        page += 1
        time.sleep(0.25)
    return out


def list_issue_comments(owner: str, repo: str, number: int) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        q = urllib.parse.urlencode({"per_page": "100", "page": str(page)})
        url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}/comments?{q}"
        batch = gh("GET", url)
        if not isinstance(batch, list) or not batch:
            break
        out.extend(batch)
        if len(batch) < 100:
            break
        page += 1
        time.sleep(0.15)
    return out


def parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def add_issue_label(owner: str, repo: str, number: int, label: str) -> None:
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}/labels"
    gh("POST", url, data={"labels": [label]})


def remove_issue_label(owner: str, repo: str, number: int, label: str) -> None:
    enc = urllib.parse.quote(label, safe="")
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}/labels/{enc}"
    gh_delete(url)


def main() -> None:
    tok = _token()
    if _github_token_is_example_placeholder(tok):
        print(
            "当前 token 为示例占位符（ghp_ 后跟 36 个 x），无法调用 API。\n"
            "请将真实 PAT 写入 github_report.secrets.json，或设置 GITHUB_TOKEN。",
            file=sys.stderr,
        )
        raise SystemExit(2)

    owner, repo = _repo()
    maintainers = _maintainers()
    close_days = _close_days()
    stale_days = _stale_days()
    if stale_days > close_days:
        print(
            f"警告：STALE_AFTER_DAYS（{stale_days}）> INACTIVE_DAYS（{close_days}），"
            f"将按 STALE_AFTER_DAYS={close_days} 处理打标区间。",
            file=sys.stderr,
        )
        stale_days = close_days

    stale_label = _stale_label_name()
    stale_label_l = stale_label.lower()
    exclude = _exclude_labels()
    dry = _dry_run()
    now = datetime.now(timezone.utc)
    close_cutoff = now - timedelta(days=close_days)
    stale_cutoff = now - timedelta(days=stale_days)

    issues = list_open_issues(owner, repo)
    n_closed = n_stale_added = n_stale_removed = 0

    for it in issues:
        num = it["number"]
        labels_l = _issue_label_names_lower(it)
        if exclude and any(lb in exclude for lb in labels_l):
            continue

        comments = list_issue_comments(owner, repo, num)
        if not comments:
            continue

        comments.sort(key=lambda c: parse_dt(c["created_at"]))
        last = comments[-1]
        last_login = ((last.get("user") or {}).get("login") or "").lower()
        last_ca = last.get("created_at")
        if not last_ca:
            continue
        last_t = parse_dt(last_ca)

        # 最后一条不是维护者：线程已由他人跟进，去掉误打的 stale
        if last_login not in maintainers:
            if stale_label_l in labels_l:
                msg = f"{'[dry-run] ' if dry else ''}Remove label {stale_label!r} from #{num} (last comment by non-maintainer)"
                print(msg)
                if not dry:
                    try:
                        remove_issue_label(owner, repo, num, stale_label)
                    except urllib.error.HTTPError as e:
                        print(f"WARN: remove label #{num}: {e}", file=sys.stderr)
                    time.sleep(0.25)
                n_stale_removed += 1
            continue

        # 最后一条是维护者
        if last_t > close_cutoff:
            # 未满关闭天数：若已早于 stale 阈值则打 stale；若未满 stale 天则去掉 stale（维护者刚回复）
            if last_t <= stale_cutoff:
                if stale_label_l not in labels_l:
                    msg = f"{'[dry-run] ' if dry else ''}Add label {stale_label!r} to #{num} (last=maintainer, age>={stale_days}d)"
                    print(msg)
                    if not dry:
                        try:
                            add_issue_label(owner, repo, num, stale_label)
                        except urllib.error.HTTPError as e:
                            print(f"WARN: add label #{num}: {e}", file=sys.stderr)
                        time.sleep(0.25)
                    n_stale_added += 1
            else:
                if stale_label_l in labels_l:
                    msg = f"{'[dry-run] ' if dry else ''}Remove label {stale_label!r} from #{num} (maintainer replied within {stale_days}d)"
                    print(msg)
                    if not dry:
                        try:
                            remove_issue_label(owner, repo, num, stale_label)
                        except urllib.error.HTTPError as e:
                            print(f"WARN: remove label #{num}: {e}", file=sys.stderr)
                        time.sleep(0.25)
                    n_stale_removed += 1
            continue

        # 最后一条是维护者且已满关闭天数
        print(
            f"{'[dry-run] ' if dry else ''}Close #{num} "
            f"(last comment by maintainer on {last_t.date()}, >= {close_days}d)"
        )
        if dry:
            n_closed += 1
            time.sleep(0.1)
            continue

        c_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{num}/comments"
        try:
            gh("POST", c_url, data={"body": CLOSE_COMMENT})
        except urllib.error.HTTPError as e:
            print(f"WARN: comment failed #{num}: {e}", file=sys.stderr)
            continue
        time.sleep(0.35)
        p_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{num}"
        try:
            gh("PATCH", p_url, data={"state": "closed"})
        except urllib.error.HTTPError as e:
            print(f"WARN: close failed #{num}: {e}", file=sys.stderr)
            continue
        n_closed += 1
        time.sleep(0.35)

    print(
        f"Done. closed={n_closed}, stale_added={n_stale_added}, stale_removed={n_stale_removed}"
        f"{' (dry-run)' if dry else ''}"
    )


if __name__ == "__main__":
    main()
