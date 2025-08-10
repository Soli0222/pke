# 1Password Connect

```bash
cd pke/helmfile

op connect server create PKE-kkg --vaults kubernetes
export ONEPASSWORD_TOKEN=$(op connect token create kkg --server PKE-kkg --vault kubernetes)
```