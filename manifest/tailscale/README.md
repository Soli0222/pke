helm upgrade \
  --install \
  tailscale-operator \
  tailscale/tailscale-operator \
  --namespace=tailscale \
  --create-namespace \
  --set-string oauth.clientId="" \
  --set-string oauth.clientSecret="" \
  --wait


helm upgrade \
  --install \
  tailscale-operator \
  tailscale/tailscale-operator \
  --namespace=tailscale \
  --create-namespace \
  --set-string oauth.clientId="" \
  --set-string oauth.clientSecret="" \
  --set-string apiServerProxyConfig.mode="true" \
  --wait

  kubectl create clusterrolebinding tailnet-readers-view --group=tailnet-readers --clusterrole=view