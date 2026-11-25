"""Inspect or reverse a named chain configuration change; never restore whole databases."""

from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("list", "undo", "pause"))
    parser.add_argument("--change")
    parser.add_argument("--chain")
    args = parser.parse_args()
    from web.settings import get_settings
    from core.config_transaction import undo_change
    from core.schedule_store import ScheduleStore

    paths = get_settings().paths
    if args.action == "pause":
        if not args.chain:
            parser.error("pause requires --chain")
        from web.services import chains
        from core.video import production

        with chains.transaction():
            metadata = chains.find(args.chain)
            for output in metadata.get("outputs", []):
                production.set_auto(paths.config_dir, output, 0)
            shared = [
                other
                for other in chains.all_chains()
                if other["job"] == metadata["job"] and other["key"] != metadata["key"]
            ]
            if not shared:
                for plan in chains.schedule_store().list():
                    if plan["job_name"] == metadata["job"]:
                        chains.schedule_store().toggle(plan["id"], False)
                metadata["schedule_armed"] = False
            chains.remember(metadata)
        print(
            "Future outputs paused; shared research retained."
            if shared
            else "Future research and outputs paused; running work and history retained."
        )
        return
    if args.action == "list":
        for path in sorted((Path(paths.state_dir) / "config_changes").glob("*.json")):
            data = json.loads(path.read_text())
            print(data["id"], data["status"], len(data["files"]), "files")
        return
    if not args.change:
        parser.error("undo requires --change")
    undo_change(
        paths.state_dir,
        args.change,
        [Path(paths.config_dir), Path(paths.jobs_dir)],
        ScheduleStore(
            paths.state_dir, config_dir=paths.config_dir, jobs_dir=paths.jobs_dir
        ),
    )
    print(
        "Changed files and future plan settings restored; execution and media history retained."
    )


if __name__ == "__main__":
    main()
