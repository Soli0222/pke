helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

helm install -n monitoring --create-namespace grafana grafana/grafana -f values.grafana.yaml