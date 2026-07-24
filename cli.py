#!/usr/bin/env python3
"""
AWS Cost Analyzer — CLI

No arguments:     interactive menu
With subcommand:  run directly without menu

Subcommands:
  pull      [--profile NAME ...] [--months-back N]
  analyze   [SNAPSHOT]
  report    [SNAPSHOT] [--txt]
  ri-plan
  config                — analyzer settings per profile (datadog, labels)
  profiles  [--list | --add | --remove NAME]

Examples:
  python3 cli.py
  python3 cli.py pull
  python3 cli.py pull --profile production --profile staging
  python3 cli.py analyze
  python3 cli.py analyze data/2026-07-06/production
  python3 cli.py report --txt
  python3 cli.py profiles --add
  python3 cli.py profiles --list
"""

import configparser
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

try:
    import questionary
except ImportError:
    sys.exit("questionary not installed. Run: pip install questionary")

# ── helpers ───────────────────────────────────────────────────────────────────

CREDS  = Path.home() / ".aws/credentials"
CONFIG = Path.home() / ".aws/config"


def _load_ini(path):
    c = configparser.ConfigParser()
    if path.exists():
        c.read(path)
    return c


def _save_ini(c, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        c.write(f)


def aws_profiles():
    return _load_ini(CREDS).sections()


def _is_snapshot(path):
    return (path / "billing").exists() or (path / "pull_metadata.json").exists()


def list_snapshots():
    """Return all snapshots as Path objects, newest first.

    Handles both:
      data/YYYY-MM-DD/<profile>/   ← new format
      data/YYYY-MM-DD/             ← old flat format
    """
    data = Path("data")
    if not data.exists():
        return []
    snaps = []
    for date_dir in sorted(data.iterdir(), key=lambda d: d.name, reverse=True):
        if not date_dir.is_dir():
            continue
        if _is_snapshot(date_dir):
            snaps.append(date_dir)
        else:
            for profile_dir in sorted(date_dir.iterdir(), key=lambda d: d.name):
                if profile_dir.is_dir() and _is_snapshot(profile_dir):
                    snaps.append(profile_dir)
    return snaps


def latest_snapshot():
    snaps = list_snapshots()
    return snaps[0] if snaps else None


def _snap_label(snap):
    if snap is None:
        return "none"
    try:
        return str(snap.relative_to(Path("data")))
    except ValueError:
        return snap.name


BACK = "← Back"


def _pick_snapshots():
    """Two-step picker: choose date, then one or more profiles. Returns a list of Paths."""
    data = Path("data")
    if not data.exists():
        print("  No snapshots found — pull data first.")
        return []

    date_dirs = sorted(
        [d for d in data.iterdir() if d.is_dir()],
        key=lambda d: d.name, reverse=True,
    )
    if not date_dirs:
        print("  No snapshots found — pull data first.")
        return []

    while True:
        chosen_date = questionary.select(
            "Select date",
            choices=[d.name for d in date_dirs] + [questionary.Separator(), BACK],
        ).ask()
        if not chosen_date or chosen_date == BACK:
            return []

        date_dir = data / chosen_date

        # Old flat format — no profile subdir
        if _is_snapshot(date_dir):
            return [date_dir]

        # New format: data/YYYY-MM-DD/<profile>/
        profile_dirs = sorted(
            [d for d in date_dir.iterdir() if d.is_dir() and _is_snapshot(d)],
            key=lambda d: d.name,
        )
        if not profile_dirs:
            print("  No valid snapshots under this date.")
            continue                      # back to date list
        if len(profile_dirs) == 1:
            return profile_dirs

        chosen = questionary.checkbox(
            "Select profiles  (space = toggle, enter = confirm, none = back)",
            choices=[d.name for d in profile_dirs],
            initial_choice=profile_dirs[0].name,
        ).ask()
        if not chosen:
            continue                      # back to date list
        return [date_dir / name for name in chosen]


# ── profile management ────────────────────────────────────────────────────────

def cmd_profiles_list():
    profiles = aws_profiles()
    if not profiles:
        print("  No profiles configured.")
        return
    for p in profiles:
        print(f"  • {p}")
    print(f"\n  Credentials : {CREDS}")
    print(f"  Config      : {CONFIG}")


def cmd_profiles_add(name=None):
    profiles = aws_profiles()
    if profiles:
        print(f"  Existing profiles: {', '.join(profiles)}\n")
    if not name:
        name = questionary.text("Profile name (e.g. production, staging)").ask()
    if not name:
        return

    key_id = questionary.text("AWS Access Key ID").ask()
    secret = questionary.password("AWS Secret Access Key").ask()
    region = questionary.text("Region", default="ap-southeast-1").ask()

    if not key_id or not secret or not region:
        return

    creds = _load_ini(CREDS)
    if name not in creds:
        creds[name] = {}
    creds[name]["aws_access_key_id"]     = key_id
    creds[name]["aws_secret_access_key"] = secret
    _save_ini(creds, CREDS)
    CREDS.chmod(0o600)

    cfg     = _load_ini(CONFIG)
    section = name if name == "default" else f"profile {name}"
    if section not in cfg:
        cfg[section] = {}
    cfg[section]["region"] = region
    _save_ini(cfg, CONFIG)

    print(f"\n  ✓ Profile '{name}' saved.")
    print(f"  Test: aws sts get-caller-identity --profile {name}")


def cmd_profiles_remove(name=None):
    profiles = aws_profiles()
    if not profiles:
        print("  No profiles to remove.")
        return
    if not name:
        name = questionary.select("Remove which profile?",
                                  choices=profiles + [questionary.Separator(), BACK]).ask()
        if not name or name == BACK:
            return

    creds = _load_ini(CREDS)
    cfg   = _load_ini(CONFIG)
    creds.remove_section(name)
    cfg.remove_section(f"profile {name}")
    cfg.remove_section(name)
    _save_ini(creds, CREDS)
    _save_ini(cfg, CONFIG)
    print(f"  ✓ Profile '{name}' removed.")


def menu_profiles():
    while True:
        profiles = aws_profiles()
        print(f"\n  Configured profiles: {', '.join(profiles) if profiles else 'none'}")
        action = questionary.select(
            "Profiles",
            choices=["Add profile", "Remove profile", "List profiles",
                     questionary.Separator(), BACK],
        ).ask()
        if action is None or action == BACK:
            break
        elif action == "Add profile":
            print(); cmd_profiles_add()
        elif action == "Remove profile":
            print(); cmd_profiles_remove()
        elif action == "List profiles":
            print(); cmd_profiles_list()


# ── pull ──────────────────────────────────────────────────────────────────────

def cmd_pull(profiles=None, months_back=None):
    """Pull data for one or more profiles. profiles=[] uses the default AWS profile."""
    if not profiles:
        profiles = [""]
    for profile in profiles:
        args = [sys.executable, "pull_aws_data.py"]
        if profile:
            args += ["--profile", profile]
        if months_back:
            args += ["--months-back", str(months_back)]
        print(f"\n  Pulling {'profile: ' + profile if profile else 'default profile'}...")
        subprocess.run(args)


def _multi_select_profiles(profiles):
    if not profiles:
        return []
    if len(profiles) == 1:
        print(f"  Only one profile: {profiles[0]}")
        return list(profiles)
    chosen = questionary.checkbox(
        "Select profiles to pull  (space = toggle, enter = confirm, none = back)",
        choices=profiles,
        initial_choice=profiles[0],
    ).ask()
    return chosen or []


# ── analyze ───────────────────────────────────────────────────────────────────

def cmd_analyze(snapshot=None):
    snap = Path(snapshot) if snapshot else latest_snapshot()
    if not snap:
        print("  No snapshots found. Run pull first.")
        return
    subprocess.run(["python3", "analyze.py", str(snap)])


# ── report ────────────────────────────────────────────────────────────────────

def cmd_report_txt(snapshot=None):
    snap = Path(snapshot) if snapshot else latest_snapshot()
    if not snap:
        print("  No snapshots found. Run pull first.")
        return

    label   = _snap_label(snap).replace("/", "_")
    out_txt = f"report_{label}.txt"
    print(f"  Generating text report from {_snap_label(snap)}...")
    result = subprocess.run([sys.executable, "analyze.py", str(snap)], capture_output=True, text=True)
    with open(out_txt, "w") as fout:
        fout.write(result.stdout)
    print(f"  ✓ TXT : {out_txt}")


def cmd_report(snapshot=None):
    snap = Path(snapshot) if snapshot else latest_snapshot()
    if not snap:
        print("  No snapshots found. Run pull first.")
        return

    label    = _snap_label(snap).replace("/", "_")
    out_html = f"report_{label}.html"
    print(f"  Generating report from {_snap_label(snap)}...")
    with open(out_html, "w") as fout:
        p1 = subprocess.Popen([sys.executable, "analyze.py", str(snap)], stdout=subprocess.PIPE)
        p2 = subprocess.Popen([sys.executable, "to_html.py", "--data", str(snap)],
                               stdin=p1.stdout, stdout=fout)
        p1.stdout.close()
        p2.communicate()
    print(f"  ✓ HTML: {out_html}")


# ── ri-plan ───────────────────────────────────────────────────────────────────

def cmd_ri_plan(snapshot=None):
    args = [sys.executable, "make_ri_plan.py"]
    if snapshot:
        args.append(str(snapshot))
    subprocess.run(args)


# ── analyzer settings (config.json) ──────────────────────────────────────────

CONFIG_JSON = Path("config.json")


def _load_config():
    if CONFIG_JSON.exists():
        with open(CONFIG_JSON) as f:
            return json.load(f)
    return {}


def _save_config(cfg):
    with open(CONFIG_JSON, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


def cmd_config():
    """View / edit per-profile analyzer settings in config.json."""
    cfg = _load_config()
    profiles = sorted(set(list(cfg.get("profiles", {}).keys()) + aws_profiles()))
    if not profiles:
        print("  No profiles found (config.json or ~/.aws/credentials).")
        return

    while True:
        profile = questionary.select(
            "Configure which profile?",
            choices=profiles + [questionary.Separator(), BACK],
        ).ask()
        if not profile or profile == BACK:
            return

        pcfg = cfg.setdefault("profiles", {}).setdefault(profile, {})

        while True:
            print(f"\n  Current settings for '{profile}':")
            print(f"    account_label         : {pcfg.get('account_label', '—')}")
            print(f"    tracked_rds_instances : {len(pcfg.get('tracked_rds_instances', []))} instance(s)")
            dd = pcfg.get("datadog")
            print(f"    datadog               : {'true' if dd is True else 'false' if dd is False else 'not set'}\n")

            setting = questionary.select(
                "Edit which setting?",
                choices=[
                    "datadog — is Datadog covering container metrics?",
                    "account_label",
                    questionary.Separator(),
                    BACK,
                ],
            ).ask()
            if not setting or setting == BACK:
                break                    # back to profile list

            if setting.startswith("datadog"):
                choice = questionary.select(
                    f"Does Datadog collect container metrics in '{profile}'?",
                    choices=[
                        "true   — yes, confirmed (Container Insights advice says 'already covered')",
                        "false  — no Datadog (advice says 'verify monitoring first')",
                        "unset  — don't declare (advice stays neutral)",
                        questionary.Separator(),
                        BACK,
                    ],
                ).ask()
                if not choice or choice == BACK:
                    continue             # back to settings menu
                if choice.startswith("true"):
                    pcfg["datadog"] = True
                elif choice.startswith("false"):
                    pcfg["datadog"] = False
                else:
                    pcfg.pop("datadog", None)
                _save_config(cfg)
                print(f"  ✓ Saved: datadog = {pcfg.get('datadog', 'unset')} for '{profile}'")

            elif setting == "account_label":
                label = questionary.text(
                    "Account label",
                    default=pcfg.get("account_label", ""),
                    instruction="(ctrl-c = back)",
                ).ask()
                if label is None:
                    continue             # back to settings menu
                if label:
                    pcfg["account_label"] = label
                else:
                    pcfg.pop("account_label", None)
                _save_config(cfg)
                print(f"  ✓ Saved: account_label = '{label}' for '{profile}'")


# ── main interactive menu ─────────────────────────────────────────────────────

def main_menu():
    while True:
        print(f"\n  \033[1mAWS Cost Analyzer\033[0m")

        action = questionary.select(
            "What would you like to do?",
            choices=[
                "Pull fresh data from AWS",
                "Run analysis → terminal",
                "Generate text report (.txt)",
                "Generate HTML report",
                questionary.Separator(),
                "Manage profiles",
                "Analyzer settings (config.json)",
                questionary.Separator(),
                "Exit",
            ],
        ).ask()

        if action is None or action == "Exit":
            break

        elif action == "Pull fresh data from AWS":
            profiles = aws_profiles()
            if not profiles:
                print("\n  No profiles configured — add one via Manage profiles.")
                print("  Pulling with default AWS credentials...")
                cmd_pull()
            else:
                chosen = _multi_select_profiles(profiles)
                if not chosen:
                    print("  ↩ Back — nothing selected.")
                    continue
                mb = questionary.text(
                    "Months of billing history",
                    default="3",
                    instruction="(ctrl-c = back)",
                ).ask()
                if mb is None:
                    print("  ↩ Back.")
                    continue
                months_back = mb if mb and mb.isdigit() else None
                cmd_pull(profiles=chosen, months_back=months_back)

        elif action == "Run analysis → terminal":
            for snap in _pick_snapshots():
                cmd_analyze(str(snap))

        elif action == "Generate text report (.txt)":
            for snap in _pick_snapshots():
                cmd_report_txt(str(snap))

        elif action == "Generate HTML report":
            for snap in _pick_snapshots():
                cmd_report(str(snap))

        elif action == "Manage profiles":
            menu_profiles()

        elif action == "Analyzer settings (config.json)":
            cmd_config()


# ── argument parsing ──────────────────────────────────────────────────────────

def _all_flags(args, name):
    """Collect all values for a repeatable flag, e.g. --profile a --profile b."""
    result = []
    i = 0
    while i < len(args):
        if args[i] == name and i + 1 < len(args):
            result.append(args[i + 1])
            i += 2
        else:
            i += 1
    return result


def _flag(args, name, default=None):
    if name in args:
        idx = args.index(name)
        if idx + 1 < len(args) and not args[idx + 1].startswith("--"):
            return args[idx + 1]
    return default


def _has(args, name):
    return name in args


def _positional(args):
    return next((a for a in args if not a.startswith("--")), None)


def parse_and_run():
    args = sys.argv[1:]

    if not args:
        main_menu()
        return

    cmd  = args[0]
    rest = args[1:]

    if cmd == "pull":
        profiles   = _all_flags(rest, "--profile")
        months_back = _flag(rest, "--months-back")
        cmd_pull(profiles=profiles or None, months_back=months_back)

    elif cmd == "analyze":
        cmd_analyze(_positional(rest))

    elif cmd == "report":
        if _has(rest, "--txt"):
            cmd_report_txt(_positional(rest))
        else:
            cmd_report(_positional(rest))

    elif cmd == "ri-plan":
        cmd_ri_plan()

    elif cmd == "config":
        cmd_config()

    elif cmd == "profiles":
        if _has(rest, "--list"):
            cmd_profiles_list()
        elif _has(rest, "--add"):
            print()
            cmd_profiles_add()
        elif _has(rest, "--remove"):
            cmd_profiles_remove(_flag(rest, "--remove"))
        else:
            menu_profiles()

    else:
        print(f"  Unknown command: {cmd}\n")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    parse_and_run()
