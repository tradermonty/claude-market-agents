#!/usr/bin/env python3
"""Publish HTML reports to GitHub Pages via gh-pages branch.

Scans reports/ for .html files, generates a categorised index page,
and pushes everything to the gh-pages branch using git-worktree so
the main branch working tree is never touched.

A successful push is not the same as a published site: the GitHub Pages
build runs asynchronously and can stall for hours during a GitHub
incident. After pushing, the newly added reports are polled on the live
site and the script exits with EXIT_NOT_LIVE when they never appear, so
the caller can alert instead of silently reporting success.

Zero external dependencies – stdlib only.
"""

from __future__ import annotations

import argparse
import contextlib
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest.report_screening import ScreeningUnavailableError, validate_html_report_screening

# Exit code for "pushed to gh-pages, but the reports never went live".
# Distinct from 1 so callers can keep treating report generation as successful.
EXIT_NOT_LIVE = 3

DEFAULT_VERIFY_TIMEOUT = 300
DEFAULT_VERIFY_INTERVAL = 15
_HTTP_TIMEOUT = 20

# ---------------------------------------------------------------------------
# Category classification
# ---------------------------------------------------------------------------

CATEGORIES: list[tuple[str, re.Pattern[str]]] = [
    # Must precede "After-Market Reports": first match wins, and the ELI5
    # filename also contains "after-market".
    ("After-Market ELI5", re.compile(r"after[-_]market[-_]eli5", re.I)),
    ("After-Market Reports", re.compile(r"after[-_]market", re.I)),
    ("Earnings Trade Analysis", re.compile(r"^earnings_trade_analysis_", re.I)),
]

# Only these categories are published to GitHub Pages
PUBLISHED_CATEGORIES = {"After-Market Reports", "After-Market ELI5", "Earnings Trade Analysis"}

DATE_PATTERNS = [
    re.compile(r"(\d{4})-(\d{2})-(\d{2})"),
    re.compile(r"(\d{4})_(\d{2})_(\d{2})"),
    re.compile(r"(\d{4})(\d{2})(\d{2})"),
]


def classify(filename: str) -> str | None:
    """Return category name if the file belongs to a published category, else None."""
    for label, pattern in CATEGORIES:
        if pattern.search(filename):
            return label
    return None


def extract_date(filename: str) -> str | None:
    for pat in DATE_PATTERNS:
        m = pat.search(filename)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def extract_title(filepath: Path) -> str:
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            head = f.read(2048)
        m = re.search(r"<title>(.*?)</title>", head, re.I | re.S)
        if m:
            return html.unescape(m.group(1).strip())
    except OSError:
        pass
    return filepath.stem.replace("_", " ").replace("-", " ").title()


# ---------------------------------------------------------------------------
# Report scanning
# ---------------------------------------------------------------------------


def scan_reports(reports_dir: Path) -> list[dict]:
    reports = []
    for html_file in sorted(reports_dir.glob("*.html")):
        category = classify(html_file.name)
        if category is None:
            continue
        report_date = extract_date(html_file.name)
        if report_date is not None:
            source = (
                "earnings_trading_screener"
                if category == "Earnings Trade Analysis"
                else "earnings_afterhours_screener"
            )
            try:
                validate_html_report_screening(html_file, report_date, source)
            except ScreeningUnavailableError as exc:
                print(f"Skipping {html_file.name}: {exc}", file=sys.stderr)
                continue
        reports.append(
            {
                "path": html_file.name,
                "category": category,
                "date": report_date,
                "title": extract_title(html_file),
                "full_path": html_file,
            }
        )
    return reports


# ---------------------------------------------------------------------------
# Index HTML generation
# ---------------------------------------------------------------------------


def generate_index(reports: list[dict]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Group by category
    grouped: dict[str, list[dict]] = {}
    for r in reports:
        grouped.setdefault(r["category"], []).append(r)

    # Sort each group newest-first
    for items in grouped.values():
        items.sort(key=lambda r: r["date"] or "0000-00-00", reverse=True)

    # Desired category order
    cat_order = [
        "After-Market Reports",
        "After-Market ELI5",
        "Earnings Trade Analysis",
    ]
    ordered_cats = [c for c in cat_order if c in grouped]
    for c in grouped:
        if c not in ordered_cats:
            ordered_cats.append(c)

    # Build nav items and section HTML
    nav_items = []
    sections_html = []
    for i, cat in enumerate(ordered_cats):
        items = grouped[cat]
        cat_id = cat.lower().replace(" ", "-").replace(".", "")
        active = " active" if i == 0 else ""
        hidden = "" if i == 0 else " hidden"
        nav_items.append(
            f'<button class="nav-btn{active}" data-target="{cat_id}">'
            f'{html.escape(cat)}<span class="count-badge">{len(items)}</span></button>'
        )
        rows = []
        for r in items:
            date_badge = f'<span class="date-badge">{r["date"]}</span>' if r["date"] else ""
            safe_title = html.escape(r["title"])
            rows.append(
                f'<a href="{html.escape(r["path"])}" class="report-link" '
                f'data-title="{safe_title.lower()}" data-date="{r["date"] or ""}">'
                f'{date_badge}<span class="report-title">{safe_title}</span></a>'
            )
        sections_html.append(
            f'<div class="category-section{hidden}" id="section-{cat_id}" '
            f'data-category="{html.escape(cat)}">'
            f'<div class="report-list">{"".join(rows)}</div></div>'
        )

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trade Analysis Reports</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{
  font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif;
  background:linear-gradient(135deg,#0f0c29 0%,#302b63 50%,#24243e 100%);
  min-height:100vh;color:#e0e0e0
}}
.layout{{display:flex;min-height:100vh}}
/* --- Sidebar --- */
.sidebar{{
  width:260px;min-width:260px;
  background:rgba(0,0,0,0.35);
  backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
  border-right:1px solid rgba(255,255,255,0.08);
  display:flex;flex-direction:column;
  padding:24px 14px;position:sticky;top:0;height:100vh;
  overflow-y:auto
}}
.sidebar .logo{{
  font-size:1.3em;font-weight:700;
  background:linear-gradient(135deg,#667eea,#764ba2);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
  background-clip:text;margin-bottom:6px;text-align:center
}}
.sidebar .meta{{
  font-size:.75em;opacity:.5;text-align:center;margin-bottom:20px
}}
.nav-btn{{
  display:flex;align-items:center;justify-content:space-between;
  width:100%;padding:12px 16px;margin-bottom:6px;
  border:1px solid transparent;border-radius:10px;
  background:transparent;color:#b0b0cc;
  font-size:.92em;cursor:pointer;transition:all .15s;
  text-align:left
}}
.nav-btn:hover{{background:rgba(255,255,255,0.06);color:#e0e0f0}}
.nav-btn.active{{
  background:rgba(102,126,234,0.18);
  border-color:rgba(102,126,234,0.35);
  color:#fff
}}
.count-badge{{
  font-size:.7em;background:linear-gradient(135deg,#667eea,#764ba2);
  padding:2px 10px;border-radius:12px;color:#fff;flex-shrink:0
}}
.search-box{{
  width:100%;padding:10px 14px;font-size:.9em;
  border:1px solid rgba(255,255,255,0.12);
  border-radius:8px;margin-top:auto;
  background:rgba(255,255,255,0.05);color:#fff;
  outline:none;transition:border-color .2s
}}
.search-box:focus{{border-color:#667eea}}
.search-box::placeholder{{color:rgba(255,255,255,.35)}}
/* --- Main content --- */
.main{{flex:1;padding:28px 32px;overflow-y:auto}}
.category-section{{animation:fadeIn .2s ease}}
@keyframes fadeIn{{from{{opacity:0;transform:translateY(6px)}}to{{opacity:1;transform:translateY(0)}}}}
.report-list{{display:flex;flex-direction:column;gap:2px}}
.report-link{{
  display:flex;align-items:center;gap:12px;
  padding:11px 16px;border-radius:8px;
  text-decoration:none;color:#c8c8e0;
  transition:background .15s
}}
.report-link:hover{{background:rgba(255,255,255,0.07)}}
.date-badge{{
  font-size:.8em;font-family:monospace;
  background:rgba(102,126,234,0.2);
  padding:3px 10px;border-radius:6px;
  white-space:nowrap;min-width:96px;text-align:center
}}
.report-title{{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.hidden{{display:none}}
/* --- Mobile --- */
@media(max-width:768px){{
  .layout{{flex-direction:column}}
  .sidebar{{
    width:100%;min-width:0;position:relative;height:auto;
    flex-direction:row;flex-wrap:wrap;padding:14px 10px;gap:6px;
    align-items:center;justify-content:center
  }}
  .sidebar .logo{{width:100%;margin-bottom:4px}}
  .sidebar .meta{{width:100%;margin-bottom:8px}}
  .nav-btn{{width:auto;flex:0 1 auto}}
  .search-box{{margin-top:8px}}
  .main{{padding:16px 12px}}
}}
</style>
</head>
<body>
<div class="layout">
  <nav class="sidebar">
    <div class="logo">Trade Analysis</div>
    <div class="meta">{len(reports)} reports &middot; {now}</div>
    {"".join(nav_items)}
    <input type="text" class="search-box" placeholder="Search..." id="searchBox">
  </nav>
  <div class="main">
    {"".join(sections_html)}
  </div>
</div>
<script>
(function(){{
  const btns=document.querySelectorAll('.nav-btn');
  const secs=document.querySelectorAll('.category-section');
  const box=document.getElementById('searchBox');

  function showCategory(target){{
    secs.forEach(s=>s.classList.toggle('hidden',s.id!=='section-'+target));
    btns.forEach(b=>b.classList.toggle('active',b.dataset.target===target));
  }}
  btns.forEach(b=>b.addEventListener('click',function(){{
    box.value='';filterLinks('');
    showCategory(this.dataset.target);
  }}));

  function filterLinks(q){{
    secs.forEach(function(sec){{
      const links=sec.querySelectorAll('.report-link');
      links.forEach(function(a){{
        const match=!q||a.dataset.title.includes(q)||(a.dataset.date||'').includes(q);
        a.classList.toggle('hidden',!match);
      }});
    }});
  }}
  box.addEventListener('input',function(){{
    const q=this.value.toLowerCase().trim();
    if(q){{
      secs.forEach(s=>s.classList.remove('hidden'));
      btns.forEach(b=>b.classList.remove('active'));
    }} else {{
      showCategory(btns[0].dataset.target);
    }}
    filterLinks(q);
  }});
}})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Live verification: confirm GitHub Pages actually served the new reports
# ---------------------------------------------------------------------------

# Accepts scp-style, ssh:// and https:// remotes, including the
# `github.com-<alias>` host form produced by multi-account ~/.ssh/config setups.
GITHUB_REMOTE_RE = re.compile(
    r"^(?:git@|ssh://git@|https?://(?:[^@/]+@)?)"
    r"github\.com(?:-[A-Za-z0-9._-]+)?[:/]"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)


def derive_site_url(remote_url: str) -> str | None:
    """Map a GitHub remote URL to its GitHub Pages site URL, or None if not GitHub."""
    m = GITHUB_REMOTE_RE.match(remote_url.strip())
    if not m:
        return None
    owner = m.group("owner")
    repo = m.group("repo")
    root = f"https://{owner.lower()}.github.io/"
    # <owner>.github.io is the user/organisation site: the repo IS the site root.
    if repo.lower() == f"{owner.lower()}.github.io":
        return root
    return f"{root}{repo}/"


def parse_changed_reports(porcelain: str) -> list[str]:
    """Extract added/modified .html report names from `git status --porcelain` output.

    index.html is excluded: it changes on every run and says nothing about
    whether the new reports themselves went live.
    """
    changed = []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2], line[3:].strip()
        if "D" in status:
            continue
        if " -> " in path:  # rename: only the destination exists now
            path = path.split(" -> ", 1)[1].strip()
        if path.endswith(".html") and path != "index.html":
            changed.append(path)
    return changed


def resolve_site_url() -> str | None:
    """Derive the Pages URL from the origin remote, or None when unavailable."""
    try:
        return derive_site_url(run_git("remote", "get-url", "origin"))
    except RuntimeError:
        return None


def url_is_live(url: str) -> bool:
    """True when the URL responds 200 to a HEAD request."""
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def wait_until_live(
    url: str,
    *,
    timeout: float,
    interval: float,
    checker: Callable[[str], bool] = url_is_live,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Poll `url` until it is live or the timeout leaves no room for another wait."""
    deadline = clock() + timeout
    while True:
        if checker(url):
            return True
        if clock() + interval > deadline:
            return False
        sleeper(interval)


def verify_reports_live(
    site_url: str,
    filenames: list[str],
    *,
    timeout: float = DEFAULT_VERIFY_TIMEOUT,
    interval: float = DEFAULT_VERIFY_INTERVAL,
    checker: Callable[[str], bool] = url_is_live,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> list[str]:
    """Return the subset of `filenames` still not served by `site_url` within the budget.

    The timeout is a shared budget across all files, so verifying several
    reports cannot multiply the caller's wall-clock time.
    """
    deadline = clock() + timeout
    # Query string defeats a CDN-cached 404 from before the deploy landed.
    cache_buster = int(time.time())
    missing = []
    for name in filenames:
        url = f"{site_url}{name}?cb={cache_buster}"
        remaining = max(0.0, deadline - clock())
        live = wait_until_live(
            url,
            timeout=remaining,
            interval=interval,
            checker=checker,
            sleeper=sleeper,
            clock=clock,
        )
        if not live:
            missing.append(name)
    return missing


# ---------------------------------------------------------------------------
# Git operations: push to gh-pages via worktree
# ---------------------------------------------------------------------------


def run_git(*args: str, cwd: str | Path | None = None, env: dict | None = None) -> str:
    run_env = None
    if env:
        run_env = {**os.environ, **env}
    result = subprocess.run(
        ["git", *list(args)],
        cwd=cwd,
        env=run_env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def push_to_ghpages(
    reports_dir: Path, index_html: str, reports: list[dict], *, no_push: bool = False
) -> list[str]:
    """Push reports to gh-pages and return the report files this run added or changed.

    An empty list means there is nothing new to verify on the live site.
    """
    repo_root = Path(run_git("rev-parse", "--show-toplevel"))
    tmpdir = None
    worktree_path: Path | None = None
    try:
        tmpdir = tempfile.mkdtemp(prefix="ghpages_")
        worktree_path = Path(tmpdir) / "ghpages"

        # Check if gh-pages branch exists
        branches = run_git("branch", "--list", "gh-pages", cwd=repo_root)
        remote_branches = run_git("branch", "-r", "--list", "origin/gh-pages", cwd=repo_root)

        if branches.strip() or remote_branches.strip():
            run_git("worktree", "add", str(worktree_path), "gh-pages", cwd=repo_root)
        else:
            # Create orphan gh-pages branch
            run_git("worktree", "add", "--detach", str(worktree_path), cwd=repo_root)
            run_git("checkout", "--orphan", "gh-pages", cwd=worktree_path)

        # Clear worktree contents (except .git)
        for item in worktree_path.iterdir():
            if item.name == ".git":
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

        # Copy only published reports
        for r in reports:
            src = r["full_path"]
            dst = worktree_path / r["path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        # Write index.html
        (worktree_path / "index.html").write_text(index_html, encoding="utf-8")

        # Write .nojekyll
        (worktree_path / ".nojekyll").write_text("", encoding="utf-8")

        # Commit
        run_git("add", "-A", cwd=worktree_path)

        # Check if there are changes to commit
        status = run_git("status", "--porcelain", cwd=worktree_path)
        if not status:
            print("No changes to commit – gh-pages is up to date.")
            return []
        changed_reports = parse_changed_reports(status)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        run_git(
            "commit",
            "-m",
            f"Update reports – {now}",
            cwd=worktree_path,
            env={"PRE_COMMIT_ALLOW_NO_CONFIG": "1"},
        )

        if no_push:
            print("Committed to local gh-pages (--no-push). Skipping push.")
            return []

        run_git("push", "origin", "gh-pages", cwd=worktree_path)
        print("Pushed to origin/gh-pages.")
        return changed_reports

    finally:
        # Clean up worktree
        if worktree_path and worktree_path.exists():
            try:
                run_git("worktree", "remove", "--force", str(worktree_path), cwd=repo_root)
            except RuntimeError:
                # Fallback: manual cleanup
                shutil.rmtree(worktree_path, ignore_errors=True)
                with contextlib.suppress(RuntimeError):
                    run_git("worktree", "prune", cwd=repo_root)
        if tmpdir and os.path.isdir(tmpdir):
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish HTML reports to GitHub Pages")
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=Path("./reports"),
        help="Reports directory (default: ./reports)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Generate index.html only, no git operations"
    )
    parser.add_argument(
        "--no-push", action="store_true", help="Commit to local gh-pages but skip push"
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip the post-push check that the new reports are live on GitHub Pages",
    )
    parser.add_argument(
        "--site-url",
        help="GitHub Pages base URL (default: derived from the origin remote)",
    )
    parser.add_argument(
        "--verify-timeout",
        type=float,
        default=DEFAULT_VERIFY_TIMEOUT,
        help=f"Seconds to wait for the Pages deploy (default: {DEFAULT_VERIFY_TIMEOUT})",
    )
    parser.add_argument(
        "--verify-interval",
        type=float,
        default=DEFAULT_VERIFY_INTERVAL,
        help=f"Seconds between live checks (default: {DEFAULT_VERIFY_INTERVAL})",
    )
    args = parser.parse_args()

    reports_dir = args.reports_dir.resolve()
    if not reports_dir.is_dir():
        print(f"Error: reports directory not found: {reports_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {reports_dir} ...")
    reports = scan_reports(reports_dir)
    print(f"Found {len(reports)} HTML reports.")

    # Print category summary
    cats: dict[str, int] = {}
    for r in reports:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")

    index_html = generate_index(reports)

    if args.dry_run:
        out = reports_dir / "index.html"
        out.write_text(index_html, encoding="utf-8")
        print(f"\nDry-run: index.html written to {out}")
        return

    changed = push_to_ghpages(reports_dir, index_html, reports, no_push=args.no_push)
    print("Done.")

    if args.no_verify or not changed:
        return

    site_url = args.site_url or resolve_site_url()
    if not site_url:
        print("Warning: could not derive the GitHub Pages URL – skipping live verification.")
        return
    if not site_url.endswith("/"):
        site_url += "/"

    print(f"Verifying {len(changed)} report(s) at {site_url} ...")
    missing = verify_reports_live(
        site_url,
        changed,
        timeout=args.verify_timeout,
        interval=args.verify_interval,
    )
    if missing:
        print(
            f"Error: pushed to gh-pages, but {len(missing)} report(s) were still not served "
            f"after {args.verify_timeout:.0f}s: {', '.join(missing)}\n"
            "The GitHub Pages build is stalled or failed – check "
            "https://www.githubstatus.com/ and the repository's Pages build log.",
            file=sys.stderr,
        )
        sys.exit(EXIT_NOT_LIVE)
    print("All new reports are live on GitHub Pages.")


if __name__ == "__main__":
    main()
