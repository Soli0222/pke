#!/usr/bin/env python3
"""swap 無効化が隣接する /boot 行を変更しないことを検証する。"""

import re
from pathlib import Path

import yaml

root = Path(__file__).resolve().parents[1]
tasks = yaml.safe_load(
    (root / "ansible/roles/general-configuration/tasks/main.yaml").read_text()
)
task_names = {"Disable swap entries in fstab", "Disable configured swap files in fstab"}
selected = [task for task in tasks if task["name"] in task_names]
assert len(selected) == 2

prefix = "UUID=root / ext4 defaults 0 1\nUUID=boot /boot ext4 defaults 0 1\n"
cases = [
    ("/swap.img none swap sw 0 0\n", "# /swap.img none swap sw 0 0\n"),
    ("\t/swap.img\tnone\tswap\tsw\t0\t0\n", "# \t/swap.img\tnone\tswap\tsw\t0\t0\n"),
    ("\n/swap.img none swap sw 0 0\n", "\n# /swap.img none swap sw 0 0\n"),
    ("# comment\n/swap.img none swap sw 0 0", "# comment\n# /swap.img none swap sw 0 0"),
    ("# /swap.img none swap sw 0 0\n", "# /swap.img none swap sw 0 0\n"),
    ("/swap.img /data ext4 defaults 0 2\n", "/swap.img /data ext4 defaults 0 2\n"),
    ("/dev/vdb /data ext4 defaults 0 2 # swap\n", "/dev/vdb /data ext4 defaults 0 2 # swap\n"),
]

for task in selected:
    params = task["ansible.builtin.replace"]
    pattern = params["regexp"].replace("{{ item | regex_escape }}", re.escape("/swap.img"))
    for before, after in cases:
        result = re.sub(pattern, params["replace"], prefix + before, flags=re.MULTILINE)
        assert result == prefix + after, (task["name"], before, result)
        assert re.sub(pattern, params["replace"], result, flags=re.MULTILINE) == result
    print(f"{task['name']}: {len(cases)} preservation/idempotence cases passed")
