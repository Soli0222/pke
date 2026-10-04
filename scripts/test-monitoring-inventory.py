#!/usr/bin/env python3
"""新しい host / DB が監視側の手編集なしで追加されることを検証する。"""

import importlib.util
import tempfile
from pathlib import Path

import yaml

spec = importlib.util.spec_from_file_location(
    "inventory", Path(__file__).with_name("sync-monitoring-inventory.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)

    def write(path, value):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(value))

    write(
        "ansible/inventories/hosts.yaml",
        {
            "all": {
                "children": {
                    "k3s_cluster": {"children": {"servers": None}},
                    "servers": {"hosts": {"new-host": None}},
                }
            }
        },
    )
    write(
        "ansible/inventories/host_vars/new-host.yaml",
        {
            "cluster": "new-cluster",
            "alloy_systemd_units": ["new.service"],
            "network_netplan": {"private": {"device": "bond0"}, "global": {}},
        },
    )
    db = {
        "apiVersion": "postgresql.cnpg.io/v1",
        "kind": "Cluster",
        "metadata": {"namespace": "new-app", "name": "new-db"},
        "spec": {"instances": 2, "plugins": [{"isWALArchiver": True}]},
    }
    backup = {
        "apiVersion": "postgresql.cnpg.io/v1",
        "kind": "ScheduledBackup",
        "metadata": {"namespace": "new-app", "name": "new-backup"},
        "spec": {"cluster": {"name": "new-db"}, "schedule": "0 0 0 * * 1"},
    }
    write("flux/clusters/new-cluster/apps/new/db.yaml", db)
    write("flux/clusters/new-cluster/apps/new/backup.yaml", backup)
    actual = module.inventories(root)["new-cluster"]
    assert actual["hosts"] == [
        {"name": "new-host", "units": ["new.service"], "networkDevices": ["bond0"]}
    ]
    assert actual["databases"] == [
        {
            "namespace": "new-app",
            "name": "new-db",
            "replication": True,
            "archive": True,
            "baseBackupMaxAgeSeconds": 648000,
        }
    ]
    backup["spec"]["schedule"] = "0 30 15 * * *"
    assert module.backup_max_age(backup) == 108000
    backup["spec"]["schedule"] = "0 0 */3 * * *"
    try:
        module.backup_max_age(backup)
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported cron must not silently use a wrong deadline")
    backup["metadata"]["annotations"] = {
        "monitoring.pke.soli0222.com/backup-max-age-seconds": "14400"
    }
    assert module.backup_max_age(backup) == 14400
    backup["spec"]["suspend"] = True
    write("flux/clusters/new-cluster/apps/new/backup.yaml", backup)
    assert (
        "baseBackupMaxAgeSeconds"
        not in module.inventories(root)["new-cluster"]["databases"][0]
    )
    (root / "flux/clusters/new-cluster/apps/new/db.yaml").unlink()
    assert module.inventories(root)["new-cluster"]["databases"] == []
print(
    "Monitoring inventory: host / network / DB additions, removal, schedules and overrides OK"
)
