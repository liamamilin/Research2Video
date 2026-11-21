"""Keep all workbench execution data on the mounted trial volume."""
from pathlib import Path
import importlib.util
HERE=Path(__file__).resolve().parent

def locations():
    source=HERE.parents[2]/"scripts/trial_storage.py"
    spec=importlib.util.spec_from_file_location("workbench_trial_storage",source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return {kind:module.trial_directory(kind,"chain-workbench-20261117").resolve() for kind in ("research","autovideo")}

def initialize(root):
    roots=locations()
    pairs=[(root/name,roots["research"]/name) for name in ("state","output","logs","tmp","cache")]
    pairs.append((root/"renderer/g3/data",roots["autovideo"]/"data"))
    for local,target in pairs:
        target.mkdir(parents=True,exist_ok=True)
        local.parent.mkdir(parents=True,exist_ok=True)
        if local.is_symlink():
            if local.resolve()!=target:raise RuntimeError(f"Unexpected storage link: {local}")
        elif local.exists():
            raise RuntimeError(f"Existing local data requires an explicit stopped-service migration: {local}")
        else:local.symlink_to(target,target_is_directory=True)
    return roots
