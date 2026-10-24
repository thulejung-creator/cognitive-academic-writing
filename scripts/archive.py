#!/usr/bin/env python3
"""Validate and archive a project plan using Python's standard library only."""
import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

CATEGORIES = {"research", "programming", "writing", "analysis", "design", "operations"}
STATES = {"pending", "ready", "running", "blocked", "done", "cancelled"}
SKILL_STATES = {"candidate", "rejected", "reused", "installed", "unavailable"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
 status TEXT NOT NULL, data_json TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS contracts(
 project_id TEXT NOT NULL REFERENCES projects(id), version INTEGER NOT NULL,
 status TEXT NOT NULL, data_json TEXT NOT NULL, digest TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(project_id,version));
CREATE TABLE IF NOT EXISTS units(
 project_id TEXT NOT NULL REFERENCES projects(id), id TEXT NOT NULL,
 category TEXT NOT NULL, status TEXT NOT NULL, action TEXT NOT NULL,
 data_json TEXT NOT NULL, PRIMARY KEY(project_id,id));
CREATE TABLE IF NOT EXISTS dependencies(
 project_id TEXT NOT NULL, unit_id TEXT NOT NULL, depends_on TEXT NOT NULL,
 PRIMARY KEY(project_id,unit_id,depends_on),
 FOREIGN KEY(project_id,unit_id) REFERENCES units(project_id,id),
 FOREIGN KEY(project_id,depends_on) REFERENCES units(project_id,id));
CREATE TABLE IF NOT EXISTS skills(
 project_id TEXT NOT NULL REFERENCES projects(id), id TEXT NOT NULL,
 name TEXT NOT NULL, status TEXT NOT NULL, data_json TEXT NOT NULL,
 PRIMARY KEY(project_id,id));
CREATE TABLE IF NOT EXISTS artifacts(
 project_id TEXT NOT NULL REFERENCES projects(id), path TEXT NOT NULL,
 exists_on_disk INTEGER NOT NULL, sha256 TEXT, data_json TEXT NOT NULL,
 PRIMARY KEY(project_id,path));
CREATE TABLE IF NOT EXISTS git_snapshots(
 id INTEGER PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 digest TEXT NOT NULL, data_json TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(project_id,digest));
CREATE TABLE IF NOT EXISTS revisions(
 id INTEGER PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 digest TEXT NOT NULL, manifest_json TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(project_id,digest));
"""


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def digest(value):
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text_fields(obj, fields, context):
    require(isinstance(obj, dict), f"{context}: expected object")
    for key in fields:
        require(isinstance(obj.get(key), str) and obj[key].strip(),
                f"{context}.{key}: nonempty text required")


def list_fields(obj, fields, context, nonempty=True):
    for key in fields:
        value = obj.get(key)
        require(isinstance(value, list), f"{context}.{key}: expected list")
        require(not nonempty or value, f"{context}.{key}: must not be empty")
        require(all(isinstance(x, str) and x.strip() for x in value),
                f"{context}.{key}: expected nonempty strings")


def inside(root, relative):
    require(isinstance(relative, str) and relative.strip(), "path: nonempty text required")
    item = Path(relative)
    require(not item.is_absolute() and not item.drive and not PureWindowsPath(relative).drive
            and ":" not in relative and ".." not in item.parts and ".." not in PureWindowsPath(relative).parts,
            f"path must be relative without traversal: {relative}")
    result = (root / item).resolve()
    require(result.is_relative_to(root), f"path escapes project: {relative}")
    return result


def validate_original(data, root):
    require(isinstance(data, dict), "manifest must be an object")
    p, c = data.get("project"), data.get("contract")
    text_fields(p, ["id", "name", "request", "category", "summary", "status"], "project")
    require(p["category"] in CATEGORIES, "project.category: unsupported category")
    require(set(p.get("secondary_categories", [])) <= CATEGORIES,
            "project.secondary_categories: unsupported category")
    text_fields(c, ["status", "goal", "change_policy"], "contract")
    require(type(c.get("version")) is int and c["version"] >= 1,
            "contract.version: positive integer required")
    require(c["status"] in {"draft", "agreed", "superseded"}, "invalid contract status")
    list_fields(c, ["scope", "deliverables", "acceptance", "responsibilities",
                    "constraints", "milestones"], "contract")
    if c["status"] == "agreed":
        text_fields(c, ["agreement_evidence"], "contract")
    for key in ("units", "skills", "artifacts"):
        require(isinstance(data.get(key), list), f"{key}: expected list")
    require(data["units"], "units: at least one execution unit required")
    by_id = {}
    for unit in data["units"]:
        text_fields(unit, ["id", "category", "action", "status"], "unit")
        require(unit["id"] not in by_id, f"duplicate unit: {unit['id']}")
        require(unit["category"] in CATEGORIES, f"invalid unit category: {unit['id']}")
        require(unit["status"] in STATES, f"invalid unit status: {unit['id']}")
        list_fields(unit, ["inputs", "outputs", "acceptance"], unit["id"])
        list_fields(unit, ["depends_on"], unit["id"], nonempty=False)
        require(len(set(unit["depends_on"])) == len(unit["depends_on"]), "duplicate dependency")
        if unit["status"] == "done":
            list_fields(unit, ["evidence"], unit["id"])
        if unit["status"] == "blocked":
            text_fields(unit, ["blocker"], unit["id"])
        by_id[unit["id"]] = unit
    visiting, visited = set(), set()

    def visit(uid):
        require(uid in by_id, f"unknown dependency: {uid}")
        require(uid not in visiting, f"cyclic dependency: {uid}")
        if uid in visited:
            return
        visiting.add(uid)
        for dep in by_id[uid]["depends_on"]:
            visit(dep)
            if by_id[uid]["status"] in {"ready", "running", "done"}:
                require(by_id[dep]["status"] == "done", f"unfinished dependency: {uid} -> {dep}")
        visiting.remove(uid)
        visited.add(uid)

    for uid in by_id:
        visit(uid)
    skill_ids = set()
    for s in data["skills"]:
        text_fields(s, ["id", "name", "status", "workflow", "review"], "skill")
        require(s["id"] not in skill_ids, f"duplicate skill: {s['id']}")
        skill_ids.add(s["id"])
        require(s["status"] in SKILL_STATES, "invalid skill status")
        list_fields(s, ["unit_ids"], s["id"], nonempty=False)
        require(set(s["unit_ids"]) <= set(by_id), f"skill refers to unknown unit: {s['id']}")
        if s["status"] == "installed":
            text_fields(s, ["local_path"], s["id"])
            folder = inside(root, s["local_path"])
            entry = inside(root, str(Path(s["local_path"]) / "SKILL.md"))
            require(entry.is_file(), "missing installed SKILL.md")
            if s.get("source_kind", "github") == "local":
                text_fields(s, ["content_sha256"], s["id"])
                require(re.fullmatch(r"[0-9a-fA-F]{64}", s["content_sha256"]), "local skill needs SHA-256")
                actual = hashlib.sha256(entry.read_bytes()).hexdigest()
                require(actual == s["content_sha256"].lower(), "local skill checksum mismatch")
            else:
                text_fields(s, ["source_url", "commit", "license"], s["id"])
                require(s["source_url"].startswith("https://github.com/"), "installed skill needs GitHub URL")
                require(re.fullmatch(r"[0-9a-fA-F]{40}", s["commit"]), "installed skill needs commit SHA")
    paths = set()
    for a in data["artifacts"]:
        text_fields(a, ["path", "kind", "description"], "artifact")
        require(a["path"] not in paths, "duplicate artifact path")
        paths.add(a["path"])
        file = inside(root, a["path"])
        require(not file.exists() or file.is_file(), "artifact must be a file")
        a["exists"] = file.is_file()
        a["sha256"] = None
        if a["exists"]:
            with file.open("rb") as stream:
                a["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    return data



PRIVATE_KEYS = {"cognitive_model", "cognitive_profile", "collaboration_history",
                "private_notes", "reviewer_private_history", "credentials", "api_key", "password"}


def reject_private_keys(value):
    if isinstance(value, dict):
        require(not (set(value) & PRIVATE_KEYS), "private-state fields are not project records")
        for item in value.values():
            reject_private_keys(item)
    elif isinstance(value, list):
        for item in value:
            reject_private_keys(item)


def allowed_fields(obj, required, optional, context):
    require(isinstance(obj, dict), context + ": expected object")
    require(set(required) <= set(obj) <= set(required) | set(optional),
            context + ": missing or unsupported fields")


def validate(data, root):
    root = root.resolve()
    require(root.is_dir(), "project root must exist")
    reject_private_keys(data)
    allowed_fields(data, {"project", "contract", "units", "skills", "artifacts"}, set(), "manifest")
    p = data["project"]
    allowed_fields(p, {"id", "name", "request", "category", "summary", "status"},
                   {"secondary_categories", "assumptions", "preparation", "recommended_flow", "git_note"}, "project")
    for field in ("secondary_categories", "assumptions", "recommended_flow"):
        if field in p:
            list_fields(p, [field], "project", nonempty=False)
    if "git_note" in p:
        require(isinstance(p["git_note"], str), "project.git_note: expected string")
    if "preparation" in p:
        require(isinstance(p["preparation"], list), "project.preparation: expected list")
        for item in p["preparation"]:
            allowed_fields(item, {"item", "status", "detail"}, set(), "preparation")
            text_fields(item, ["item", "status"], "preparation")
            require(isinstance(item["detail"], str), "preparation.detail: expected string")
    c = data["contract"]
    allowed_fields(c, {"version", "status", "goal", "scope", "deliverables", "acceptance",
                      "responsibilities", "constraints", "milestones", "change_policy"},
                   {"agreement_evidence"}, "contract")
    if "agreement_evidence" in c:
        require(isinstance(c["agreement_evidence"], str), "agreement evidence: expected string")
    for name in ("units", "skills", "artifacts"):
        require(isinstance(data[name], list), name + ": expected list")
    for unit in data["units"]:
        allowed_fields(unit, {"id", "category", "action", "inputs", "outputs", "depends_on", "acceptance", "status"},
                       {"evidence", "blocker"}, "unit")
        if "evidence" in unit:
            list_fields(unit, ["evidence"], "unit", nonempty=False)
        if "blocker" in unit:
            require(isinstance(unit["blocker"], str), "unit.blocker: expected string")
    for skill in data["skills"]:
        allowed_fields(skill, {"id", "name", "status", "workflow", "review", "unit_ids"},
                       {"source_url", "commit", "license", "local_path", "source_kind", "content_sha256"}, "skill")
        require(skill.get("source_kind", "github") in {"github", "local"}, "unsupported skill source kind")
        for key in ("source_url", "commit", "license", "local_path", "content_sha256"):
            if key in skill:
                require(isinstance(skill[key], str), "skill provenance fields must be strings")
    for artifact in data["artifacts"]:
        allowed_fields(artifact, {"path", "kind", "description"}, {"exists", "sha256"}, "artifact")
        resolved = inside(root, artifact["path"])
        archive = (root / ".project-archive").resolve()
        require(not resolved.is_relative_to(archive), "do not hash the archive as its own artifact")
    return validate_original(data, root)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON object key")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("non-finite JSON constants are unsupported")


def read_plan(path):
    return json.loads(path.read_text(encoding="utf-8-sig"),
                      object_pairs_hook=unique_object, parse_constant=reject_constant)


def read_git(root):
    def run(*args):
        result = subprocess.run(["git", "--no-optional-locks", "-C", str(root), *args], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=20)
        if result.returncode:
            raise ValueError(result.stderr.strip() or "Git command failed")
        return result.stdout.strip()
    try:
        repo = run("rev-parse", "--show-toplevel")
        return {"available": True, "repo_root": repo, "commit": run("rev-parse", "HEAD"),
                "branch": run("branch", "--show-current") or "detached",
                "dirty": bool(run("status", "--porcelain"))}
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return {"available": False, "reason": str(error)}


def archive_folder(root):
    root = root.resolve()
    raw = root / ".project-archive"
    require(not raw.is_symlink(), "archive folder must not be a symlink")
    folder = inside(root, ".project-archive")
    for name in ("archive.sqlite3", "project.json", "PROJECT.md"):
        require(not (raw / name).is_symlink(), "archive files must not be symlinks")
        inside(root, ".project-archive/" + name)
    return folder


def render(data, git, counts):
    p, c = data["project"], data["contract"]
    done = sum(u["status"] == "done" for u in data["units"])
    lines = ["# " + p["name"], "", p["summary"], "",
             f"Project: {p['id']} | Category: {p['category']} | Status: {p['status']}",
             f"Units completed: {done}/{len(data['units'])} | Stored revisions: {counts['revisions']}",
             "", "## Task request", "", p["request"], ""]

    def section(title, value):
        lines.extend(["## " + title, ""])
        if isinstance(value, list):
            for item in value:
                lines.append("- " + (item if isinstance(item, str) else encode(item)))
        else:
            lines.append(str(value))
        lines.append("")

    for key, title in (("assumptions", "Assumptions and gaps"), ("preparation", "Preparation"),
                       ("recommended_flow", "Recommended sequence")):
        section(title, p.get(key, []))
    section("Execution units", [encode(unit) for unit in data["units"]])
    section("Capabilities and integration", [encode(skill) for skill in data["skills"]])
    section("Observed Git state", encode(git))
    if p.get("git_note"):
        section("Snapshot coverage", p["git_note"])
    section("Artifacts", [encode(item) for item in data["artifacts"]])
    section(f"Working agreement v{c['version']} ({c['status']})", encode(c))
    lines.extend(["Agreement status is operational metadata, not proof of legal signature.", ""])
    active = [u for u in data["units"] if u["status"] in {"ready", "running"}]
    remaining = [u for u in data["units"] if u["status"] in {"pending", "blocked"}]
    section("Next actions", [f"{u['id']}: {u['action']} ({u['status']})" for u in (active or remaining)]
            or ["All current units have ended."])
    lines.extend(["Archive records index project history; preserve deliverable files separately.", ""])
    return "\n".join(lines)


def atomic_write(file, content):
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=file.parent,
                                         prefix=file.name + ".", suffix=".tmp", delete=False) as stream:
            temp_path = Path(stream.name)
            stream.write(content)
        temp_path.replace(file)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def read_snapshot(root):
    folder = archive_folder(root)
    db = folder / "archive.sqlite3"
    require(db.is_file(), "archive database not found; run authorized sync first")
    require(not any(Path(str(db) + suffix).exists() for suffix in ("-wal", "-journal")),
            "archive has a live or unfinished journal; inspect after the writer closes")
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro&immutable=1", uri=True)) as con:
        require(con.execute("PRAGMA user_version").fetchone()[0] == 1, "unsupported database schema")
        rows = con.execute("SELECT id,data_json FROM projects").fetchall()
        require(len(rows) == 1, "expected exactly one project record")
        pid, raw = rows[0]
        git_row = con.execute("SELECT data_json FROM git_snapshots WHERE project_id=? ORDER BY id DESC LIMIT 1",
                              (pid,)).fetchone()
        counts = {"revisions": con.execute("SELECT count(*) FROM revisions WHERE project_id=?", (pid,)).fetchone()[0]}
    return json.loads(raw), json.loads(git_row[0]) if git_row else {}, counts


def show(root):
    data, git, counts = read_snapshot(root)
    return render(data, git, counts)


def export(root, *, allow_project_archive=False):
    require(allow_project_archive is True, "export requires explicit project archive permission")
    data, git, counts = read_snapshot(root)
    folder = archive_folder(root)
    atomic_write(folder / "project.json", encode(data) + "\n")
    atomic_write(folder / "PROJECT.md", render(data, git, counts))
    return {"project": data["project"]["id"], "database": str(folder / "archive.sqlite3"),
            "overview": str(folder / "PROJECT.md"), **counts}


def sync(root, input_file, *, allow_project_archive=False):
    require(allow_project_archive is True, "sync requires explicit project archive permission")
    root = root.resolve()
    data = validate(read_plan(input_file), root)
    git = read_git(root)
    folder = archive_folder(root)
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    p, c = data["project"], data["contract"]
    pid = p["id"]
    with closing(sqlite3.connect(folder / "archive.sqlite3")) as con, con:
        require(con.execute("PRAGMA user_version").fetchone()[0] in (0, 1), "unsupported database schema")
        con.execute("PRAGMA foreign_keys=ON")
        con.executescript(SCHEMA)
        existing = con.execute("SELECT id FROM projects").fetchall()
        require(not existing or existing == [(pid,)], "archive already belongs to another project")
        old = con.execute("SELECT version,digest FROM contracts WHERE project_id=? ORDER BY version DESC LIMIT 1",
                          (pid,)).fetchone()
        if old:
            require(c["version"] >= old[0], "contract version cannot decrease")
            require(c["version"] > old[0] or digest(c) == old[1], "contract changed: increment version")
        con.execute("INSERT INTO projects VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,"
                    "category=excluded.category,status=excluded.status,data_json=excluded.data_json,updated_at=excluded.updated_at",
                    (pid, p["name"], p["category"], p["status"], encode(data), now))
        con.execute("INSERT OR IGNORE INTO contracts VALUES(?,?,?,?,?,?)",
                    (pid, c["version"], c["status"], encode(c), digest(c), now))
        for table in ("dependencies", "units", "skills", "artifacts"):
            con.execute(f"DELETE FROM {table} WHERE project_id=?", (pid,))
        for u in data["units"]:
            con.execute("INSERT INTO units VALUES(?,?,?,?,?,?)",
                        (pid, u["id"], u["category"], u["status"], u["action"], encode(u)))
        for u in data["units"]:
            con.executemany("INSERT INTO dependencies VALUES(?,?,?)", [(pid, u["id"], dep) for dep in u["depends_on"]])
        for s in data["skills"]:
            con.execute("INSERT INTO skills VALUES(?,?,?,?,?)", (pid, s["id"], s["name"], s["status"], encode(s)))
        for a in data["artifacts"]:
            con.execute("INSERT INTO artifacts VALUES(?,?,?,?,?)", (pid, a["path"], int(a["exists"]), a["sha256"], encode(a)))
        con.execute("INSERT OR IGNORE INTO revisions(project_id,digest,manifest_json,created_at) VALUES(?,?,?,?)",
                    (pid, digest(data), encode(data), now))
        # Update recency even when returning to an already observed Git state.
        con.execute("DELETE FROM git_snapshots WHERE project_id=? AND digest=?", (pid, digest(git)))
        con.execute("INSERT INTO git_snapshots(project_id,digest,data_json,created_at) VALUES(?,?,?,?)",
                    (pid, digest(git), encode(git), now))
        con.execute("PRAGMA user_version=1")
    try:
        return export(root, allow_project_archive=True)
    except (ValueError, OSError, sqlite3.Error) as error:
        raise ValueError("Database saved, but export failed; resolve the error and run authorized export") from error


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "sync", "show", "export"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--allow-project-archive", action="store_true")
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        require(root.is_dir(), "project root must exist")
        if args.command in {"sync", "export"}:
            require(args.allow_project_archive, "write command requires --allow-project-archive")
        if args.command in {"validate", "sync"}:
            require(args.input is not None, "this command requires --input")
        if args.command == "validate":
            data = validate(read_plan(args.input), root)
            result = {"valid": True, "project": data["project"]["id"], "units": len(data["units"])}
        elif args.command == "sync":
            result = sync(root, args.input, allow_project_archive=True)
        elif args.command == "export":
            result = export(root, allow_project_archive=True)
        else:
            print(show(root))
            return
        print(encode(result))
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, RecursionError) as error:
        parser.exit(1, f"Archive error: {error}\n")


if __name__ == "__main__":
    main()
