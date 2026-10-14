#!/bin/bash
# Deploy Brick Tidy to the NAS from this machine, over SSH.
#
# Updates nas-update.sh on the NAS (showing a diff and asking first), then runs
# it there to install the latest images right away instead of waiting for the
# nightly task, and shows the result.
#
# Usage:   scripts/deploy-nas.sh [--yes] [--force]
# Config:  .deploy.env in the repository root (gitignored, see .deploy.env.example),
#          or environment variables, which take precedence:
#          NAS_HOST      SSH target (user@host or an alias from ~/.ssh/config), required
#          NAS_DIR       app folder on the NAS (default: /volume1/docker/brick-tidy)
#          NAS_PASS_CMD  optional command printing the NAS password (first line used),
#                        e.g. "pass show nas"; it is piped straight to ssh and sudo
# Options: --yes     don't ask for confirmation
#          --force   restart the app even if no new image was published
#                    (e.g. after editing the compose file on the NAS)
#
# The compose file and .env belong to the deployment and are never copied;
# edit them on the NAS.

set -euo pipefail

ASSUME_YES=0
FORCE=""
for arg in "$@"; do
    case "$arg" in
        --yes|-y) ASSUME_YES=1 ;;
        --force) FORCE="--force" ;;
        -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

# Read KEY=VALUE lines from .deploy.env without executing it; environment wins
if [ -f .deploy.env ]; then
    while IFS='=' read -r key value; do
        case "$key" in
            NAS_HOST|NAS_DIR|NAS_PASS_CMD)
                value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
                [ -z "${!key:-}" ] && printf -v "$key" '%s' "$value" ;;
        esac
    done < <(grep -E '^[A-Z_]+=' .deploy.env)
fi
NAS_DIR="${NAS_DIR:-/volume1/docker/brick-tidy}"
NAS_PASS_CMD="${NAS_PASS_CMD:-}"
[ -n "${NAS_HOST:-}" ] || { echo "Set NAS_HOST in .deploy.env (see .deploy.env.example) or the environment" >&2; exit 2; }

confirm() {
    [ "$ASSUME_YES" -eq 1 ] && return 0
    read -r -p "$1 [y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]]
}

# One SSH connection for all commands, so the password is needed only once
WORK_DIR=$(mktemp -d)
SSH_OPTS=(-o ControlMaster=auto -o "ControlPath=$WORK_DIR/cm-%C" -o ControlPersist=60)
cleanup() {
    ssh "${SSH_OPTS[@]}" -O exit "$NAS_HOST" 2>/dev/null || true
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

if [ -n "$NAS_PASS_CMD" ]; then
    # ssh asks this helper for the password; it holds the command, never the password.
    # Host key questions are refused: confirm a new NAS's key once with a manual `ssh`.
    cat > "$WORK_DIR/askpass" <<EOF
#!/bin/bash
case "\$1" in
    *yes/no*|*fingerprint*) echo "Unknown host key: connect once with 'ssh $NAS_HOST' to verify it" >&2; echo no ;;
    *) $NAS_PASS_CMD | head -n1 ;;
esac
EOF
    chmod 700 "$WORK_DIR/askpass"
    export SSH_ASKPASS="$WORK_DIR/askpass" SSH_ASKPASS_REQUIRE=force
fi

nas() { ssh "${SSH_OPTS[@]}" "$NAS_HOST" "$@"; }

# Run a command with sudo on the NAS. With NAS_PASS_CMD the password is piped to
# `sudo -k -S`, which always reads it, so it can never reach the command itself.
nas_sudo() {
    if [ -n "$NAS_PASS_CMD" ]; then
        bash -c "$NAS_PASS_CMD" | head -n1 | nas "sudo -k -S -p '' $1"
    else
        ssh -t "${SSH_OPTS[@]}" "$NAS_HOST" "sudo $1"
    fi
}

if ! git diff --quiet HEAD -- scripts/nas-update.sh \
    || [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main 2>/dev/null)" ]; then
    echo "Note: deploying files that differ from origin/main (uncommitted changes or another branch)."
    confirm "Continue anyway?" || exit 1
fi

echo "Connecting to $NAS_HOST ..."
nas true || { echo "Cannot log in to $NAS_HOST (host key, password or network; see the ssh error above)" >&2; exit 1; }
nas "test -d '$NAS_DIR'" || { echo "Folder $NAS_DIR not found on $NAS_HOST" >&2; exit 1; }

# Upload a file if it changed: copy it to a temporary file as the SSH user, then
# move it into place with sudo (the app folder usually belongs to root)
upload_if_changed() {
    local local_file=$1 name=$2 mode=$3 remote_tmp
    if nas "cat '$NAS_DIR/$name' 2>/dev/null" | diff -u --label "NAS: $name" --label "local: $name" - "$local_file"; then
        echo "$name: unchanged"
        return 1
    fi
    confirm "Upload the changes to $name above?" || { echo "Aborted." >&2; exit 1; }
    remote_tmp="/tmp/deploy-nas.$$.$name"
    nas "cat > '$remote_tmp'" < "$local_file"
    nas_sudo "install -m $mode '$remote_tmp' '$NAS_DIR/$name'" && nas "rm -f '$remote_tmp'"
    echo "$name: uploaded"
}

upload_if_changed scripts/nas-update.sh nas-update.sh 755 || true

echo "Running the update on the NAS ..."
nas_sudo "'$NAS_DIR/nas-update.sh' $FORCE '$NAS_DIR'"
