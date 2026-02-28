#!/usr/bin/env python3
#  Orchestration - UI Inspector
#
#  Loads a page in headless Chromium via Playwright and dumps the
#  accessibility tree as structured text.  Designed for AI consumption:
#  the output lets an LLM spot layout bugs, overlapping elements,
#  missing content, and broken navigation without a screenshot.
#
#  Depends on: playwright (pip install playwright && playwright install chromium)
#  Used by:    AI agents during UI development / debugging

"""
Usage:
    python tools/ui_inspect.py [URL] [OPTIONS]

    URL defaults to http://localhost:5173 (Vite dev server).

Options:
    --wait SELECTOR     Wait for a CSS selector before dumping (e.g. ".task-card")
    --timeout MS        Navigation timeout in milliseconds (default: 10000)
    --viewport WxH      Viewport size (default: 1280x720)
    --full              Dump full accessibility tree (verbose)
    --boxes             Include bounding boxes for every element
    --cookies FILE      Load cookies from a JSON file (for authenticated pages)
    --auth USER:PASS    Auto-login via /api/auth/login before navigating

Examples:
    python tools/ui_inspect.py
    python tools/ui_inspect.py http://localhost:5173/projects/1 --wait ".task-card"
    python tools/ui_inspect.py http://localhost:5173/admin --auth admin@test.com:password
    python tools/ui_inspect.py --boxes --viewport 1920x1080
"""

import argparse
import json
import sys


def parse_args():
    parser = argparse.ArgumentParser(description="Dump page accessibility tree for AI inspection")
    parser.add_argument("url", nargs="?", default="http://localhost:5173", help="URL to inspect")
    parser.add_argument("--wait", metavar="SELECTOR", help="Wait for CSS selector before dump")
    parser.add_argument("--timeout", type=int, default=10000, help="Navigation timeout (ms)")
    parser.add_argument("--viewport", default="1280x720", help="Viewport WxH")
    parser.add_argument("--full", action="store_true", help="Full accessibility tree (verbose)")
    parser.add_argument("--boxes", action="store_true", help="Include bounding boxes")
    parser.add_argument("--cookies", metavar="FILE", help="Cookie JSON file")
    parser.add_argument("--auth", metavar="USER:PASS", help="Auto-login via /api/auth/login")
    return parser.parse_args()


def _login(page, base_url: str, credentials: str):
    """POST to /api/auth/login and set the JWT as a cookie."""
    user, password = credentials.split(":", 1)
    resp = page.request.post(
        f"{base_url}/api/auth/login",
        data=json.dumps({"email": user, "password": password}),
        headers={"Content-Type": "application/json"},
    )
    if resp.status != 200:
        print(f"ERROR: Login failed ({resp.status}): {resp.text()}", file=sys.stderr)
        sys.exit(1)
    body = resp.json()
    token = body.get("access_token") or body.get("token")
    if not token:
        print(f"ERROR: No token in login response: {body}", file=sys.stderr)
        sys.exit(1)
    # Store token in localStorage so the React app picks it up
    page.evaluate(f"localStorage.setItem('token', '{token}')")


def _walk_tree(node, depth=0, include_boxes=False):
    """Recursively walk the accessibility tree and yield formatted lines."""
    role = node.get("role", "")
    name = node.get("name", "")

    # Skip generic/empty nodes unless they have interesting children
    skip_roles = {"none", "generic", "LineBreak"}
    children = node.get("children", [])

    if role in skip_roles and not name:
        for child in children:
            yield from _walk_tree(child, depth, include_boxes)
        return

    indent = "  " * depth
    parts = [f"{indent}[{role}]"]

    if name:
        # Truncate long names
        display_name = name if len(name) <= 80 else name[:77] + "..."
        parts.append(f'"{display_name}"')

    # Key properties
    props = []
    if node.get("disabled"):
        props.append("disabled")
    if node.get("checked") is not None:
        props.append(f"checked={node['checked']}")
    if node.get("expanded") is not None:
        props.append(f"expanded={node['expanded']}")
    if node.get("selected"):
        props.append("selected")
    if node.get("level"):
        props.append(f"level={node['level']}")
    if node.get("valuetext"):
        props.append(f"value={node['valuetext']}")

    if props:
        parts.append(f"({', '.join(props)})")

    if include_boxes and "boundingBox" in node:
        box = node["boundingBox"]
        if box:
            parts.append(f"@ x={box['x']:.0f} y={box['y']:.0f} w={box['width']:.0f} h={box['height']:.0f}")

    yield " ".join(parts)

    for child in children:
        yield from _walk_tree(child, depth + 1, include_boxes)


def _detect_overlaps(snapshot):
    """Find elements with bounding boxes that overlap significantly."""
    elements = []
    _collect_boxes(snapshot, elements)

    overlaps = []
    for i, (a_desc, a_box) in enumerate(elements):
        for b_desc, b_box in elements[i + 1:]:
            overlap = _rect_overlap(a_box, b_box)
            if overlap > 0.3:  # > 30% overlap
                overlaps.append((a_desc, b_desc, f"{overlap:.0%}"))

    return overlaps


def _collect_boxes(node, result, depth=0):
    """Collect elements that have bounding boxes."""
    role = node.get("role", "")
    name = node.get("name", "")
    box = node.get("boundingBox")

    if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
        desc = f"[{role}] \"{name[:40]}\"" if name else f"[{role}]"
        result.append((desc, box))

    for child in node.get("children", []):
        _collect_boxes(child, result, depth + 1)


def _rect_overlap(a, b):
    """Calculate overlap ratio between two bounding boxes (0.0 to 1.0)."""
    x_overlap = max(0, min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"]))
    y_overlap = max(0, min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"]))
    intersection = x_overlap * y_overlap
    if intersection == 0:
        return 0.0
    smaller_area = min(a["width"] * a["height"], b["width"] * b["height"])
    if smaller_area == 0:
        return 0.0
    return intersection / smaller_area


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright not installed.", file=sys.stderr)
        print("  pip install playwright && playwright install chromium", file=sys.stderr)
        sys.exit(1)

    args = parse_args()

    width, height = (int(x) for x in args.viewport.split("x"))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": width, "height": height})

        # Load cookies if provided
        if args.cookies:
            with open(args.cookies, encoding="utf-8") as f:
                cookies = json.load(f)
            context.add_cookies(cookies)

        page = context.new_page()

        # Auto-login if credentials provided
        if args.auth:
            base_url = args.url.split("/", 3)
            base_url = f"{base_url[0]}//{base_url[2]}" if len(base_url) >= 3 else args.url
            _login(page, base_url, args.auth)

        # Navigate
        print(f"Loading {args.url} ({width}x{height})...", file=sys.stderr)
        try:
            page.goto(args.url, timeout=args.timeout, wait_until="networkidle")
        except Exception as e:
            print(f"ERROR: Navigation failed: {e}", file=sys.stderr)
            sys.exit(1)

        # Wait for specific element if requested
        if args.wait:
            try:
                page.wait_for_selector(args.wait, timeout=args.timeout)
            except Exception:
                print(f"WARNING: Selector '{args.wait}' not found within timeout", file=sys.stderr)

        # Get accessibility snapshot
        snapshot = page.accessibility.snapshot()
        if not snapshot:
            print("ERROR: No accessibility tree returned (page may be empty)", file=sys.stderr)
            sys.exit(1)

        # Print page title and URL
        print(f"Page: {page.title()}")
        print(f"URL:  {page.url}")
        print(f"Viewport: {width}x{height}")
        print("---")

        # Print tree
        for line in _walk_tree(snapshot, include_boxes=args.boxes or args.full):
            print(line)

        # Overlap detection (always run when boxes are available)
        if args.boxes or args.full:
            overlaps = _detect_overlaps(snapshot)
            if overlaps:
                print("\n--- OVERLAP WARNINGS ---")
                for a, b, pct in overlaps:
                    print(f"  {a}  overlaps  {b}  ({pct})")

        browser.close()


if __name__ == "__main__":
    main()
