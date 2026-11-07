#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Clean-install check (AF-0701): install a built wheel into a fresh distro container and
# run the README quickstart as an ordinary user, the way a stranger would.
#
#   uv build --wheel
#   scripts/clean-install-check.sh docker.io/library/ubuntu:26.04 dist/agentfence_lab-*.whl
#
# Needs Podman (rootless is fine) and network access for the distro and PyPI packages.
# The container runs as a disposable test bed; it is not an AgentFence sandbox.
set -euo pipefail

image=${1:?usage: clean-install-check.sh IMAGE WHEEL}
wheel=${2:?usage: clean-install-check.sh IMAGE WHEEL}
dist=$(cd "$(dirname "$wheel")" && pwd)
name=$(basename "$wheel")

case "$image" in
  *fedora*) prepare="dnf -q -y install python3 shadow-utils util-linux >/dev/null" ;;
  *) prepare="apt-get -qq update >/dev/null && apt-get -qq install -y python3 python3-venv >/dev/null" ;;
esac

podman run --rm --pull=never -v "$dist:/dist:ro" "$image" bash -euo pipefail -c "
$prepare
. /etc/os-release
echo \"distro: \$PRETTY_NAME | \$(python3 --version)\"
python3 -m venv /opt/agentfence
/opt/agentfence/bin/pip install -q --disable-pip-version-check '/dist/$name[ui]'
useradd -m tester
runuser -u tester -- env PATH=/opt/agentfence/bin:/usr/bin:/bin bash -euo pipefail -c '
cd ~
agentfence version
code=0; agentfence doctor >/dev/null || code=\$?
echo \"doctor: exit \$code (3 expected: no container engine in this container)\"; test \$code -eq 3
count=\$(agentfence scenarios list | wc -l); echo \"scenarios list: \$count\"; test \$count -eq 10
code=0; agentfence test --agent mock --agent-profile unsafe >/dev/null || code=\$?
echo \"test unsafe: exit \$code\"; test \$code -eq 1
agentfence test --agent mock --agent-profile safe >/dev/null && echo \"test safe: exit 0\"
test \$(agentfence runs list | wc -l) -eq 2 && echo \"runs list: 2 runs\"
run=\$(agentfence runs list --json | python3 -c \"import json,sys; print(json.load(sys.stdin)[\\\"runs\\\"][0][\\\"run_id\\\"])\")
agentfence report \$run --format html --output report.html
test \"\$(stat -c %a report.html)\" = 600 && echo \"report.html: written, mode 600\"
agentfence ui --help >/dev/null && echo \"ui: the [ui] extra is installed\"
'
echo 'clean install: OK'
"
