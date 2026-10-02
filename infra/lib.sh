# Sourced by every */deploy.sh: reach the node over SSH and ship source
# files as ConfigMaps, so the .py files in this repo stay the only copy of
# the code (no images to build or push).
#
#   source "$REPO_ROOT/infra/lib.sh" [node-ip]

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/weather-pipeline}"
NODE_IP="${1:-${NODE_IP:-$(terraform -chdir="$REPO_ROOT/infra" output -raw public_ip)}}"
NAMESPACE=weather-pipeline

# accept-new trusts a first-time host but refuses a changed key. Scripts pipe
# secrets over this connection; after a legitimate node rebuild clear the old
# key with `ssh-keygen -R <ip>` instead of disabling the check.
SSH=(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "ubuntu@$NODE_IP")
KUBECTL="sudo kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml"

kube() { "${SSH[@]}" "$KUBECTL $*"; }

# push_configmap NAME DIR -- the .py, .json, .sql and .gz files directly inside DIR
# become keys (.gz lands in binaryData); tests, scripts and manifests stay
# behind. Idempotent (dry-run | apply).
push_configmap() {
  local name=$1 dir=$2
  tar -C "$REPO_ROOT" --exclude=tests --exclude=__pycache__ -cf - "$dir" \
    | "${SSH[@]}" "rm -rf ~/src/$name && mkdir -p ~/src/$name && tar -C ~/src/$name -xf - \
        && $KUBECTL create configmap $name --namespace $NAMESPACE \
             \$(find ~/src/$name/$dir -maxdepth 1 -type f \( -name '*.py' -o -name '*.json' -o -name '*.gz' -o -name '*.sql' \) -printf '--from-file=%p ') \
             --dry-run=client -o yaml \
        | $KUBECTL apply -f -"
}

# apply_manifest FILE -- kubectl apply from local stdin
apply_manifest() { "${SSH[@]}" "$KUBECTL apply -f -" < "$1"; }

# ship_data -- data/*.gz (rainfall, ~1.4 MB: over the 1 MiB ConfigMap limit
# together) to /home/ubuntu/weatherops-data, mounted by pods as a hostPath.
ship_data() {
  tar -C "$REPO_ROOT/data" -cf - rain_daily.csv.gz rain_hourly.csv.gz \
    | "${SSH[@]}" "mkdir -p ~/weatherops-data && tar -C ~/weatherops-data -xf -"
}
